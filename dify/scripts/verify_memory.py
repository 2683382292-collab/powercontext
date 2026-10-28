"""Two-turn check that the innkeeper's ledger survives in PowerContext.

Uses the HTTP API when POWERCONTEXT_URL is set. Otherwise drives the local
builtin runtime, which is the same remember/search behavior the Dify HTTP
nodes call through /v1/memory/remember and /v1/memory/search.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


TURN_ONE = "旧账：旅客把一只青蛙放进了老汤的汤里"
TURN_TWO = "旧账：旅客又点了一碗汤，并说这次付钱"


def _post(base: str, path: str, payload: dict, token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} HTTP {error.code}: {detail}") from error


def _texts(page: dict) -> list[str]:
    return [str(hit.get("text") or "") for hit in page.get("hits") or []]


def run_http(base: str, token: str | None) -> dict:
    scope = _post(
        base,
        "/v1/scopes",
        {
            "title": "老汤的记仇酒馆",
            "summary": "一位旅客的旧账",
            "idempotency_key": "dify-grumpy-innkeeper-sample",
        },
        token,
    )
    scope_id = scope["scope_id"]
    _post(
        base,
        "/v1/memory/remember",
        {
            "scope_id": scope_id,
            "kind": "innkeeper-grudge",
            "text": TURN_ONE,
            "reason": "traveler turn",
        },
        token,
    )
    first = _post(
        base,
        "/v1/memory/search",
        {"scope_id": scope_id, "query": "旧账", "limit": 5, "mode": "fts"},
        token,
    )
    _post(
        base,
        "/v1/memory/remember",
        {
            "scope_id": scope_id,
            "kind": "innkeeper-grudge",
            "text": TURN_TWO,
            "reason": "traveler turn",
        },
        token,
    )
    second = _post(
        base,
        "/v1/memory/search",
        {"scope_id": scope_id, "query": "旧账", "limit": 5, "mode": "fts"},
        token,
    )
    return {
        "transport": "http",
        "base_url": base,
        "scope_id": scope_id,
        "turn_one_hits": _texts(first),
        "turn_two_hits": _texts(second),
    }


async def run_builtin() -> dict:
    from powercontext.builtin.artifacts.memory import MemoryEntryInput
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import (
        BuiltinConfig,
        RememberMemoryRequest,
        SearchMemoryRequest,
        open_builtin_runtime,
    )
    from powercontext.builtin.scope import ScopeDraft

    directory = Path(tempfile.mkdtemp(prefix="innkeeper-"))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'inn.db'}"))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(
                title="老汤的记仇酒馆",
                summary="一位旅客的旧账",
                idempotency_key="dify-grumpy-innkeeper-sample",
            )
        )
        memory = runtime.memory.for_scope(scope.scope_id)
        await memory.remember(
            RememberMemoryRequest(
                entries=(MemoryEntryInput(kind="innkeeper-grudge", text=TURN_ONE, reason="traveler turn"),)
            )
        )
        first = await memory.search(SearchMemoryRequest(query="旧账", mode="fts", limit=5))
        await memory.remember(
            RememberMemoryRequest(
                entries=(MemoryEntryInput(kind="innkeeper-grudge", text=TURN_TWO, reason="traveler turn"),)
            )
        )
        second = await memory.search(SearchMemoryRequest(query="旧账", mode="fts", limit=5))
    return {
        "transport": "builtin-runtime",
        "database": str(directory / "inn.db"),
        "scope_id": scope.scope_id,
        "turn_one_hits": [hit.text for hit in first.hits],
        "turn_two_hits": [hit.text for hit in second.hits],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    base = os.environ.get("POWERCONTEXT_URL", "").strip()
    token = os.environ.get("POWERCONTEXT_TOKEN", "").strip() or None
    report = run_http(base, token) if base else asyncio.run(run_builtin())
    first = "\n".join(report["turn_one_hits"])
    second = "\n".join(report["turn_two_hits"])
    if "青蛙" not in first:
        raise SystemExit("turn 1 did not recall the frog")
    if "青蛙" not in second or "付钱" not in second:
        raise SystemExit("turn 2 did not keep both ledger lines")
    report["ok"] = True
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
