from __future__ import annotations

from typing import Any, Iterator

from curl_cffi import requests

from services.config import config
from services.protocol.conversation import (
    ConversationRequest,
    collect_image_outputs,
    count_text_tokens,
    stream_image_chunks,
    stream_image_outputs_with_pool,
)
from utils.image_tokens import count_image_output_items_tokens, image_usage


def _third_party_image_generation(body: dict[str, Any]) -> dict[str, Any]:
    settings = config.get_third_party_image_api_settings()
    if not bool(settings.get("enabled")):
        raise RuntimeError("third-party image api is disabled")
    base_url = str(settings.get("base_url") or "").strip().rstrip("/")
    api_key = str(settings.get("api_key") or "").strip()
    if not base_url:
        raise RuntimeError("third-party image api base_url is required")
    if not api_key:
        raise RuntimeError("third-party image api api_key is required")

    payload = {
        "prompt": str(body.get("prompt") or ""),
        "model": str(body.get("model") or "gpt-image-2"),
        "n": int(body.get("n") or 1),
        "size": body.get("size"),
        "quality": str(body.get("quality") or "auto"),
        "response_format": str(body.get("response_format") or "url"),
    }
    response = requests.post(
        f"{base_url}/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=300,
        verify=False,
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:800]
        raise RuntimeError(f"third-party image generation failed: HTTP {response.status_code}, {detail}")
    result = response.json()
    # Some upstreams ignore response_format=url and return b64_json. Persist it
    # before the task reaches the browser: task polling then carries a compact
    # URL and the result image can load normally instead of blocking on a multi-
    # megabyte JSON/IndexedDB update.
    data = result.get("data")
    if isinstance(data, list):
        from services.protocol.conversation import format_image_result

        if any(isinstance(item, dict) and item.get("b64_json") for item in data):
            return format_image_result(
                data,
                str(body.get("prompt") or ""),
                "url",
                base_url=str(body.get("base_url") or "") or None,
                created=result.get("created") if isinstance(result.get("created"), int) else None,
                message=str(result.get("message") or ""),
            )
    return result


def _use_third_party_image_api() -> bool:
    settings = config.get_third_party_image_api_settings()
    return bool(settings.get("enabled"))


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    model = str(body.get("model") or "gpt-image-2")
    n = int(body.get("n") or 1)
    size = body.get("size")
    quality = str(body.get("quality") or "auto")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None
    progress_callback = body.get("progress_callback")

    if _use_third_party_image_api():
        result = _third_party_image_generation(body)
        result["usage"] = image_usage(
            input_text_tokens=count_text_tokens(prompt, model),
            output_tokens=count_image_output_items_tokens(result.get("data"), size, quality),
        )
        return result

    outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        size=size,
        quality=quality,
        response_format=response_format,
        base_url=base_url,
        message_as_error=True,
        progress_callback=progress_callback,
    ))
    if body.get("stream"):
        return stream_image_chunks(outputs)
    result = collect_image_outputs(outputs)
    result["usage"] = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        output_tokens=count_image_output_items_tokens(result.get("data"), size, quality),
    )
    return result
