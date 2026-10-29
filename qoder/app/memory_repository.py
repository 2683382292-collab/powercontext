"""记忆仓储：领域对象 ↔ PowerContext Atomic Memory 的映射。

PowerContext 的记忆是**追加式**的：每次 ``remember`` 都写入一条新的 Atomic Memory。
本模块据此把复习领域对象编码为三类 ``kind``：

- ``wrong_question``：仅在答错时追加一条错题事件，构成"错题本"。
- ``weak_point``：某知识点的最新快照（attempts / misses），每次作答后追加新版本，
  读取时按时间取每个 (subject, kp) 的最新一条。
- ``review_progress``：某轮会话的进度快照，读取时按 session_id 取最新。

记忆 ``text`` 字段存放对象的 JSON 序列化，读取时反序列化；无法解析的记录被安全跳过。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.powercontext_client import PowerContextClient
from app.schemas import (
    KIND_REVIEW_PROGRESS,
    KIND_WEAK_POINT,
    KIND_WRONG_QUESTION,
    ReviewProgress,
    WeakPoint,
    WrongQuestionEvent,
)


@dataclass
class LearnerHistory:
    """从 PowerContext 读取并解析后的学习者历史。"""

    weak_points: dict[tuple[str, str], WeakPoint] = field(default_factory=dict)
    wrong_questions: list[WrongQuestionEvent] = field(default_factory=list)  # 新→旧
    progress: list[ReviewProgress] = field(default_factory=list)  # 新→旧

    def weak_point(self, subject: str, kp: str) -> WeakPoint | None:
        return self.weak_points.get((subject, kp))

    def latest_progress(self, session_id: str) -> ReviewProgress | None:
        for item in self.progress:
            if item.session_id == session_id:
                return item
        return None

    @property
    def subjects(self) -> list[str]:
        seen: dict[str, None] = {}
        for subject, _ in self.weak_points:
            seen.setdefault(subject, None)
        for event in self.wrong_questions:
            seen.setdefault(event.subject, None)
        return list(seen)

    def has_history(self) -> bool:
        return bool(self.weak_points or self.wrong_questions or self.progress)


class MemoryRepository:
    """封装对 PowerContext 的读写，并做领域序列化/反序列化。"""

    def __init__(self, client: PowerContextClient, scope_prefix: str) -> None:
        self._client = client
        self._scope_prefix = scope_prefix
        self._scope_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Scope
    # ------------------------------------------------------------------ #
    async def ensure_scope(self, learner_id: str) -> str:
        """为学习者返回稳定的 scope_id（幂等创建 + 进程内缓存）。"""

        cached = self._scope_cache.get(learner_id)
        if cached:
            return cached
        idem = f"{self._scope_prefix}-{learner_id}"
        scope_id = await self._client.create_scope(
            title=f"复习助手 · {learner_id}",
            summary=f"个性化复习助手为学习者 {learner_id} 记录的薄弱知识点、错题与复习进度。",
            idempotency_key=idem,
        )
        self._scope_cache[learner_id] = scope_id
        return scope_id

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    async def append_wrong_question(self, scope_id: str, event: WrongQuestionEvent) -> None:
        await self._client.remember(
            scope_id,
            KIND_WRONG_QUESTION,
            event.model_dump_json(),
            reason=f"错题：{event.subject}/{event.kp}",
        )

    async def append_weak_point(self, scope_id: str, weak_point: WeakPoint) -> None:
        await self._client.remember(
            scope_id,
            KIND_WEAK_POINT,
            weak_point.model_dump_json(),
            reason=f"薄弱知识点快照：{weak_point.kp}（misses={weak_point.misses}）",
        )

    async def append_progress(self, scope_id: str, progress: ReviewProgress) -> None:
        await self._client.remember(
            scope_id,
            KIND_REVIEW_PROGRESS,
            progress.model_dump_json(),
            reason=f"复习进度：{progress.subject} {progress.correct}/{progress.total}",
        )

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    async def load_history(self, scope_id: str) -> LearnerHistory:
        """枚举全部活跃记忆并按 kind 解析为 :class:`LearnerHistory`。"""

        entries = await self._client.list_memory_entries(scope_id)
        history = LearnerHistory()

        weak_latest: dict[tuple[str, str], WeakPoint] = {}
        wrong: list[WrongQuestionEvent] = []
        progress: list[ReviewProgress] = []

        for entry in entries:
            kind = entry.get("kind")
            payload = _parse_text(entry.get("text"))
            if payload is None:
                continue
            try:
                if kind == KIND_WRONG_QUESTION:
                    wrong.append(WrongQuestionEvent.model_validate(payload))
                elif kind == KIND_WEAK_POINT:
                    wp = WeakPoint.model_validate(payload)
                    key = (wp.subject, wp.kp)
                    prev = weak_latest.get(key)
                    if prev is None or wp.last_ts >= prev.last_ts:
                        weak_latest[key] = wp
                elif kind == KIND_REVIEW_PROGRESS:
                    progress.append(ReviewProgress.model_validate(payload))
            except Exception:  # noqa: BLE001 - 单条脏数据不应影响整体读取
                continue

        wrong.sort(key=lambda e: e.ts, reverse=True)
        progress.sort(key=lambda p: p.ts, reverse=True)
        history.weak_points = weak_latest
        history.wrong_questions = wrong
        history.progress = progress
        return history

    async def recall_narrative(self, scope_id: str, subject: str, weak_kps: list[str]) -> str | None:
        """调用 ``/v1/context/prepare`` 获取叙述式回忆；无内容时返回 ``None``。"""

        if weak_kps:
            query = f"{subject} 薄弱知识点复习：{'、'.join(weak_kps[:6])}"
        else:
            query = f"{subject} 复习历史与常见错误"
        try:
            prepared = await self._client.prepare_context(scope_id, query, max_bytes=4000)
        except Exception:  # noqa: BLE001 - 回忆失败不阻断出题
            return None
        if prepared.get("status") == "ready":
            content = prepared.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return None


def _parse_text(text: Any) -> dict[str, Any] | None:
    """把记忆 text 解析为 dict；非 JSON 或非对象返回 None。"""

    if not isinstance(text, str) or not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
