"""PowerContext HTTP API 客户端。

这是一个薄封装：只使用 ``httpx`` 直接调用 PowerContext Server 的 HTTP 接口，
不导入 PowerContext 仓库中的任何 Python 源码，从而保证本应用与仓库解耦。

用到的接口（均为 HTTP API 契约的一部分）::

    GET  /health/live                 存活探针（始终公开）
    POST /v1/scopes                   创建/幂等返回一个 Scope
    POST /v1/memory/remember          显式写入一条 Atomic Memory（无需模型）
    POST /v1/memory/search            搜索活跃记忆
    POST /v1/memory/entries/list      分页枚举记忆条目
    POST /v1/context/prepare          为一次对话准备有界上下文（叙述式回忆）
    POST /v1/sources/content          写入一条 Source 证据
"""

from __future__ import annotations

from typing import Any

import httpx


class PowerContextError(RuntimeError):
    """PowerContext 返回非 2xx 或传输失败时抛出。"""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class PowerContextClient:
    """面向 PowerContext HTTP API 的异步客户端。

    ``transport`` 参数仅供测试注入 ``httpx.MockTransport``；生产环境保持为 ``None``。
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> PowerContextClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ #
    # 底层请求
    # ------------------------------------------------------------------ #
    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.request(method, path, json=payload)
        except httpx.HTTPError as exc:  # 传输层错误
            raise PowerContextError(f"无法连接 PowerContext: {exc}") from exc

        if not (200 <= response.status_code < 300):
            body = response.text
            raise PowerContextError(
                f"PowerContext {method} {path} 返回 HTTP {response.status_code}",
                status_code=response.status_code,
                body=body,
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", path, payload)

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    # ------------------------------------------------------------------ #
    # 健康与能力
    # ------------------------------------------------------------------ #
    async def is_alive(self) -> bool:
        """调用公开的存活探针，判断 Server 是否可达。"""

        try:
            await self._get("/health/live")
        except PowerContextError:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Scope
    # ------------------------------------------------------------------ #
    async def create_scope(self, *, title: str, summary: str, idempotency_key: str) -> str:
        """创建（或幂等返回）一个 Scope，返回其 ``scope_id``。

        ``idempotency_key`` 相同则返回同一个 Scope，因此可安全地在每次会话开始时调用。
        """

        data = await self._post(
            "/v1/scopes",
            {"title": title, "summary": summary, "idempotency_key": idempotency_key},
        )
        scope_id = data.get("scope_id")
        if not scope_id:
            raise PowerContextError("create_scope 响应缺少 scope_id", body=str(data))
        return str(scope_id)

    # ------------------------------------------------------------------ #
    # Memory —— 显式写入 / 搜索 / 枚举
    # ------------------------------------------------------------------ #
    async def remember(self, scope_id: str, kind: str, text: str, *, reason: str | None = None) -> dict[str, Any]:
        """显式写入一条记忆（``POST /v1/memory/remember``），无需模型。"""

        payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        return await self._post("/v1/memory/remember", payload)

    async def search_memory(self, scope_id: str, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """搜索活跃记忆（``POST /v1/memory/search``），返回命中列表。"""

        data = await self._post(
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": limit},
        )
        return list(data.get("hits", []))

    async def list_memory_entries(
        self,
        scope_id: str,
        *,
        include_inactive: bool = False,
        page_limit: int = 100,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """分页枚举记忆条目（``POST /v1/memory/entries/list``），聚合为单个列表。"""

        entries: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            payload: dict[str, Any] = {
                "scope_id": scope_id,
                "include_inactive": include_inactive,
                "limit": page_limit,
            }
            if cursor:
                payload["cursor"] = cursor
            data = await self._post("/v1/memory/entries/list", payload)
            entries.extend(data.get("entries", []))
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return entries

    # ------------------------------------------------------------------ #
    # Context —— 有界回忆
    # ------------------------------------------------------------------ #
    async def prepare_context(self, scope_id: str, query: str, *, max_bytes: int = 4000) -> dict[str, Any]:
        """为一次对话准备有界上下文（``POST /v1/context/prepare``）。

        返回体形如 ``{"status": "ready"|"empty", "content": str|None, ...}``。
        """

        return await self._post(
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
        )

    # ------------------------------------------------------------------ #
    # Source —— 证据写入
    # ------------------------------------------------------------------ #
    async def capture_source(
        self,
        scope_id: str,
        source_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """写入一条 Source 证据（``POST /v1/sources/content``）。"""

        payload: dict[str, Any] = {"scope_id": scope_id, "source_id": source_id, "content": content}
        if metadata is not None:
            payload["metadata"] = metadata
        return await self._post("/v1/sources/content", payload)
