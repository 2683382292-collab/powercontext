"""Build a task context from seekdb hits and saved task state."""

from __future__ import annotations

import json
from pathlib import Path

from sddctx.materials import Material, MaterialStore
from sddctx.paths import REQUIRED, ROOT, TASK

_QUERY_TERMS = (
    "SPEC-AMT-001",
    "SPEC-CAT-001",
    "SPEC-COMPAT-001",
    "filter_expenses",
    "test_min_amount",
    "test_category",
    "min_amount",
    "category",
)


def task_query(task_path: Path = TASK) -> str:
    text = task_path.read_text(encoding="utf-8")
    present = [term for term in _QUERY_TERMS if term in text]
    if len(present) < len(_QUERY_TERMS):
        missing = [term for term in _QUERY_TERMS if term not in text]
        raise ValueError(f"task file no longer names {missing}")
    return " ".join(present)


def build_context(store: MaterialStore, state: dict | None, task_path: Path = TASK) -> dict:
    query = task_query(task_path)
    hits = store.search(query)
    if not hits:
        raise RuntimeError("seekdb returned no materials for the task query")
    selected = _select(hits)
    _require_sources(selected)
    completed, pending = _progress(state)
    return {
        "task_id": "add-min-amount",
        "task_path": str(task_path.relative_to(ROOT)),
        "query": query,
        "change": "实现 min_amount 闭区间筛选，并允许与 category 同时生效。",
        "preserve": "只按 category 筛选、省略 category、空列表这三项行为保持不变。",
        "specs": [_item(hit) for hit in selected if hit.kind == "spec"],
        "code": [_item(hit) for hit in selected if hit.kind == "code"],
        "tests": [_item(hit) for hit in selected if hit.kind == "test"],
        "completed": completed,
        "pending": pending,
        "verification": None if state is None else state.get("verification"),
        "state_source": None if state is None else state.get("source"),
    }


def render_context(context: dict) -> str:
    return json.dumps(context, ensure_ascii=False, indent=2)


def _select(hits: list[Material]) -> list[Material]:
    chosen: list[Material] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.doc_id in seen:
            continue
        if not (ROOT / hit.source_path).is_file():
            continue
        seen.add(hit.doc_id)
        chosen.append(hit)
    return chosen


def _require_sources(hits: list[Material]) -> None:
    found = {(hit.kind, hit.symbol, hit.source_path) for hit in hits}
    missing = [item for item in REQUIRED if item not in found]
    if missing:
        raise RuntimeError(f"context is missing required sources: {missing}")


def _item(hit: Material) -> dict:
    return {
        "doc_id": hit.doc_id,
        "symbol": hit.symbol,
        "path": hit.source_path,
        "score": hit.score,
        "excerpt": hit.body.strip(),
    }


def _progress(state: dict | None) -> tuple[list[str], list[str]]:
    if state is None:
        return [], ["让 filter_expenses 按 SPEC-AMT-001 应用 min_amount"]
    verification = state.get("verification") or {}
    if verification.get("target") == "passed" and verification.get("regression") == "passed":
        return ["min_amount 已实现，目标测试和类别回归测试都已通过"], []
    return [], ["继续完成 min_amount，已有验证尚未全部通过"]
