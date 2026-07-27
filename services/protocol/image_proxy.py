from __future__ import annotations

from typing import Any

from services.proxy_service import proxy_settings


def image_request_proxy_kwargs() -> dict[str, Any]:
    """Resolve only the admin-configured upstream proxy for image API calls.

    Image adapters already deliberately set their own TLS verification policy.
    Forwarding the whole session config could duplicate ``verify`` in a request,
    so this boundary carries only routing and remains direct when unset.
    """
    profile = proxy_settings.get_profile(upstream=True)
    return {"proxy": profile.proxy_url} if profile.proxy_url else {}
