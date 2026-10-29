# ruff: noqa: S101
"""复习业务编排的行为测试（使用内存版假 PowerContext，无需真实 Server）。"""

from __future__ import annotations

import asyncio

from app.schemas import AnswerRequest


def _run(coro):
    return asyncio.run(coro)


def _answer_req(session, q, given_index):
    return AnswerRequest(
        learner_id=session.learner_id,
        subject=q.subject,
        session_id=session.session_id,
        question_id=q.id,
        kp=q.kp,
        prompt=q.prompt,
        options=q.options,
        correct_index=q.correct_index,
        given_index=given_index,
    )


def test_first_session_is_diagnostic(service):
    resp = _run(service.start_session("alice", "python", size=4))
    assert resp.is_diagnostic is True
    assert resp.recall_text is None
    assert len(resp.questions) == 4
    assert all(q.origin == "diagnostic" for q in resp.questions)
    # 诊断题应跨多个知识点，覆盖面尽量广
    assert len({q.kp for q in resp.questions}) >= 2
    assert resp.scope_id


def test_wrong_answer_persists_wrong_question_and_weak_point(service, fake):
    resp = _run(service.start_session("bob", "python", size=3))
    q = resp.questions[0]
    wrong_index = (q.correct_index + 1) % len(q.options)

    ans = _run(service.submit_answer(_answer_req(resp, q, wrong_index)))

    assert ans.is_correct is False
    assert ans.correct_index == q.correct_index
    assert ans.weak_point is not None
    assert ans.weak_point.misses == 1
    assert ans.weak_point.attempts == 1
    assert ans.session_progress.total == 1
    assert ans.session_progress.correct == 0

    # 验证确实写入了 PowerContext（三类 kind 都在）
    scope = fake.scope_for("qoder-test-bob")
    assert scope is not None
    kinds = {r["kind"] for r in fake.records(scope)}
    assert {"wrong_question", "weak_point", "review_progress"} <= kinds


def test_correct_answer_records_progress_without_wrong_question(service, fake):
    resp = _run(service.start_session("carol", "math", size=3))
    q = resp.questions[0]

    ans = _run(service.submit_answer(_answer_req(resp, q, q.correct_index)))

    assert ans.is_correct is True
    assert ans.weak_point.misses == 0
    assert ans.weak_point.attempts == 1
    assert ans.session_progress.correct == 1

    scope = fake.scope_for("qoder-test-carol")
    kinds = [r["kind"] for r in fake.records(scope)]
    assert "wrong_question" not in kinds
    assert "weak_point" in kinds
    assert "review_progress" in kinds


def test_progress_accumulates_within_session(service):
    resp = _run(service.start_session("frank", "python", size=3))
    q0, q1 = resp.questions[0], resp.questions[1]

    a0 = _run(service.submit_answer(_answer_req(resp, q0, q0.correct_index)))       # 对
    a1 = _run(service.submit_answer(_answer_req(resp, q1, (q1.correct_index + 1) % len(q1.options))))  # 错

    assert a0.session_progress.total == 1 and a0.session_progress.correct == 1
    assert a1.session_progress.total == 2 and a1.session_progress.correct == 1
    assert a1.session_progress.accuracy == 0.5


def test_new_session_reads_history_and_targets_weak_points(service):
    # 第一轮：把某个知识点连续答错两次，形成薄弱点与错题
    first = _run(service.start_session("dave", "python", size=5))
    target = first.questions[0]
    wrong_index = (target.correct_index + 1) % len(target.options)
    for _ in range(2):
        _run(service.submit_answer(_answer_req(first, target, wrong_index)))

    # 新一轮：应自动读取历史、不再是诊断、并靶向该薄弱点
    second = _run(service.start_session("dave", "python", size=5))
    assert second.is_diagnostic is False
    assert second.remembered_summary
    assert second.recall_text is not None  # context/prepare 返回了叙述式回忆

    weak_kps = [w.kp for w in second.weak_points]
    assert target.kp in weak_kps
    # 薄弱点计数已累积到 2
    wp = next(w for w in second.weak_points if w.kp == target.kp)
    assert wp.misses == 2

    # 出题应重练错题或靶向该薄弱知识点
    assert any(q.id == target.id or q.kp == target.kp for q in second.questions)
    origins = {q.origin for q in second.questions}
    assert origins & {"wrong_question", "weak_point"}
