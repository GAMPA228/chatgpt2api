from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
VALID_STATUSES = TERMINAL_STATUSES | UNFINISHED_STATUSES
DEFAULT_IMAGE_TASK_LIST_LIMIT = 100
MAX_IMAGE_TASK_LIST_LIMIT = 200
POST_MIGRATION_CLEANUP_GRACE_SECS = 86400
_MIGRATION_META_KEY = "legacy_json"
_MIGRATION_STATE_COMPLETED = "completed"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _sqlite_path(path: Path) -> Path:
    return path.with_suffix(".sqlite3")


def _normalize_list_limit(value: object) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError, OverflowError):
        limit = DEFAULT_IMAGE_TASK_LIST_LIMIT
    return min(MAX_IMAGE_TASK_LIST_LIMIT, max(1, limit))


def _encode_page_cursor(task: dict[str, Any]) -> str:
    payload = {
        "updated_at": _clean(task.get("updated_at")),
        "updated_ts": _coerce_float(task.get("updated_ts")) or 0.0,
        "task_id": _clean(task.get("id")),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_cursor(value: object) -> tuple[str, float, str] | None:
    cursor = _clean(value)
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    updated_at = _clean(payload.get("updated_at"))
    updated_ts = _coerce_float(payload.get("updated_ts"))
    task_id = _clean(payload.get("task_id") or payload.get("id"))
    if not updated_at or updated_ts is None or not task_id:
        return None
    return updated_at, updated_ts, task_id


def _file_fingerprint(path: Path) -> tuple[int, str] | None:
    try:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as file:
            while True:
                chunk = file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()
    except FileNotFoundError:
        return None


def _collect_image_urls(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _public_task(task: dict[str, Any], *, include_data: bool = True) -> dict[str, Any]:
    item = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "size": task.get("size"),
        "quality": task.get("quality"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    # Tasks are returned only to their owner. Keeping the prompt here lets a
    # browser recover its history after local IndexedDB/site data is cleared.
    if task.get("prompt"):
        item["prompt"] = task.get("prompt")
    if task.get("conversation_id"):
        item["conversation_id"] = task.get("conversation_id")
    if include_data and task.get("data") is not None:
        item["data"] = task.get("data")
    if task.get("usage") is not None:
        item["usage"] = task.get("usage")
    if task.get("error"):
        item["error"] = task.get("error")
    if task.get("progress"):
        item["progress"] = task.get("progress")
    if task.get("duration_ms") is not None:
        item["duration_ms"] = task.get("duration_ms")
    if task.get("status") in (TASK_STATUS_RUNNING, TASK_STATUS_QUEUED):
        if task.get("status") == TASK_STATUS_RUNNING:
            # RUNNING 状态仅在 started_ts 被设置后（image_stream_resolve_start）才计时
            base_ts = task.get("started_ts")
        else:
            # QUEUED 状态从 created_ts 开始计时（排队等待中）
            base_ts = task.get("created_ts") or task.get("updated_ts")
        if base_ts:
            item["elapsed_secs"] = round(time.time() - base_ts, 1)
    return item


def _default_generation_handler(body: dict[str, Any]) -> dict[str, Any]:
    from services.protocol import openai_v1_image_generations

    return openai_v1_image_generations.handle(body)


def _default_edit_handler(body: dict[str, Any]) -> dict[str, Any]:
    from services.protocol import openai_v1_image_edit

    return openai_v1_image_edit.handle(body)


def _default_retention_days() -> int:
    try:
        from services.config import config

        return int(config.image_retention_days)
    except Exception:
        return 30


def _default_data_dir() -> Path:
    try:
        from services.config import DATA_DIR

        return DATA_DIR
    except Exception:
        return Path(__file__).resolve().parents[1] / "data"


def _request_text(*values: object) -> str:
    try:
        from services.content_filter import request_text

        return request_text(*values)
    except Exception:
        return "\n".join(part for value in values if (part := _fallback_text(value).strip()))


def _fallback_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_fallback_text(item) for item in value)
    if isinstance(value, dict):
        keys = ("text", "input_text", "content", "input", "instructions", "system", "prompt")
        return "\n".join(_fallback_text(value.get(key)) for key in keys)
    return ""


def _json_text(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _coerce_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _normalize_task_item(item: dict[str, Any]) -> dict[str, Any] | None:
    task_id = _clean(item.get("id") or item.get("task_id"))
    owner = _clean(item.get("owner_id"))
    if not task_id or not owner:
        return None

    status = _clean(item.get("status"))
    if status not in VALID_STATUSES:
        status = TASK_STATUS_ERROR

    created_at = _clean(item.get("created_at"), _now_iso())
    updated_at = _clean(item.get("updated_at"), created_at)
    created_ts = _coerce_float(item.get("created_ts"))
    updated_ts = _coerce_float(item.get("updated_ts"))
    if created_ts is None:
        created_ts = _timestamp(created_at) or None
    if updated_ts is None:
        updated_ts = _timestamp(updated_at) or None

    task: dict[str, Any] = {
        "id": task_id,
        "owner_id": owner,
        "status": status,
        "mode": "edit" if item.get("mode") == "edit" else "generate",
        "model": _clean(item.get("model"), "gpt-image-2"),
        "size": _clean(item.get("size")),
        "quality": _clean(item.get("quality"), "auto"),
        "prompt": _clean(item.get("prompt")),
        "created_at": created_at,
        "updated_at": updated_at,
        "created_ts": created_ts,
        "updated_ts": updated_ts,
        "started_ts": _coerce_float(item.get("started_ts")),
        "duration_ms": _coerce_int(item.get("duration_ms")),
    }

    data = item.get("data")
    if isinstance(data, list):
        task["data"] = data
    usage = item.get("usage")
    if isinstance(usage, dict):
        task["usage"] = usage
    for field in ("error", "conversation_id", "progress"):
        value = _clean(item.get(field))
        if value:
            task[field] = value
    return task


_JSON_STREAM_CHUNK_SIZE = 1024 * 1024


class _JsonStreamDecoder:
    def __init__(self, file, chunk_size: int = _JSON_STREAM_CHUNK_SIZE):
        self._file = file
        self._chunk_size = chunk_size
        self._decoder = json.JSONDecoder()
        self._buffer = ""
        self._index = 0
        self._eof = False

    def _read_more(self) -> bool:
        if self._index:
            self._buffer = self._buffer[self._index :]
            self._index = 0
        chunk = self._file.read(self._chunk_size)
        if not chunk:
            self._eof = True
            return False
        self._buffer += chunk
        return True

    def _compact_if_needed(self) -> None:
        if self._index > self._chunk_size:
            self._buffer = self._buffer[self._index :]
            self._index = 0

    def _skip_ws(self) -> bool:
        while True:
            while self._index < len(self._buffer):
                if not self._buffer[self._index].isspace():
                    return True
                self._index += 1
            if self._eof:
                return False
            if not self._read_more():
                return False

    def peek(self) -> str:
        if not self._skip_ws():
            return ""
        return self._buffer[self._index]

    def consume(self, expected: str) -> bool:
        if self.peek() != expected:
            return False
        self._index += 1
        self._compact_if_needed()
        return True

    def decode_value(self) -> Any:
        while True:
            if not self._skip_ws():
                raise ValueError("unexpected end of JSON")
            try:
                value, end = self._decoder.raw_decode(self._buffer, self._index)
            except json.JSONDecodeError:
                if self._eof or not self._read_more():
                    raise
                continue
            self._index = end
            self._compact_if_needed()
            return value


def _iter_legacy_task_items(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as file:
            stream = _JsonStreamDecoder(file)
            first = stream.peek()
            if first == "[":
                stream.consume("[")
                yield from _iter_json_array_objects(stream)
                return
            if first != "{" or not stream.consume("{"):
                return
            yield from _iter_legacy_tasks_object(stream)
    except Exception:
        return


def _iter_legacy_tasks_object(stream: _JsonStreamDecoder) -> Iterator[dict[str, Any]]:
    while True:
        first = stream.peek()
        if not first or first == "}":
            return
        if first == ",":
            stream.consume(",")
            continue

        key = stream.decode_value()
        if not isinstance(key, str) or not stream.consume(":"):
            return
        if key == "tasks":
            if stream.consume("["):
                yield from _iter_json_array_objects(stream)
            return
        stream.decode_value()


def _iter_json_array_objects(stream: _JsonStreamDecoder) -> Iterator[dict[str, Any]]:
    while True:
        first = stream.peek()
        if not first or first == "]":
            stream.consume("]")
            return
        if first == ",":
            stream.consume(",")
            continue

        item = stream.decode_value()
        if isinstance(item, dict):
            yield item


class ImageTaskService:
    def __init__(
        self,
        path: Path,
        *,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = _default_generation_handler,
        edit_handler: Callable[[dict[str, Any]], dict[str, Any]] = _default_edit_handler,
        retention_days_getter: Callable[[], int] | None = None,
    ):
        self.path = Path(path)
        self.db_path = _sqlite_path(self.path)
        self.generation_handler = generation_handler
        self.edit_handler = edit_handler
        self.retention_days_getter = retention_days_getter or _default_retention_days
        self._lock = threading.RLock()
        self._cleanup_paused_until = 0.0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Keep committed WAL pages bounded: a large legacy migration can otherwise
        # leave a second near-source-size WAL file until every reader disconnects.
        self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
        with self._lock:
            self._initialize_db_locked()
            migrated_legacy = self._ensure_legacy_migrated_locked()
            if migrated_legacy:
                self._cleanup_paused_until = time.time() + POST_MIGRATION_CLEANUP_GRACE_SECS
            self._recover_unfinished_locked()
            if not migrated_legacy:
                self._cleanup_locked()

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "model": model,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="generate", payload=payload)

    def submit_edit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
        images: list[tuple[bytes, str, str]] | None = None,
        masks: list[tuple[bytes, str, str]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "images": images or [],
            "mask": masks or [],
            "model": model,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="edit", payload=payload)

    def list_tasks(
        self,
        identity: dict[str, object],
        task_ids: list[str],
        *,
        include_data: bool | None = None,
        limit: int = DEFAULT_IMAGE_TASK_LIST_LIMIT,
        before: str | None = None,
    ) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)]
        with self._lock:
            self._cleanup_locked()
            if requested_ids:
                effective_include_data = True if include_data is None else include_data
                tasks_by_id = self._select_tasks_by_ids_locked(owner, requested_ids, include_data=effective_include_data)
                items = []
                missing_ids = []
                for task_id in requested_ids:
                    task = tasks_by_id.get(task_id)
                    if task is None:
                        missing_ids.append(task_id)
                    else:
                        items.append(_public_task(task, include_data=effective_include_data))
                return {"items": items, "missing_ids": missing_ids}

            effective_include_data = False if include_data is None else include_data
            page_limit = _normalize_list_limit(limit)
            cursor = _decode_page_cursor(before)
            params: list[Any] = [owner]
            cursor_filter = ""
            if cursor is not None:
                updated_at, updated_ts, task_id = cursor
                cursor_filter = """
                AND (
                    updated_at < ?
                    OR (updated_at = ? AND COALESCE(updated_ts, 0) < ?)
                    OR (updated_at = ? AND COALESCE(updated_ts, 0) = ? AND task_id < ?)
                )
                """
                params.extend([updated_at, updated_at, updated_ts, updated_at, updated_ts, task_id])
            params.append(page_limit + 1)
            rows = self._conn.execute(
                f"""
                SELECT {self._select_columns(include_data=effective_include_data)}
                FROM image_tasks
                WHERE owner_id = ?
                {cursor_filter}
                ORDER BY updated_at DESC, COALESCE(updated_ts, 0) DESC, task_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            has_next = len(rows) > page_limit
            page_rows = rows[:page_limit]
            tasks = [self._row_to_task(row) for row in page_rows]
            items = [_public_task(task, include_data=effective_include_data) for task in tasks]
            result: dict[str, Any] = {"items": items, "missing_ids": []}
            if has_next and tasks:
                next_cursor = _encode_page_cursor(tasks[-1])
                result["next_cursor"] = next_cursor
                result["next_before"] = next_cursor
            return result

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.close()
            except sqlite3.ProgrammingError:
                # Tests and shutdown hooks can race to close the same service.
                pass

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = _clean(client_task_id)
        if not task_id:
            raise ValueError("client_task_id is required")
        owner = _owner_id(identity)
        now = _now_iso()
        created_ts = time.time()
        task = {
            "id": task_id,
            "owner_id": owner,
            "status": TASK_STATUS_QUEUED,
            "mode": mode,
            "model": _clean(payload.get("model"), "gpt-image-2"),
            "size": _clean(payload.get("size")),
            "quality": _clean(payload.get("quality"), "auto"),
            "prompt": _clean(payload.get("prompt")),
            "created_at": now,
            "updated_at": now,
            "created_ts": created_ts,
            "updated_ts": created_ts,
        }
        should_start = False
        with self._lock:
            self._cleanup_locked()
            inserted = self._insert_task_locked(task, replace=False)
            if inserted:
                should_start = True
            else:
                existing = self._get_task_locked(owner, task_id, include_data=True)
                if existing is not None:
                    return _public_task(existing)

        if should_start:
            thread = threading.Thread(
                target=self._run_task,
                args=(owner, task_id, mode, payload, dict(identity), _clean(payload.get("model"), "gpt-image-2")),
                name=f"image-task-{task_id[:16]}",
                daemon=True,
            )
            thread.start()
        return _public_task(task)

    def _run_task(
        self,
        owner_id: str,
        task_id: str,
        mode: str,
        payload: dict[str, Any],
        identity: dict[str, object],
        model: str,
    ) -> None:
        started = time.time()
        self._update_task(owner_id, task_id, status=TASK_STATUS_RUNNING, error="")

        # 创建进度回调，每个步骤完成后更新任务状态
        def progress_callback(step: str) -> None:
            if step == "image_stream_resolve_start":
                self._update_task(owner_id, task_id, started_ts=time.time())
            self._update_task(owner_id, task_id, progress=step)

        # 将进度回调添加到 payload 中（handler 会提取并传递给 ConversationRequest）
        payload_with_progress = {**payload, "progress_callback": progress_callback}
        try:
            handler = self.edit_handler if mode == "edit" else self.generation_handler
            result = handler(payload_with_progress)
            if not isinstance(result, dict):
                raise RuntimeError("image task returned streaming result unexpectedly")
            data = result.get("data")
            account_email = _clean(result.get("_account_email") or result.get("account_email"))
            if not isinstance(data, list) or not data:
                upstream = _clean(result.get("message"))
                if upstream:
                    message = upstream
                else:
                    message = "号池中没有可用账号或所有账号均被限流，请检查号池状态（账号额度、是否被封禁、是否到达生图上限）"
                error = RuntimeError(message)
                if account_email:
                    setattr(error, "account_email", account_email)
                raise error
            usage = result.get("usage")
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(owner_id, task_id, status=TASK_STATUS_SUCCESS, data=data, usage=usage, error="", duration_ms=duration_ms)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成",
                request_preview=_request_text(payload.get("prompt")),
                urls=_collect_image_urls(data),
                account_email=account_email,
            )
        except Exception as exc:
            error_message = str(exc) or "image task failed"
            account_email = _clean(getattr(exc, "account_email", ""))
            conversation_id = _clean(getattr(exc, "conversation_id", ""))
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(
                owner_id,
                task_id,
                status=TASK_STATUS_ERROR,
                error=error_message,
                data=[],
                duration_ms=duration_ms,
                **({"conversation_id": conversation_id} if conversation_id else {}),
            )
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败",
                request_preview=_request_text(payload.get("prompt")),
                status="failed",
                error=error_message,
                account_email=account_email,
            )

    def _log_call(
        self,
        identity: dict[str, object],
        mode: str,
        model: str,
        started: float,
        suffix: str,
        *,
        request_preview: str = "",
        status: str = "success",
        error: str = "",
        urls: list[str] | None = None,
        account_email: str = "",
    ) -> None:
        endpoint = "/v1/images/edits" if mode == "edit" else "/v1/images/generations"
        summary_prefix = "图生图" if mode == "edit" else "文生图"
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": endpoint,
            "model": model,
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if request_preview:
            detail["request_text"] = request_preview
        if error:
            detail["error"] = error
        if account_email:
            detail["account_email"] = account_email
        if urls:
            safe_urls = [
                url for url in urls
                if not url.startswith("data:") and len(url) <= 4096
            ]
            if safe_urls:
                detail["urls"] = list(dict.fromkeys(safe_urls))
        try:
            from services.log_service import LOG_TYPE_CALL, log_service

            log_service.add(LOG_TYPE_CALL, f"{summary_prefix}{suffix}", detail)
        except Exception:
            pass

    def _update_task(self, owner_id: str, task_id: str, **updates: Any) -> None:
        with self._lock:
            self._update_task_locked(owner_id, task_id, **updates)

    def _initialize_db_locked(self) -> None:
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        # A clean service restart has no active readers; trim any completed
        # migration WAL before accepting traffic so it cannot retain ~1x source.
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS image_tasks (
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL,
                    size TEXT NOT NULL DEFAULT '',
                    quality TEXT NOT NULL DEFAULT 'auto',
                    prompt TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_ts REAL,
                    updated_ts REAL,
                    started_ts REAL,
                    duration_ms INTEGER,
                    conversation_id TEXT,
                    data_json TEXT,
                    usage_json TEXT,
                    error TEXT,
                    progress TEXT,
                    PRIMARY KEY (owner_id, task_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_image_tasks_owner_updated
                ON image_tasks(owner_id, updated_at DESC, updated_ts DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_image_tasks_owner_updated_cursor
                ON image_tasks(owner_id, updated_at DESC, updated_ts DESC, task_id DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_image_tasks_status_updated_ts
                ON image_tasks(status, updated_ts)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_meta (
                    key TEXT PRIMARY KEY,
                    source_size INTEGER NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    migration_state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _ensure_legacy_migrated_locked(self) -> bool:
        # Once a legacy import has committed, SQLite is authoritative. The old
        # JSON remains only as an immutable rollback source; its later mutation
        # must never erase tasks created after the cutover.
        if self._legacy_migration_has_completed_locked():
            return False
        fingerprint = _file_fingerprint(self.path)
        if fingerprint is None:
            return False
        source_size, source_sha256 = fingerprint
        self._migrate_legacy_json_locked(source_size=source_size, source_sha256=source_sha256)
        return True

    def _legacy_migration_has_completed_locked(self) -> bool:
        row = self._conn.execute(
            "SELECT migration_state FROM migration_meta WHERE key = ?",
            (_MIGRATION_META_KEY,),
        ).fetchone()
        return row is not None and row["migration_state"] == _MIGRATION_STATE_COMPLETED

    def _legacy_migration_completed_locked(self, source_size: int, source_sha256: str) -> bool:
        row = self._conn.execute(
            """
            SELECT source_size, source_sha256, migration_state
            FROM migration_meta
            WHERE key = ?
            """,
            (_MIGRATION_META_KEY,),
        ).fetchone()
        if row is None:
            return False
        return (
            row["migration_state"] == _MIGRATION_STATE_COMPLETED
            and int(row["source_size"]) == source_size
            and row["source_sha256"] == source_sha256
        )

    def _migrate_legacy_json_locked(self, *, source_size: int, source_sha256: str) -> int:
        migrated = 0
        with self._conn:
            self._conn.execute("DELETE FROM image_tasks")
            for item in _iter_legacy_task_items(self.path):
                task = _normalize_task_item(item)
                if task is None:
                    continue
                self._insert_task_row_locked(task, replace=True)
                migrated += 1
            self._conn.execute(
                """
                INSERT OR REPLACE INTO migration_meta (
                    key,
                    source_size,
                    source_sha256,
                    migration_state,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _MIGRATION_META_KEY,
                    source_size,
                    source_sha256,
                    _MIGRATION_STATE_COMPLETED,
                    _now_iso(),
                ),
            )
        # The migration's single transaction can write a WAL nearly as large as
        # the legacy source. Checkpoint it before serving requests so the
        # temporary duplicate does not consume disk indefinitely.
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return migrated

    @staticmethod
    def _select_columns(*, include_data: bool) -> str:
        columns = [
            "task_id AS id",
            "owner_id",
            "status",
            "mode",
            "model",
            "size",
            "quality",
            "prompt",
            "created_at",
            "updated_at",
            "created_ts",
            "updated_ts",
            "started_ts",
            "duration_ms",
            "conversation_id",
            "usage_json",
            "error",
            "progress",
        ]
        if include_data:
            columns.append("data_json")
        return ", ".join(columns)

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        task = {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "size": row["size"],
            "quality": row["quality"],
            "prompt": row["prompt"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "created_ts": row["created_ts"],
            "updated_ts": row["updated_ts"],
            "started_ts": row["started_ts"],
            "duration_ms": row["duration_ms"],
        }
        if row["conversation_id"]:
            task["conversation_id"] = row["conversation_id"]
        if row["usage_json"] is not None:
            try:
                usage = json.loads(row["usage_json"])
            except Exception:
                usage = None
            if isinstance(usage, dict):
                task["usage"] = usage
        if "data_json" in keys and row["data_json"] is not None:
            try:
                data = json.loads(row["data_json"])
            except Exception:
                data = None
            if isinstance(data, list):
                task["data"] = data
        if row["error"]:
            task["error"] = row["error"]
        if row["progress"]:
            task["progress"] = row["progress"]
        return task

    def _get_task_locked(self, owner_id: str, task_id: str, *, include_data: bool = True) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"""
            SELECT {self._select_columns(include_data=include_data)}
            FROM image_tasks
            WHERE owner_id = ? AND task_id = ?
            """,
            (owner_id, task_id),
        ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def _select_tasks_by_ids_locked(
        self,
        owner_id: str,
        task_ids: list[str],
        *,
        include_data: bool = True,
    ) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        unique_ids = list(dict.fromkeys(task_ids))
        for start in range(0, len(unique_ids), 900):
            chunk = unique_ids[start:start + 900]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"""
                SELECT {self._select_columns(include_data=include_data)}
                FROM image_tasks
                WHERE owner_id = ? AND task_id IN ({placeholders})
                """,
                [owner_id, *chunk],
            ).fetchall()
            for row in rows:
                task = self._row_to_task(row)
                found[str(task["id"])] = task
        return found

    def _insert_task_locked(self, task: dict[str, Any], *, replace: bool = False) -> bool:
        with self._conn:
            cursor = self._insert_task_row_locked(task, replace=replace)
        return bool(cursor.rowcount)

    def _insert_task_row_locked(self, task: dict[str, Any], *, replace: bool) -> sqlite3.Cursor:
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        return self._conn.execute(
            f"""
            {verb} INTO image_tasks (
                owner_id,
                task_id,
                status,
                mode,
                model,
                size,
                quality,
                prompt,
                created_at,
                updated_at,
                created_ts,
                updated_ts,
                started_ts,
                duration_ms,
                conversation_id,
                data_json,
                usage_json,
                error,
                progress
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["owner_id"],
                task["id"],
                task["status"],
                task["mode"],
                task["model"],
                task.get("size") or "",
                task.get("quality") or "auto",
                task.get("prompt") or "",
                task["created_at"],
                task["updated_at"],
                task.get("created_ts"),
                task.get("updated_ts"),
                task.get("started_ts"),
                task.get("duration_ms"),
                task.get("conversation_id"),
                _json_text(task.get("data")) if "data" in task else None,
                _json_text(task.get("usage")) if "usage" in task else None,
                task.get("error"),
                task.get("progress"),
            ),
        )

    def _update_task_locked(self, owner_id: str, task_id: str, **updates: Any) -> None:
        values: dict[str, Any] = {}
        column_map = {
            "status": "status",
            "mode": "mode",
            "model": "model",
            "size": "size",
            "quality": "quality",
            "prompt": "prompt",
            "created_at": "created_at",
            "created_ts": "created_ts",
            "started_ts": "started_ts",
            "duration_ms": "duration_ms",
            "conversation_id": "conversation_id",
            "error": "error",
            "progress": "progress",
        }
        for key, value in updates.items():
            if key == "data":
                values["data_json"] = _json_text(value)
            elif key == "usage":
                values["usage_json"] = _json_text(value)
            elif key in column_map:
                values[column_map[key]] = value
        values["updated_at"] = _now_iso()
        values["updated_ts"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._conn:
            self._conn.execute(
                f"""
                UPDATE image_tasks
                SET {assignments}
                WHERE owner_id = ? AND task_id = ?
                """,
                [*values.values(), owner_id, task_id],
            )

    def _recover_unfinished_locked(self) -> bool:
        now = _now_iso()
        now_ts = time.time()
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE image_tasks
                SET status = ?, error = ?, updated_at = ?, updated_ts = ?
                WHERE status IN (?, ?)
                """,
                (
                    TASK_STATUS_ERROR,
                    "服务已重启，未完成的图片任务已中断",
                    now,
                    now_ts,
                    TASK_STATUS_QUEUED,
                    TASK_STATUS_RUNNING,
                ),
            )
        return bool(cursor.rowcount)

    def _cleanup_locked(self) -> bool:
        now_ts = time.time()
        if now_ts < self._cleanup_paused_until:
            return False
        try:
            retention_days = max(1, int(self.retention_days_getter()))
        except Exception:
            retention_days = 30
        cutoff = now_ts - retention_days * 86400
        with self._conn:
            cursor = self._conn.execute(
                """
                DELETE FROM image_tasks
                WHERE status IN (?, ?) AND COALESCE(updated_ts, 0) < ?
                """,
                (TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, cutoff),
            )
        return bool(cursor.rowcount)

    def resume_poll(
        self,
        identity: dict[str, object],
        task_id: str,
        extra_timeout_secs: float = 30.0,
    ) -> dict[str, Any]:
        """恢复对已超时任务的轮询，额外等待 extra_timeout_secs 秒。"""
        owner = _owner_id(identity)
        clean_task_id = _clean(task_id)
        with self._lock:
            task = self._get_task_locked(owner, clean_task_id, include_data=True)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") != TASK_STATUS_ERROR:
                raise ValueError("task is not in error state")
            error_msg = _clean(task.get("error"))
            if "超时" not in error_msg:
                raise ValueError("task error is not a timeout error")
            conversation_id = _clean(task.get("conversation_id"))
            if not conversation_id:
                raise ValueError("task has no conversation_id")
            mode = task.get("mode", "generate")
            model = task.get("model", "gpt-image-2")
            # 将任务状态重置为 running
            self._update_task_locked(owner, clean_task_id, status=TASK_STATUS_RUNNING, error="")
            task = self._get_task_locked(owner, clean_task_id, include_data=True) or task

        # 启动新线程继续轮询
        thread = threading.Thread(
            target=self._run_resume_poll,
            args=(owner, clean_task_id, conversation_id, extra_timeout_secs, dict(identity), mode, model),
            name=f"image-resume-{clean_task_id[:16]}",
            daemon=True,
        )
        thread.start()
        return _public_task(task)

    def _run_resume_poll(
        self,
        owner_id: str,
        task_id: str,
        conversation_id: str,
        extra_timeout_secs: float,
        identity: dict[str, object],
        mode: str,
        model: str,
    ) -> None:
        """后台线程：继续轮询已有 conversation_id 的图片结果。"""
        started = time.time()
        try:
            import base64

            from services.openai_backend_api import OpenAIBackendAPI
            from services.protocol.conversation import format_image_result

            try:
                from services.config import config

                proxy_url = config.proxy_url or None
            except Exception:
                proxy_url = None
            backend = OpenAIBackendAPI(proxy_url=proxy_url)
            file_ids, sediment_ids = backend._poll_image_results(
                conversation_id,
                extra_timeout_secs,
            )
            if not file_ids and not sediment_ids:
                raise RuntimeError(
                    f"继续等待 {extra_timeout_secs} 秒后仍未找到图片结果。"
                )

            image_urls = backend.resolve_conversation_image_urls(
                conversation_id, file_ids, sediment_ids, poll=False,
            )
            if not image_urls:
                raise RuntimeError("图片 URL 解析失败")

            image_items = [
                {"b64_json": base64.b64encode(image_data).decode("ascii")}
                for image_data in backend.download_image_bytes(image_urls)
            ]
            # 获取 task 的原始 prompt（从 _public_task 的 mode 判断）
            with self._lock:
                task = self._get_task_locked(owner_id, task_id, include_data=False)
                quality = _clean(task.get("quality"), "auto") if task else "auto"
                size = _clean(task.get("size")) if task else None
            data = format_image_result(
                image_items,
                "",  # prompt 已不重要，结果已经拿到了
                "b64_json",
                "",
                int(time.time()),
            )["data"]
            self._update_task(
                owner_id,
                task_id,
                status=TASK_STATUS_SUCCESS,
                data=data,
                error="",
                duration_ms=int((time.time() - started) * 1000),
            )
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成（续轮询）",
                status="success",
                urls=_collect_image_urls(data),
            )
        except Exception as exc:
            error_message = str(exc) or "resume poll failed"
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(owner_id, task_id, status=TASK_STATUS_ERROR, error=error_message, data=[], duration_ms=duration_ms)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败（续轮询）",
                status="failed",
                error=error_message,
            )


image_task_service = ImageTaskService(_default_data_dir() / "image_tasks.json")
