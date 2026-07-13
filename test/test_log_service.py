from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.log_service import LOG_TYPE_CALL, LogService


class LogServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "logs.jsonl"
        self.service = LogService(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_records(self, records: list[dict]) -> None:
        self.path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + "\n",
            encoding="utf-8",
        )

    def test_list_reads_newest_matching_records_in_reverse(self) -> None:
        self.write_records([
            {"id": "old", "time": "2026-07-01 08:00:00", "type": "account", "detail": {}},
            {"id": "call-1", "time": "2026-07-02 08:00:00", "type": LOG_TYPE_CALL, "detail": {}},
            {"id": "call-2", "time": "2026-07-03 08:00:00", "type": LOG_TYPE_CALL, "detail": {}},
        ])

        items = self.service.list(type=LOG_TYPE_CALL, limit=1)

        self.assertEqual([item["id"] for item in items], ["call-2"])

    def test_list_omits_inline_image_data_but_keeps_normal_urls(self) -> None:
        inline_image = "data:image/png;base64," + "a" * 300_000
        self.write_records([
            {
                "id": "image-log",
                "time": "2026-07-03 08:00:00",
                "type": LOG_TYPE_CALL,
                "detail": {"urls": [inline_image, "https://example.test/image.png"]},
            }
        ])

        item = self.service.list(type=LOG_TYPE_CALL)[0]

        self.assertEqual(item["detail"]["urls"], ["https://example.test/image.png"])
        self.assertIn(inline_image, self.path.read_text(encoding="utf-8"))
    def test_list_skips_historic_inline_image_records(self) -> None:
        self.write_records([
            {
                "id": "old-image-log",
                "time": "2026-07-02 08:00:00",
                "type": LOG_TYPE_CALL,
                "detail": {"urls": ["data:image/png;base64," + "a" * 1_100_000]},
            },
            {"id": "normal-log", "time": "2026-07-03 08:00:00", "type": LOG_TYPE_CALL, "detail": {}},
        ])

        items = self.service.list(type=LOG_TYPE_CALL, limit=20)

        self.assertEqual([item["id"] for item in items], ["normal-log"])

    def test_line_match_precheck_honors_type_and_date(self) -> None:
        raw_line = '{"time":"2026-07-03 08:00:00","type":"call"}'

        self.assertTrue(self.service._line_may_match(raw_line, type=LOG_TYPE_CALL, start_date="2026-07-01", end_date="2026-07-03"))
        self.assertFalse(self.service._line_may_match(raw_line, type="account"))
        self.assertFalse(self.service._line_may_match(raw_line, start_date="2026-07-04"))


if __name__ == "__main__":
    unittest.main()
