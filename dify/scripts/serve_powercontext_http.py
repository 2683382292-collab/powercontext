"""Serve the PowerContext routes used by the innkeeper Chatflow.

The DSL posts to ``/v1/scopes``, ``/v1/memory/search`` and ``/v1/memory/remember``.
This process keeps one PowerContext runtime behind those HTTP routes and records
every request, so a run can be checked without calling the runtime in-process.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft


class PowerContextHttpServer:
    def __init__(self, database: str, host: str = "127.0.0.1", port: int = 0) -> None:
        self.database = database
        self.host = host
        self.port = port
        self.access_log: list[dict[str, Any]] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._runtime = None
        self._stack = None
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not started")
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        self._thread.start()
        ready = asyncio.run_coroutine_threadsafe(self._open(), self._loop)
        ready.result(timeout=30)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0] != "/health/live":
                    self._send(404, {"error": "not found"})
                    return
                self._send(200, {"status": "live"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                path = self.path.split("?", 1)[0]
                try:
                    payload = json.loads(raw) if raw else {}
                    status, body = owner._handle(path, payload)
                except Exception as error:  # noqa: BLE001 - returned to the caller as HTTP 400
                    status, body = 400, {"error": str(error)}
                self._send(status, body, request_body=raw, path=path)

            def _send(self, status: int, body: dict, request_body: str = "", path: str | None = None) -> None:
                encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
                record = {
                    "method": self.command,
                    "path": path or self.path.split("?", 1)[0],
                    "status": status,
                    "request_body": request_body,
                    "response_body": encoded.decode("utf-8"),
                }
                owner.access_log.append(record)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self.base_url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
        asyncio.run_coroutine_threadsafe(self._close(), self._loop).result(timeout=30)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _open(self) -> None:
        stack = open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self.database}")))
        self._stack = stack
        self._runtime = await stack.__aenter__()

    async def _close(self) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(None, None, None)

    def _handle(self, path: str, payload: dict) -> tuple[int, dict]:
        call = {
            "/v1/scopes": self._create_scope,
            "/v1/memory/remember": self._remember,
            "/v1/memory/search": self._search,
        }.get(path)
        if call is None:
            return 404, {"error": "not found"}
        return asyncio.run_coroutine_threadsafe(call(payload), self._loop).result(timeout=30)

    async def _create_scope(self, payload: dict) -> tuple[int, dict]:
        scope = await self._runtime.scopes.create(
            ScopeDraft(
                title=str(payload.get("title") or "老汤的记仇酒馆"),
                summary=str(payload.get("summary") or "一位旅客的旧账"),
                idempotency_key=str(payload.get("idempotency_key") or "dify-grumpy-innkeeper"),
            )
        )
        return 200, {"scope_id": scope.scope_id}

    async def _remember(self, payload: dict) -> tuple[int, dict]:
        scope_id = str(payload["scope_id"])
        text = str(payload["text"])
        result = await self._runtime.memory.for_scope(scope_id).remember(
            RememberMemoryRequest(
                entries=(
                    MemoryEntryInput(
                        kind=str(payload.get("kind") or "innkeeper-grudge"),
                        text=text,
                        reason=payload.get("reason"),
                    ),
                )
            )
        )
        return 200, {
            "scope_id": scope_id,
            "text": text,
            "revision": None if result.memory_ref is None else result.memory_ref.revision,
        }

    async def _search(self, payload: dict) -> tuple[int, dict]:
        scope_id = str(payload["scope_id"])
        page = await self._runtime.memory.for_scope(scope_id).search(
            SearchMemoryRequest(
                query=str(payload.get("query") or ""),
                mode=str(payload.get("mode") or "fts"),
                limit=int(payload.get("limit") or 5),
            )
        )
        return 200, {
            "mode": "fts",
            "hits": [{"text": hit.text, "score": hit.score} for hit in page.hits],
        }


def main() -> None:
    import tempfile
    from pathlib import Path

    directory = Path(tempfile.mkdtemp(prefix="innkeeper-http-"))
    server = PowerContextHttpServer(str(directory / "inn.db"), port=8765)
    print(server.start(), flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
