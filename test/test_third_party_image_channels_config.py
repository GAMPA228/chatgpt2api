import json
import tempfile
import unittest
from pathlib import Path

from services.config import ConfigStore, _normalize_third_party_apps_settings


class ThirdPartyImageChannelsConfigTests(unittest.TestCase):
    def _make_store(self, initial: dict[str, object]) -> tuple[tempfile.TemporaryDirectory[str], ConfigStore]:
        tmp_dir = tempfile.TemporaryDirectory()
        path = Path(tmp_dir.name) / "config.json"
        path.write_text(json.dumps({"auth-key": "test-auth", **initial}), encoding="utf-8")
        return tmp_dir, ConfigStore(path)

    def test_legacy_single_channel_is_migrated_and_selected(self) -> None:
        apps = _normalize_third_party_apps_settings(
            {
                "image_api": {
                    "enabled": True,
                    "base_url": " https://legacy.example/v1/ ",
                    "api_key": " legacy-key ",
                }
            }
        )

        image_api = apps["image_api"]
        self.assertTrue(image_api["enabled"])
        self.assertEqual(image_api["active_channel_id"], "legacy")
        self.assertEqual(
            image_api["channels"],
            [{"id": "legacy", "name": "默认渠道", "base_url": "https://legacy.example/v1", "api_key": "legacy-key"}],
        )

    def test_active_channel_is_the_only_channel_used_by_runtime(self) -> None:
        tmp_dir, store = self._make_store(
            {
                "third_party_apps": {
                    "image_api": {
                        "enabled": True,
                        "active_channel_id": "second",
                        "channels": [
                            {"id": "first", "name": "主渠道", "base_url": "https://first.example/v1", "api_key": "first-key"},
                            {"id": "second", "name": "备用渠道", "base_url": "https://second.example/v1", "api_key": "second-key"},
                        ],
                    }
                }
            }
        )
        with tmp_dir:
            active = store.get_third_party_image_api_settings()
            self.assertTrue(active["enabled"])
            self.assertEqual(active["id"], "second")
            self.assertEqual(active["base_url"], "https://second.example/v1")
            self.assertEqual(active["api_key"], "second-key")

    def test_invalid_or_duplicate_channel_ids_are_normalized_and_active_falls_back(self) -> None:
        apps = _normalize_third_party_apps_settings(
            {
                "image_api": {
                    "enabled": True,
                    "active_channel_id": "missing",
                    "channels": [
                        {"id": "same", "name": " A ", "base_url": " https://a.example/ ", "api_key": " a "},
                        {"id": "same", "name": "", "base_url": " https://b.example/ ", "api_key": " b "},
                    ],
                }
            }
        )
        image_api = apps["image_api"]
        self.assertEqual(image_api["active_channel_id"], "same")
        self.assertEqual([channel["id"] for channel in image_api["channels"]], ["same", "channel-2"])
        self.assertEqual(image_api["channels"][1]["name"], "渠道 2")


if __name__ == "__main__":
    unittest.main()
