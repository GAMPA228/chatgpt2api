from __future__ import annotations

import base64
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.image_tasks as image_tasks_module


AUTH_HEADERS = {"Authorization": "Bearer chatgpt2api"}
PNG_BYTES = b"\x89PNG\r\n\x1a\n"
DATA_IMAGE_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"


class FakeImageTaskService:
    def __init__(self):
        self.generation_calls = []
        self.edit_calls = []
        self.list_calls = []

    def submit_generation(self, identity, **kwargs):
        self.generation_calls.append((identity, kwargs))
        return {
            "id": kwargs["client_task_id"],
            "status": "success",
            "mode": "generate",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "data": [{"url": f"{kwargs['base_url']}/images/fake.png"}],
        }

    def submit_edit(self, identity, **kwargs):
        self.edit_calls.append((identity, kwargs))
        return {
            "id": kwargs["client_task_id"],
            "status": "queued",
            "mode": "edit",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }

    def list_tasks(self, _identity, ids, *, include_data=True, limit=100, before=None):
        self.list_calls.append({"ids": ids, "include_data": include_data, "limit": limit, "before": before})
        source_ids = ids or ["history-task"]
        items = [
            {
                "id": task_id,
                "status": "success",
                "mode": "generate",
                "created_at": "2026-01-01 00:00:00",
                "updated_at": "2026-01-01 00:00:00",
                "data": [{"url": "http://testserver/images/fake.png"}],
            }
            for task_id in source_ids
            if task_id != "missing"
        ]
        if not include_data:
            for item in items:
                item.pop("data", None)
        result = {
            "items": items,
            "missing_ids": [task_id for task_id in ids if task_id == "missing"],
        }
        if not ids and limit == 1:
            result["next_cursor"] = "cursor-1"
            result["next_before"] = "cursor-1"
        return result


class ImageTasksApiTests(unittest.TestCase):
    def setUp(self):
        self.fake_service = FakeImageTaskService()
        self.service_patcher = mock.patch.object(image_tasks_module, "image_task_service", self.fake_service)
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)
        app = FastAPI()
        app.include_router(image_tasks_module.create_router())
        self.client = TestClient(app)

    def test_create_generation_task(self):
        response = self.client.post(
            "/api/image-tasks/generations",
            headers=AUTH_HEADERS,
            json={"client_task_id": "task-1", "prompt": "cat", "model": "gpt-image-2"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], "task-1")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(self.fake_service.generation_calls), 1)

    def test_create_edit_task_accepts_multiple_images(self):
        """测试图片编辑任务接口支持多个上传图片。"""
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            data={"client_task_id": "edit-1", "prompt": "edit", "model": "gpt-image-2"},
            files=[
                ("image", ("one.png", b"one", "image/png")),
                ("image", ("two.png", b"two", "image/png")),
            ],
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], "edit-1")
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        images = self.fake_service.edit_calls[0][1]["images"]
        self.assertEqual(len(images), 2)

    def test_create_edit_task_accepts_image_url(self):
        """测试图片编辑任务接口支持表单 image_url 引用。"""
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            data={
                "client_task_id": "edit-url-1",
                "prompt": "edit",
                "model": "gpt-image-2",
                "image_url": DATA_IMAGE_URL,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        images = self.fake_service.edit_calls[0][1]["images"]
        self.assertEqual(images, [(PNG_BYTES, "image_url.png", "image/png")])

    def test_list_tasks_with_multiple_ids_omits_image_data_by_default(self):
        response = self.client.get("/api/image-tasks?ids=task-1,missing", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["items"]], ["task-1"])
        self.assertEqual(payload["missing_ids"], ["missing"])
        self.assertNotIn("data", payload["items"][0])
        self.assertEqual(self.fake_service.list_calls[-1]["include_data"], False)

    def test_list_tasks_with_one_id_keeps_legacy_result_data_default(self):
        response = self.client.get("/api/image-tasks?ids=task-1", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("data", response.json()["items"][0])
        self.assertEqual(self.fake_service.list_calls[-1]["include_data"], True)

    def test_list_tasks_without_ids_returns_owner_history(self):
        response = self.client.get("/api/image-tasks", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["items"]], ["history-task"])
        self.assertEqual(payload["missing_ids"], [])
        self.assertNotIn("data", payload["items"][0])
        self.assertEqual(self.fake_service.list_calls[-1]["include_data"], False)
        self.assertEqual(self.fake_service.list_calls[-1]["limit"], 100)

    def test_list_tasks_without_ids_allows_data_only_with_explicit_pagination(self):
        response = self.client.get(
            "/api/image-tasks?include_data=true&limit=1&cursor=cursor-0",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("data", payload["items"][0])
        self.assertEqual(payload["next_cursor"], "cursor-1")
        self.assertEqual(self.fake_service.list_calls[-1]["include_data"], True)
        self.assertEqual(self.fake_service.list_calls[-1]["limit"], 1)
        self.assertEqual(self.fake_service.list_calls[-1]["before"], "cursor-0")

    def test_list_tasks_without_ids_include_data_true_still_needs_explicit_pagination(self):
        response = self.client.get("/api/image-tasks?include_data=true", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("data", payload["items"][0])
        self.assertEqual(self.fake_service.list_calls[-1]["include_data"], False)

    def test_list_tasks_metadata_mode_omits_image_data(self):
        response = self.client.get("/api/image-tasks?ids=task-1&include_data=false", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["id"], "task-1")
        self.assertEqual(item["status"], "success")
        self.assertNotIn("data", item)


if __name__ == "__main__":
    unittest.main()
