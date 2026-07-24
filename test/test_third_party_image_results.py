from __future__ import annotations

import unittest
from unittest import mock

from services.protocol import openai_v1_image_generations


class ThirdPartyImageResultTests(unittest.TestCase):
    def test_generation_converts_upstream_base64_to_persisted_url(self) -> None:
        upstream = {
            "created": 123,
            "data": [{"b64_json": "aGVsbG8=", "revised_prompt": "cat"}],
        }
        settings = {
            "enabled": True,
            "base_url": "https://image.example/v1",
            "api_key": "test-key",
        }
        with (
            mock.patch.object(openai_v1_image_generations.config, "get_third_party_image_api_settings", return_value=settings),
            mock.patch.object(openai_v1_image_generations.requests, "post") as post,
            mock.patch("services.protocol.conversation.save_image_bytes", return_value="https://app.example/images/result.png") as save,
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = upstream

            result = openai_v1_image_generations.handle({"prompt": "cat", "model": "gpt-image-2", "n": 1, "base_url": "https://app.example"})

        self.assertEqual(result["data"], [{"url": "https://app.example/images/result.png", "revised_prompt": "cat"}])
        save.assert_called_once_with(b"hello", "https://app.example")


if __name__ == "__main__":
    unittest.main()
