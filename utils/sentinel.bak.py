"""OpenAI Sentinel Token (PoW) 生成与请求工具函数。

用于密码登录、注册等需要 sentinel token 的流程。
兼容旧接口：(sentinel_value, oai_sc) 解包仍可用；
新接口可读取 so_token / raw 等扩展字段。
"""
from __future__ import annotations

import base64
import json
import random
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from curl_cffi.requests import Session


class SentinelRequirements(dict):
    """兼容旧 tuple 解包的 requirements 结果。"""

    def __iter__(self):
        yield str(self.get("openai_sentinel_token") or "")
        yield str(self.get("oai_sc") or "")


class SentinelTokenGenerator:
    """Sentinel Token 生成器（PoW - Proof of Work）。"""
    MAX_ATTEMPTS = 500_000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id: str, ua: str):
        self.device_id = device_id
        self.user_agent = ua
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    def _get_config(self) -> list:
        perf_now = random.uniform(1000, 50000)
        return [
            "1920x1080",
            time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()),
            4294705152,
            random.random(),
            self.user_agent,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None,
            None,
            "en-US",
            random.random(),
            random.choice(["vendorSub-undefined", "plugins-undefined", "mimeTypes-undefined", "hardwareConcurrency-undefined"]),
            random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
            random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
            perf_now,
            self.sid,
            "",
            random.choice([4, 8, 12, 16]),
            time.time() * 1000 - perf_now,
        ]

    @staticmethod
    def _b64(data) -> str:
        return base64.b64encode(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("ascii")

    def generate_requirements_token(self) -> str:
        data = self._get_config()
        data[3] = 1
        data[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(data)

    def generate_token(self, seed: str, difficulty: str) -> str:
        start = time.time()
        data = self._get_config()
        difficulty = str(difficulty or "0")
        for i in range(self.MAX_ATTEMPTS):
            data[3] = i
            data[9] = round((time.time() - start) * 1000)
            payload = self._b64(data)
            if self._fnv1a_32(seed + payload)[: len(difficulty)] <= difficulty:
                return "gAAAAAB" + payload + "~S"
        return "gAAAAAB" + self.ERROR_PREFIX + self._b64(str(None))


DEFAULT_SENTINEL_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
DEFAULT_SENTINEL_SEC_CH_UA = '"Chromium";v="145", "Google Chrome";v="145", "Not/A)Brand";v="99"'
DEFAULT_SENTINEL_SDK_VERSION = "20260124ceb8"
DEFAULT_SO_OBSERVER_WAIT_MS = 5000


def _extract_so_token(data: dict[str, Any]) -> str:
    candidates = [
        data.get("so_token"),
        data.get("so"),
        (data.get("requirements") or {}).get("so_token") if isinstance(data.get("requirements"), dict) else None,
        (data.get("requirements") or {}).get("so") if isinstance(data.get("requirements"), dict) else None,
        (data.get("observer") or {}).get("so_token") if isinstance(data.get("observer"), dict) else None,
        (data.get("observer") or {}).get("so") if isinstance(data.get("observer"), dict) else None,
        (data.get("result") or {}).get("so_token") if isinstance(data.get("result"), dict) else None,
        (data.get("result") or {}).get("so") if isinstance(data.get("result"), dict) else None,
    ]
    for item in candidates:
        value = str(item or "").strip()
        if value:
            return value
    return ""


def _build_init_payload(generator: SentinelTokenGenerator, flow: str) -> dict[str, Any]:
    return {
        "type": "init",
        "flow": flow,
        "requestId": f"req_{uuid.uuid4().hex}",
        "p": generator.generate_requirements_token(),
    }


def _build_token_payload(generator: SentinelTokenGenerator, flow: str, p_value: str) -> dict[str, Any]:
    return {
        "type": "token",
        "flow": flow,
        "requestId": f"req_{uuid.uuid4().hex}",
        "p": p_value,
    }


def _post_sentinel_message(
    session: "Session",
    payload: dict[str, Any],
    *,
    user_agent: str,
    sec_ch_ua: str,
) -> dict[str, Any]:
    """用官方 SDK 所见的 frame/message 语义去请求 Sentinel。

    这里仍通过 HTTP 调 sentinel req，但把 payload 结构对齐到 SDK 的 init/token 双阶段：
    - init 阶段：先建立 flow 状态
    - token 阶段：再请求最终 token 包
    """
    resp = session.post(
        "https://sentinel.openai.com/backend-api/sentinel/req",
        data=json.dumps(payload, separators=(",", ":")),
        headers={
            "Content-Type": "text/plain;charset=UTF-8",
            "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
            "Origin": "https://sentinel.openai.com",
            "User-Agent": user_agent,
            "sec-ch-ua": sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
        timeout=20,
        verify=False,
    )
    try:
        data = resp.json() if getattr(resp, "text", "") else {}
    except Exception:
        data = {}
    if resp.status_code != 200:
        raise RuntimeError(f"sentinel_req_failed_{resp.status_code}")
    if not isinstance(data, dict):
        raise RuntimeError("sentinel_req_invalid_json")
    return data


def build_sentinel_token(
    session: "Session",
    device_id: str,
    flow: str,
    *,
    user_agent: str = "",
    sec_ch_ua: str = "",
) -> SentinelRequirements:
    """请求 sentinel token，并返回兼容旧 tuple 解包的 requirements 对象。"""
    ua = user_agent or DEFAULT_SENTINEL_USER_AGENT
    ch_ua = sec_ch_ua or DEFAULT_SENTINEL_SEC_CH_UA
    generator = SentinelTokenGenerator(device_id, ua)

    # Phase 1: init
    init_payload = _build_init_payload(generator, flow)
    init_data = _post_sentinel_message(session, init_payload, user_agent=ua, sec_ch_ua=ch_ua)

    token = str(init_data.get("token") or "").strip()
    if not token:
        fallback = json.dumps(
            {"p": generator.generate_requirements_token(), "t": "", "c": "", "id": device_id, "flow": flow},
            separators=(",", ":"),
        )
        return SentinelRequirements({
            "openai_sentinel_token": fallback,
            "oai_sc": "",
            "so_token": "",
            "raw": init_data,
            "sdk_version": DEFAULT_SENTINEL_SDK_VERSION,
            "observer_wait_ms": DEFAULT_SO_OBSERVER_WAIT_MS,
        })

    pow_data = init_data.get("proofofwork") or {}
    p_value = (
        generator.generate_token(str(pow_data.get("seed") or ""), str(pow_data.get("difficulty") or "0"))
        if pow_data.get("required") and pow_data.get("seed")
        else generator.generate_requirements_token()
    )

    # Phase 2: token
    token_payload = _build_token_payload(generator, flow, p_value)
    token_data = _post_sentinel_message(session, token_payload, user_agent=ua, sec_ch_ua=ch_ua)

    # observer wait window
    time.sleep(DEFAULT_SO_OBSERVER_WAIT_MS / 1000.0)

    final_token = str(token_data.get("token") or token or "").strip()
    sentinel_value = json.dumps({"p": p_value, "t": None, "c": final_token, "flow": flow}, separators=(",", ":"))
    oai_sc_value = "0" + final_token if final_token else ""
    so_token = _extract_so_token(token_data) or _extract_so_token(init_data)

    return SentinelRequirements({
        "openai_sentinel_token": sentinel_value,
        "oai_sc": oai_sc_value,
        "so_token": so_token,
        "raw": {"init": init_data, "token": token_data},
        "sdk_version": DEFAULT_SENTINEL_SDK_VERSION,
        "observer_wait_ms": DEFAULT_SO_OBSERVER_WAIT_MS,
    })
