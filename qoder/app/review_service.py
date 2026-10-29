"""复习业务编排。

把「题库选题」与「PowerContext 记忆读写」组合成三条用例：

- :meth:`ReviewService.start_session`：开新一轮复习——读取历史记忆、生成叙述式回忆、
  按薄弱知识点/错题靶向出题（无历史则出诊断题）。
- :meth:`ReviewService.submit_answer`：判分并把结果写回 PowerContext（错题、薄弱点、进度）。
- :meth:`ReviewService.overview`：读取学习者的薄弱点、错题本与进度总览。

后端保持无状态：题目随会话下发，答题时前端回传题目上下文，所有持久状态都在 PowerContext。
"""

from __future__ import annotations

import uuid

from app import question_bank
from app.memory_repository import LearnerHistory, MemoryRepository
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    OverviewResponse,
    ProgressOut,
    QuestionOut,
    ReviewProgress,
    SessionStartResponse,
    WeakPoint,
    WeakPointOut,
    WrongQuestionEvent,
)


def _weak_point_out(wp: WeakPoint) -> WeakPointOut:
    accuracy = round((wp.attempts - wp.misses) / wp.attempts, 4) if wp.attempts else 0.0
    return WeakPointOut(
        subject=wp.subject,
        kp=wp.kp,
        misses=wp.misses,
        attempts=wp.attempts,
        accuracy=accuracy,
    )


def _progress_out(p: ReviewProgress) -> ProgressOut:
    return ProgressOut(
        session_id=p.session_id,
        subject=p.subject,
        total=p.total,
        correct=p.correct,
        accuracy=p.accuracy,
        ts=p.ts,
        weak_kps=list(p.weak_kps),
    )


class ReviewService:
    """面向前端用例的服务层。"""

    def __init__(self, repository: MemoryRepository, *, default_size: int = 5) -> None:
        self._repo = repository
        self._default_size = default_size

    # ------------------------------------------------------------------ #
    # 用例一：开新会话（自动读历史 + 针对性出题）
    # ------------------------------------------------------------------ #
    async def start_session(self, learner_id: str, subject: str, size: int | None = None) -> SessionStartResponse:
        count = size or self._default_size
        scope_id = await self._repo.ensure_scope(learner_id)
        history = await self._repo.load_history(scope_id)
        session_id = uuid.uuid4().hex[:12]

        weak_kps = _ranked_weak_kps(history, subject)
        recent_wrong_ids = _recent_wrong_ids(history, subject)
        is_diagnostic = not weak_kps and not recent_wrong_ids

        if is_diagnostic:
            picked = question_bank.diagnostic_questions(subject, count)
            weak_set: set[str] = set()
            wrong_set: set[str] = set()
        else:
            picked = question_bank.targeted_questions(
                subject,
                count,
                weak_kps=weak_kps,
                recent_wrong_ids=recent_wrong_ids,
            )
            weak_set = set(weak_kps)
            wrong_set = set(recent_wrong_ids)

        questions = [_to_question_out(q, is_diagnostic, weak_set, wrong_set) for q in picked]

        recall_text = None
        if not is_diagnostic:
            recall_text = await self._repo.recall_narrative(scope_id, subject, weak_kps)

        summary = _build_summary(history, subject, is_diagnostic, weak_kps)

        return SessionStartResponse(
            learner_id=learner_id,
            scope_id=scope_id,
            subject=subject,
            session_id=session_id,
            is_diagnostic=is_diagnostic,
            remembered_summary=summary,
            recall_text=recall_text,
            weak_points=[_weak_point_out(history.weak_points[(subject, kp)]) for kp in weak_kps
                         if (subject, kp) in history.weak_points],
            questions=questions,
        )

    # ------------------------------------------------------------------ #
    # 用例二：提交答案（判分 + 写回记忆）
    # ------------------------------------------------------------------ #
    async def submit_answer(self, req: AnswerRequest) -> AnswerResponse:
        scope_id = await self._repo.ensure_scope(req.learner_id)

        # 以题库为权威判定正确答案与解析；题库缺失时回退到请求携带的值。
        bank_item = question_bank.by_id(req.subject, req.question_id)
        correct_index = bank_item.correct_index if bank_item else req.correct_index
        explanation = bank_item.explanation if bank_item else ""
        is_correct = req.given_index == correct_index

        history = await self._repo.load_history(scope_id)

        # 1) 错题本：仅答错时追加一条事件
        if not is_correct:
            event = WrongQuestionEvent(
                question_id=req.question_id,
                subject=req.subject,
                kp=req.kp,
                prompt=req.prompt,
                options=req.options,
                correct_index=correct_index,
                given_index=req.given_index,
                is_correct=False,
            )
            await self._repo.append_wrong_question(scope_id, event)

        # 2) 薄弱知识点：在既有快照上累加 attempts / misses，追加新版本
        prev_wp = history.weak_point(req.subject, req.kp)
        updated_wp = WeakPoint(
            subject=req.subject,
            kp=req.kp,
            attempts=(prev_wp.attempts if prev_wp else 0) + 1,
            misses=(prev_wp.misses if prev_wp else 0) + (0 if is_correct else 1),
            sample_question_id=req.question_id if not is_correct else (prev_wp.sample_question_id if prev_wp else None),
        )
        await self._repo.append_weak_point(scope_id, updated_wp)

        # 3) 复习进度：在该会话的既有进度上累加，追加新快照
        prev_prog = history.latest_progress(req.session_id)
        weak_snapshot = dict(history.weak_points)
        weak_snapshot[(req.subject, req.kp)] = updated_wp
        weak_kps_now = _ranked_weak_kps_from(weak_snapshot, req.subject)
        progress = ReviewProgress(
            session_id=req.session_id,
            subject=req.subject,
            total=(prev_prog.total if prev_prog else 0) + 1,
            correct=(prev_prog.correct if prev_prog else 0) + (1 if is_correct else 0),
            weak_kps=weak_kps_now,
        )
        await self._repo.append_progress(scope_id, progress)

        return AnswerResponse(
            is_correct=is_correct,
            correct_index=correct_index,
            explanation=explanation,
            weak_point=_weak_point_out(updated_wp),
            session_progress=_progress_out(progress),
        )

    # ------------------------------------------------------------------ #
    # 用例三：总览（薄弱点 / 错题本 / 进度）
    # ------------------------------------------------------------------ #
    async def overview(self, learner_id: str) -> OverviewResponse:
        scope_id = await self._repo.ensure_scope(learner_id)
        history = await self._repo.load_history(scope_id)

        weak_outs = sorted(
            (_weak_point_out(wp) for wp in history.weak_points.values()),
            key=lambda w: (w.misses, w.attempts),
            reverse=True,
        )

        # 每个会话取最新一条进度
        latest_by_session: dict[str, ProgressOut] = {}
        for p in history.progress:  # 已按时间新→旧
            if p.session_id not in latest_by_session:
                latest_by_session[p.session_id] = _progress_out(p)

        total_attempts = sum(wp.attempts for wp in history.weak_points.values())
        totals = {
            "wrong_questions": len(history.wrong_questions),
            "weak_points": len(weak_outs),
            "sessions": len(latest_by_session),
            "answers": total_attempts,
        }

        return OverviewResponse(
            learner_id=learner_id,
            scope_id=scope_id,
            subjects=history.subjects,
            weak_points=weak_outs,
            wrong_questions=history.wrong_questions[:50],
            latest_progress=list(latest_by_session.values())[:10],
            totals=totals,
        )


