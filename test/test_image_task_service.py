from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import services.image_task_service as image_task_service_module
from services.image_task_service import ImageTaskService


OWNER = {"id": "owner-1", "name": "Owner", "role": "admin"}
OTHER_OWNER = {"id": "owner-2", "name": "Other", "role": "user"}


def wait_for_task(service: ImageTaskService, identity: dict[str, object], task_id: str, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        result = service.list_tasks(identity, [task_id])
        last = (result.get("items") or [None])[0]
        if last and last.get("status") == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}, last={last}")


class ImageTaskServiceTests(unittest.TestCase):
    def make_service(self, path: Path, handler=None, retention_days: int = 30) -> ImageTaskService:
        service = ImageTaskService(
            path,
            generation_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/image.png"}]}),
            edit_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]}),
            retention_days_getter=lambda: retention_days,
        )
        service._log_call = lambda *args, **kwargs: None
        self.addCleanup(service.close)
        return service

    def test_duplicate_submit_uses_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                time.sleep(0.05)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            first = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            second = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            self.assertEqual(first["id"], "task-1")
            self.assertEqual(second["id"], "task-1")
            task = wait_for_task(service, OWNER, "task-1", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/image.png")
            self.assertEqual(task["prompt"], "cat")
            self.assertEqual(calls, 1)

    def test_metadata_list_omits_image_data(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="metadata-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "metadata-task", "success")

            result = service.list_tasks(OWNER, ["metadata-task"], include_data=False)

            self.assertEqual(result["items"][0]["status"], "success")
            self.assertNotIn("data", result["items"][0])

    def test_list_without_ids_defaults_to_metadata_page(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="history-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "history-task", "success")

            result = service.list_tasks(OWNER, [])

            self.assertEqual([item["id"] for item in result["items"]], ["history-task"])
            self.assertNotIn("data", result["items"][0])
            self.assertEqual(result["missing_ids"], [])
            self.assertNotIn("next_cursor", result)

    def test_list_without_ids_uses_limit_and_stable_cursor(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "task-a",
                                "owner_id": "owner-1",
                                "status": "success",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "quality": "auto",
                                "prompt": "a",
                                "created_at": "2099-07-01 10:00:00",
                                "updated_at": "2099-07-03 10:00:00",
                                "created_ts": 1,
                                "updated_ts": 29,
                                "data": [{"url": "http://example.test/a.png"}],
                            },
                            {
                                "id": "task-b",
                                "owner_id": "owner-1",
                                "status": "success",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "quality": "auto",
                                "prompt": "b",
                                "created_at": "2099-07-01 10:00:00",
                                "updated_at": "2099-07-03 10:00:00",
                                "created_ts": 1,
                                "updated_ts": 30,
                                "data": [{"url": "http://example.test/b.png"}],
                            },
                            {
                                "id": "task-c",
                                "owner_id": "owner-1",
                                "status": "success",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "quality": "auto",
                                "prompt": "c",
                                "created_at": "2099-07-01 10:00:00",
                                "updated_at": "2099-07-03 10:00:00",
                                "created_ts": 1,
                                "updated_ts": 30,
                                "data": [{"url": "http://example.test/c.png"}],
                            },
                            {
                                "id": "task-z",
                                "owner_id": "owner-1",
                                "status": "success",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "quality": "auto",
                                "prompt": "z",
                                "created_at": "2099-07-01 10:00:00",
                                "updated_at": "2099-07-02 10:00:00",
                                "created_ts": 1,
                                "updated_ts": 99,
                                "data": [{"url": "http://example.test/z.png"}],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            first_page = service.list_tasks(OWNER, [], limit=2)
            second_page = service.list_tasks(OWNER, [], limit=2, before=first_page["next_cursor"])

            self.assertEqual([item["id"] for item in first_page["items"]], ["task-c", "task-b"])
            self.assertEqual([item["id"] for item in second_page["items"]], ["task-a", "task-z"])
            self.assertIn("next_cursor", first_page)
            self.assertNotIn("next_cursor", second_page)
            self.assertNotIn("data", first_page["items"][0])

    def test_different_owner_cannot_query_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="private-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            wait_for_task(service, OWNER, "private-task", "success")
            result = service.list_tasks(OTHER_OWNER, ["private-task"])

            self.assertEqual(result["items"], [])
            self.assertEqual(result["missing_ids"], ["private-task"])

    def test_success_task_persists_to_new_service_instance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            service.submit_generation(
                OWNER,
                client_task_id="persisted-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "persisted-task", "success")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persisted-task"])

            self.assertTrue(path.with_suffix(".sqlite3").exists())
            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(result["items"][0]["status"], "success")
            self.assertEqual(result["items"][0]["prompt"], "cat")
            self.assertEqual(result["items"][0]["data"][0]["url"], "http://example.test/image.png")

    def test_legacy_json_migrates_to_sqlite_without_modifying_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            legacy_text = json.dumps(
                {
                    "tasks": [
                        {
                            "id": "shared-task",
                            "owner_id": "owner-1",
                            "status": "success",
                            "mode": "generate",
                            "model": "gpt-image-2",
                            "quality": "auto",
                            "prompt": "owner one prompt",
                            "created_at": "2099-07-01 10:00:00",
                            "updated_at": "2099-07-01 10:01:00",
                            "data": [{"url": "http://example.test/owner-one.png"}],
                            "usage": {"total_tokens": 12},
                        },
                        {
                            "id": "shared-task",
                            "owner_id": "owner-2",
                            "status": "success",
                            "mode": "generate",
                            "model": "gpt-image-2",
                            "quality": "auto",
                            "prompt": "owner two prompt",
                            "created_at": "2099-07-01 11:00:00",
                            "updated_at": "2099-07-01 11:01:00",
                            "data": [{"url": "http://example.test/owner-two.png"}],
                        },
                        {
                            "id": "older-task",
                            "owner_id": "owner-1",
                            "status": "success",
                            "mode": "generate",
                            "model": "gpt-image-2",
                            "quality": "auto",
                            "prompt": "older prompt",
                            "created_at": "2099-06-30 10:00:00",
                            "updated_at": "2099-06-30 10:01:00",
                            "data": [{"url": "http://example.test/older.png"}],
                        },
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            path.write_text(legacy_text, encoding="utf-8")

            service = self.make_service(path)

            self.assertTrue(path.with_suffix(".sqlite3").exists())
            self.assertEqual(path.read_text(encoding="utf-8"), legacy_text)
            owner_result = service.list_tasks(OWNER, ["shared-task"])
            other_result = service.list_tasks(OTHER_OWNER, ["shared-task"])

            self.assertEqual(owner_result["missing_ids"], [])
            self.assertEqual(owner_result["items"][0]["prompt"], "owner one prompt")
            self.assertEqual(owner_result["items"][0]["data"][0]["url"], "http://example.test/owner-one.png")
            self.assertEqual(owner_result["items"][0]["usage"], {"total_tokens": 12})
            self.assertEqual(other_result["missing_ids"], [])
            self.assertEqual(other_result["items"][0]["prompt"], "owner two prompt")
            self.assertEqual(other_result["items"][0]["data"][0]["url"], "http://example.test/owner-two.png")
            owner_list = service.list_tasks(OWNER, [], include_data=False)
            self.assertEqual([item["id"] for item in owner_list["items"]], ["shared-task", "older-task"])
            self.assertNotIn("data", owner_list["items"][0])

    def test_legacy_migration_does_not_immediately_cleanup_old_history(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "old-history-task",
                                "owner_id": "owner-1",
                                "status": "success",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "quality": "auto",
                                "prompt": "old history",
                                "created_at": "2000-01-01 00:00:00",
                                "updated_at": "2000-01-01 00:01:00",
                                "created_ts": 946684800,
                                "updated_ts": 946684860,
                                "data": [{"url": "http://example.test/old.png"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path, retention_days=1)
            result = service.list_tasks(OWNER, [])

            self.assertEqual([item["id"] for item in result["items"]], ["old-history-task"])
            self.assertNotIn("data", result["items"][0])

    def test_success_log_omits_inline_base64_urls(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ImageTaskService(
                Path(tmp_dir) / "image_tasks.json",
                generation_handler=lambda _payload: {"data": []},
                edit_handler=lambda _payload: {"data": []},
                retention_days_getter=lambda: 30,
            )
            self.addCleanup(service.close)
            captured: list[tuple[str, str, dict]] = []
            with mock.patch("services.log_service.log_service.add", side_effect=lambda kind, summary, detail: captured.append((kind, summary, detail))):
                service._log_call(
                    {"id": "key-1", "name": "test"}, "generate", "gpt-image-2", time.time(), "调用完成",
                    urls=["data:image/png;base64," + "a" * 100, "https://example.test/image.png"],
                )
            self.assertEqual(captured[0][2]["urls"], ["https://example.test/image.png"])

    def test_existing_empty_sqlite_without_completed_meta_migrates_legacy_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            db_path = path.with_suffix(".sqlite3")
            sqlite3.connect(db_path).close()
            legacy_text = json.dumps(
                {
                    "tasks": [
                        {
                            "id": "empty-db-task",
                            "owner_id": "owner-1",
                            "status": "success",
                            "mode": "generate",
                            "model": "gpt-image-2",
                            "quality": "auto",
                            "prompt": "from legacy",
                            "created_at": "2099-07-04 10:00:00",
                            "updated_at": "2099-07-04 10:01:00",
                            "data": [{"url": "http://example.test/empty-db.png"}],
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            path.write_text(legacy_text, encoding="utf-8")

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["empty-db-task"])

            self.assertEqual(path.read_text(encoding="utf-8"), legacy_text)
            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(result["items"][0]["data"][0]["url"], "http://example.test/empty-db.png")
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT migration_state FROM migration_meta WHERE key = ?",
                    ("legacy_json",),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "completed")

    def test_partial_sqlite_without_completed_meta_is_rebuilt_from_legacy_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            seed_service = ImageTaskService(
                path,
                generation_handler=lambda _payload: {"data": [{"url": "http://example.test/image.png"}]},
                edit_handler=lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]},
                retention_days_getter=lambda: 30,
            )
            seed_service._log_call = lambda *args, **kwargs: None
            seed_service._insert_task_locked(
                {
                    "id": "stale-task",
                    "owner_id": "owner-1",
                    "status": "success",
                    "mode": "generate",
                    "model": "gpt-image-2",
                    "size": "",
                    "quality": "auto",
                    "prompt": "stale",
                    "created_at": "2099-07-03 10:00:00",
                    "updated_at": "2099-07-03 10:01:00",
                    "created_ts": 1,
                    "updated_ts": 2,
                    "data": [{"url": "http://example.test/stale.png"}],
                },
                replace=True,
            )
            with seed_service._conn:
                seed_service._conn.execute(
                    """
                    INSERT OR REPLACE INTO migration_meta (
                        key,
                        source_size,
                        source_sha256,
                        migration_state,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("legacy_json", 0, "bad-hash", "started", "2099-07-03 10:02:00"),
                )
            seed_service.close()
            legacy_text = json.dumps(
                {
                    "tasks": [
                        {
                            "id": "rebuilt-task",
                            "owner_id": "owner-1",
                            "status": "success",
                            "mode": "generate",
                            "model": "gpt-image-2",
                            "quality": "auto",
                            "prompt": "rebuilt",
                            "created_at": "2099-07-04 10:00:00",
                            "updated_at": "2099-07-04 10:01:00",
                            "data": [{"url": "http://example.test/rebuilt.png"}],
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            path.write_text(legacy_text, encoding="utf-8")

            service = self.make_service(path)
            rebuilt = service.list_tasks(OWNER, ["rebuilt-task"])
            stale = service.list_tasks(OWNER, ["stale-task"])

            self.assertEqual(path.read_text(encoding="utf-8"), legacy_text)
            self.assertEqual(rebuilt["missing_ids"], [])
            self.assertEqual(rebuilt["items"][0]["data"][0]["url"], "http://example.test/rebuilt.png")
            self.assertEqual(stale["items"], [])
            self.assertEqual(stale["missing_ids"], ["stale-task"])

    def test_completed_migration_does_not_replace_new_sqlite_tasks_when_legacy_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            initial_legacy = json.dumps({"tasks": [{
                "id": "legacy-task", "owner_id": "owner-1", "status": "success",
                "mode": "generate", "model": "gpt-image-2", "quality": "auto",
                "prompt": "legacy", "created_at": "2099-07-04 10:00:00",
                "updated_at": "2099-07-04 10:01:00",
            }]})
            path.write_text(initial_legacy, encoding="utf-8")
            first = self.make_service(path)
            first.close()

            reopened = ImageTaskService(
                path,
                generation_handler=lambda _payload: {"data": [{"url": "http://example.test/image.png"}]},
                edit_handler=lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]},
                retention_days_getter=lambda: 30,
            )
            reopened._log_call = lambda *args, **kwargs: None
            reopened._insert_task_locked({
                "id": "sqlite-new-task", "owner_id": "owner-1", "status": "success",
                "mode": "generate", "model": "gpt-image-2", "size": "", "quality": "auto",
                "prompt": "sqlite wins", "created_at": "2099-07-05 10:00:00",
                "updated_at": "2099-07-05 10:01:00", "created_ts": time.time(), "updated_ts": time.time(),
            }, replace=True)
            reopened.close()
            with sqlite3.connect(path.with_suffix(".sqlite3")) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM image_tasks WHERE task_id = ?", ("sqlite-new-task",)).fetchone()[0], 1)
            path.write_text(initial_legacy + "\n", encoding="utf-8")

            service = self.make_service(path)
            self.assertEqual(service.list_tasks(OWNER, ["sqlite-new-task"])["missing_ids"], [])

    def test_legacy_json_migration_handles_task_items_crossing_chunks_without_modifying_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            first_prompt = "first prompt " + ("A" * 180)
            second_prompt = "second prompt " + ("B" * 180)
            first_data = "data:image/png;base64," + ("a" * 240)
            second_data = "data:image/png;base64," + ("b" * 240)
            legacy_bytes = json.dumps(
                {
                    "tasks": [
                        {
                            "id": "chunk-task-one",
                            "owner_id": "owner-1",
                            "status": "success",
                            "mode": "generate",
                            "model": "gpt-image-2",
                            "quality": "auto",
                            "prompt": first_prompt,
                            "created_at": "2099-07-02 10:00:00",
                            "updated_at": "2099-07-02 10:01:00",
                            "data": [{"b64_json": first_data}],
                        },
                        {
                            "id": "chunk-task-two",
                            "owner_id": "owner-1",
                            "status": "success",
                            "mode": "generate",
                            "model": "gpt-image-2",
                            "quality": "auto",
                            "prompt": second_prompt,
                            "created_at": "2099-07-02 11:00:00",
                            "updated_at": "2099-07-02 11:01:00",
                            "data": [{"b64_json": second_data}],
                        },
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            path.write_bytes(legacy_bytes)
            original_decoder = image_task_service_module._JsonStreamDecoder

            class SmallChunkJsonStreamDecoder(original_decoder):
                def __init__(self, file, chunk_size=64):
                    super().__init__(file, chunk_size=chunk_size)

            with mock.patch.object(image_task_service_module, "_JSON_STREAM_CHUNK_SIZE", 64), mock.patch.object(
                image_task_service_module,
                "_JsonStreamDecoder",
                SmallChunkJsonStreamDecoder,
            ):
                service = self.make_service(path)

            self.assertTrue(path.with_suffix(".sqlite3").exists())
            self.assertEqual(path.read_bytes(), legacy_bytes)

            result = service.list_tasks(OWNER, ["chunk-task-one", "chunk-task-two"])
            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(len(result["items"]), 2)
            items_by_id = {item["id"]: item for item in result["items"]}
            self.assertEqual(items_by_id["chunk-task-one"]["prompt"], first_prompt)
            self.assertEqual(items_by_id["chunk-task-one"]["data"][0]["b64_json"], first_data)
            self.assertEqual(items_by_id["chunk-task-two"]["prompt"], second_prompt)
            self.assertEqual(items_by_id["chunk-task-two"]["data"][0]["b64_json"], second_data)

    def test_startup_marks_unfinished_tasks_as_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "queued-task",
                                "owner_id": "owner-1",
                                "status": "queued",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                            {
                                "id": "running-task",
                                "owner_id": "owner-1",
                                "status": "running",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["queued-task", "running-task"])

            self.assertEqual([item["status"] for item in result["items"]], ["error", "error"])
            self.assertTrue(all("已中断" in item.get("error", "") for item in result["items"]))


if __name__ == "__main__":
    unittest.main()
