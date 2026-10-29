# ruff: noqa: S101
"""测试夹具：内存版假 PowerContext Server。

用 ``httpx.MockTransport`` 模拟 PowerContext 的 HTTP 接口，使测试无需真实 Server
即可覆盖「写入记忆 → 读取历史 → 靶向出题」的完整闭环。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from app.config import Settings
from app.memory_repository import MemoryRepository
from app.powercontext_client import PowerContextClient
from app.review_service import ReviewService


class FakePowerContext:
    """实现本应用用到的 PowerContext 端点的最小子集。"""

    def __init__(self) -> None:
        self.scopes: dict[str, str] = {}          # idempotency_key -> scope_id
        self.memories: dict[str, list[dict[str, Any]]] = {}  # scope_id -> records
        self._scope_seq = 0
        self._mem_seq = 0

    # ---- 端点实现 ---- #
    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        method = request.method.upper()
        body = _json_body(request)

        if method == "GET" and path == "/health/live":
            return _json(200, {"status": "ok"})

        if method == "POST" and path == "/v1/scopes":
            return self._create_scope(body)
        if method == "POST" and path == "/v1/memory/remember":
            return self._remember(body)
        if method == "POST" and path == "/v1/memory/entries/list":
            return self._list_entries(body)
        if method == "POST" and path == "/v1/memory/search":
            return self._search(body)
        if method == "POST" and path == "/v1/context/prepare":
            return self._prepare(body)
        if method == "POST" and path == "/v1/sources/content":
            return _json(202, {"status": "accepted", "source": {"name": "content", "source_id": body.get("source_id")}, "position": 1})

        return _json(404, {"error": {"code": "not_found", "message": f"未模拟的端点 {method} {path}"}})

    def _create_scope(self, body: dict[str, Any]) -> httpx.Response:
        key = body.get("idempotency_key")
        if key in self.scopes:
            scope_id = self.scopes[key]
        else:
            self._scope_seq += 1
            scope_id = f"pc-scope-{self._scope_seq}"
            self.scopes[key] = scope_id
            self.memories.setdefault(scope_id, [])
        return _json(201, {
            "scope_id": scope_id,
            "title": body.get("title", ""),
            "summary": body.get("summary", ""),
            "parent_scope_id": None,
            "context_references": [],
            "external_references": [],
            "version": 1,
        })

    def _remember(self, body: dict[str, Any]) -> httpx.Response:
        scope_id = body["scope_id"]
        self.memories.setdefault(scope_id, [])
        self._mem_seq += 1
        record = {
            "artifact": {"family": "atomic-memory", "artifact_id": f"am-{self._mem_seq}", "revision": 1},
            "kind": body["kind"],
            "text": body["text"],
            "state": "active",
            "state_version": 1,
            "merged_into_id": None,
        }
        self.memories[scope_id].append(record)
        return _json(200, {"changed": True, "records": [record]})

    def _list_entries(self, body: dict[str, Any]) -> httpx.Response:
        scope_id = body["scope_id"]
        entries = self.memories.get(scope_id, [])
        limit = int(body.get("limit", 100))
        return _json(200, {"entries": entries[:limit], "next_cursor": None})

    def _search(self, body: dict[str, Any]) -> httpx.Response:
        scope_id = body["scope_id"]
        query = (body.get("query") or "").lower()
        hits = []
        for record in self.memories.get(scope_id, []):
            if query and query in record["text"].lower():
                hits.append({"memory": record, "score": 1.0, "matched_by": ["fts"]})
        return _json(200, {"mode": "text", "hits": hits})

    def _prepare(self, body: dict[str, Any]) -> httpx.Response:
        scope_id = body["scope_id"]
        entries = self.memories.get(scope_id, [])
        if not entries:
            return _json(200, {"schema": "powercontext.prepared-context.v1", "status": "empty", "content": None, "content_bytes": 0})
        lines = [f"- [{r['kind']}] {r['text'][:80]}" for r in entries[-5:]]
        content = "历史记忆回顾：\n" + "\n".join(lines)
        return _json(200, {
            "schema": "powercontext.prepared-context.v1",
            "status": "ready",
            "content": content,
            "content_bytes": len(content.encode("utf-8")),
        })

    # ---- 供测试注入 ---- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def scope_for(self, idempotency_key: str) -> str | None:
        return self.scopes.get(idempotency_key)

    def records(self, scope_id: str) -> list[dict[str, Any]]:
        return self.memories.get(scope_id, [])


def _json_body(request: httpx.Request) -> dict[str, Any]:
    if not request.content:
        return {}
    try:
        data = json.loads(request.content.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _json(status: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=payload, headers={"Content-Type": "application/json"})


def make_settings(**overrides: Any) -> Settings:
    base = {
        "powercontext_url": "http://testserver",
        "powercontext_token": None,
        "scope_prefix": "qoder-test",
        "host": "127.0.0.1",
        "port": 8080,
        "session_size": 5,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def fake() -> FakePowerContext:
    return FakePowerContext()


@pytest.fixture
def pc_client(fake: FakePowerContext):
    client = PowerContextClient("http://testserver", transport=fake.transport())
    yield client
    import asyncio

    asyncio.run(client.aclose())


@pytest.fixture
def repo(pc_client: PowerContextClient) -> MemoryRepository:
    return MemoryRepository(pc_client, "qoder-test")


@pytest.fixture
def service(repo: MemoryRepository) -> ReviewService:
    return ReviewService(repo, default_size=5)