# ---------------------------------------------------------------------- #
# 内部辅助
# ---------------------------------------------------------------------- #
def _ranked_weak_kps(history: LearnerHistory, subject: str) -> list[str]:
    return _ranked_weak_kps_from(history.weak_points, subject)


def _ranked_weak_kps_from(weak_points: dict[tuple[str, str], WeakPoint], subject: str) -> list[str]:
    """按薄弱程度（misses 多、正确率低、最近）排序，返回该学科的知识点名。"""

    items = [wp for (subj, _), wp in weak_points.items() if subj == subject and wp.misses > 0]
    items.sort(key=lambda w: (w.misses, w.last_ts), reverse=True)
    return [w.kp for w in items]


def _recent_wrong_ids(history: LearnerHistory, subject: str) -> list[str]:
    """该学科最近答错的题目 ID（新→旧，去重）。"""

    seen: dict[str, None] = {}
    for event in history.wrong_questions:
        if event.subject == subject:
            seen.setdefault(event.question_id, None)
    return list(seen)


def _to_question_out(
    q: question_bank.Question,
    is_diagnostic: bool,
    weak_set: set[str],
    wrong_set: set[str],
) -> QuestionOut:
    if is_diagnostic:
        origin = "diagnostic"
    elif q.id in wrong_set:
        origin = "wrong_question"
    elif q.kp in weak_set:
        origin = "weak_point"
    else:
        origin = "filler"
    return QuestionOut(
        id=q.id,
        subject=q.subject,
        kp=q.kp,
        prompt=q.prompt,
        options=list(q.options),
        correct_index=q.correct_index,
        explanation=q.explanation,
        origin=origin,
    )


def _build_summary(history: LearnerHistory, subject: str, is_diagnostic: bool, weak_kps: list[str]) -> str:
    if is_diagnostic:
        return (
            f"这是你在「{subject}」的首次复习，PowerContext 中还没有相关记忆。"
            "先做一组诊断题，我会据此定位并记录你的薄弱知识点。"
        )
    subject_wrong = sum(1 for e in history.wrong_questions if e.subject == subject)
    sessions = len({p.session_id for p in history.progress if p.subject == subject})
    parts = [
        f"已从 PowerContext 载入你在「{subject}」的历史记忆：累计错题 {subject_wrong} 道、"
        f"完成复习 {sessions} 轮。"
    ]
    if weak_kps:
        focus = "、".join(weak_kps[:5])
        parts.append(f"本轮将重点复盘这些薄弱知识点：{focus}。")
    return "".join(parts)
