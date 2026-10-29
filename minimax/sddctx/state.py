"""Save and reload task progress through PowerContext."""

from __future__ import annotations

import json
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

_TITLE = "SDD task state"
_SUMMARY = "Progress and verification for add-min-amount"
_KEY = "sddctx-add-min-amount"
_MARKER = "sddctx task state"


def sqlite_url(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite+aiosqlite:///" + path.resolve().as_posix()


async def save_state(path: Path, state: dict) -> dict:
    existing = await load_state(path)
    previous = 0 if existing is None else int(existing.get("sequence") or 0)
    payload = dict(state)
    payload["sequence"] = previous + 1
    text = _MARKER + "\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=sqlite_url(path)))) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(title=_TITLE, summary=_SUMMARY, idempotency_key=_KEY)
        )
        memory = runtime.memory.for_scope(scope.scope_id)
        await memory.remember(
            RememberMemoryRequest(entries=(MemoryEntryInput(kind="note", text=text),))
        )
        while (await memory.flush()).processed:
            pass
        page = await memory.search(SearchMemoryRequest(query=_MARKER, mode="fts", limit=5))
    return {
        "scope_id": scope.scope_id,
        "search_hits": len(page.hits),
        "sequence": payload["sequence"],
    }


async def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=sqlite_url(path)))) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(title=_TITLE, summary=_SUMMARY, idempotency_key=_KEY)
        )
        memory = runtime.memory.for_scope(scope.scope_id)
        while (await memory.flush()).processed:
            pass
        page = await memory.search(SearchMemoryRequest(query=_MARKER, mode="fts", limit=20))
        listed = await memory.list()
    texts = [hit.text for hit in page.hits if _MARKER in hit.text]
    if not texts:
        texts = [record.entry.text for record in listed.entries if _MARKER in record.entry.text]
    parsed = [_parse(text) for text in texts]
    parsed = [item for item in parsed if item is not None]
    if not parsed:
        return None
    chosen = max(parsed, key=lambda item: int(item.get("sequence") or 0))
    chosen["source"] = {
        "scope_id": scope.scope_id,
        "search_hits": len(page.hits),
        "entry_count": len(listed.entries),
        "sequence": chosen.get("sequence"),
    }
    return chosen


def _parse(text: str) -> dict | None:
    marker, separator, body = text.partition("\n")
    if marker != _MARKER or not separator:
        return None
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value
