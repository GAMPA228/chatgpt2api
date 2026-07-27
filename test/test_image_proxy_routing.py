from __future__ import annotations

import unittest
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_v1_image_edit, openai_v1_image_generations


class ImageProxyRoutingTests(unittest.TestCase):
    def test_channel_one_backend_receives_configured_upstream_proxy(self) -> None:
        with mock.patch("services.openai_backend_api.proxy_settings.build_session_kwargs", return_value={"proxy": "http://proxy.example:7890"}) as build:
            OpenAIBackendAPI(access_token="test-token")

        self.assertEqual(build.call_args.kwargs, {"account": mock.ANY, "impersonate": mock.ANY, "verify": True, "upstream": True})

    def test_third_party_generation_uses_configured_upstream_proxy(self) -> None:
        settings = {"enabled": True, "base_url": "https://image.example/v1", "api_key": "test-key"}
        with (
            mock.patch.object(openai_v1_image_generations.config, "get_third_party_image_api_settings", return_value=settings),
            mock.patch.object(openai_v1_image_generations, "image_request_proxy_kwargs", return_value={"proxy": "http://proxy.example:7890"}),
            mock.patch.object(openai_v1_image_generations.requests, "post") as post,
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"data": [{"url": "https://image.example/result.png"}]}

            openai_v1_image_generations.handle({"prompt": "cat", "model": "gpt-image-2"})

        self.assertEqual(post.call_args.kwargs["proxy"], "http://proxy.example:7890")

    def test_third_party_edit_uses_configured_upstream_proxy(self) -> None:
        settings = {"enabled": True, "base_url": "https://image.example/v1", "api_key": "test-key"}
        with (
            mock.patch.object(openai_v1_image_edit.config, "get_third_party_image_api_settings", return_value=settings),
            mock.patch.object(openai_v1_image_edit, "image_request_proxy_kwargs", return_value={"proxy": "http://proxy.example:7890"}),
            mock.patch.object(openai_v1_image_edit.requests, "post") as post,
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"data": [{"url": "https://image.example/result.png"}]}

            openai_v1_image_edit.handle({
                "prompt": "cat",
                "model": "gpt-image-2",
                "images": [(b"image", "input.png", "image/png")],
            })

        self.assertEqual(post.call_args.kwargs["proxy"], "http://proxy.example:7890")


if __name__ == "__main__":
    unittest.main()
