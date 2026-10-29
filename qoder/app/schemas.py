"""领域模型与 API 数据契约。

这里定义两类模型：

1. **记忆载荷**（``WeakPoint`` / ``WrongQuestionEvent`` / ``ReviewProgress``）：
   会被序列化成 JSON 存入 PowerContext Atomic Memory 的 ``text`` 字段。
2. **API 契约**（``*Request`` / ``*Response`` / ``*Out``）：前后端之间传输的 JSON。
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

# PowerContext 记忆的 kind 取值（自定义，用于区分领域对象）
KIND_WEAK_POINT = "weak_point"
KIND_WRONG_QUESTION = "wrong_question"
KIND_REVIEW_PROGRESS = "review_progress"


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------- #
# 记忆载荷（存入 PowerContext memory.text 的 JSON）
# ---------------------------------------------------------------------- #
class WrongQuestionEvent(BaseModel):
    """一次答题事件（无论对错都记录，错题构成错题本与薄弱点计数的证据来源）。"""

    question_id: str
    subject: str
    kp: str
    prompt: str
    options: list[str] = Field(default_factory=list)
    correct_index: int
    given_index: int
    is_correct: bool
    ts: float = Field(default_factory=_now)


class WeakPoint(BaseModel):
    """某个知识点的薄弱快照；misses 越大越薄弱。"""

    subject: str
    kp: str
    misses: int = 0
    attempts: int = 0
    last_ts: float = Field(default_factory=_now)
    sample_question_id: str | None = None


class ReviewProgress(BaseModel):
    """一轮复习的进度快照。"""

    session_id: str
    subject: str
    total: int = 0
    correct: int = 0
    ts: float = Field(default_factory=_now)
    weak_kps: list[str] = Field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return round(self.correct / self.total, 4) if self.total else 0.0


# ---------------------------------------------------------------------- #
# API 契约
# ---------------------------------------------------------------------- #
class QuestionOut(BaseModel):
    """下发给前端的题目（含正确答案与解析，前端在作答后再揭示）。"""

    id: str
    subject: str
    kp: str
    prompt: str
    options: list[str]
    correct_index: int
    explanation: str = ""
    # 标记本题来源：诊断 / 靶向薄弱点 / 错题重练
    origin: Literal["diagnostic", "weak_point", "wrong_question", "filler"] = "diagnostic"


class WeakPointOut(BaseModel):
    subject: str
    kp: str
    misses: int
    attempts: int
    accuracy: float = 0.0


class ProgressOut(BaseModel):
    session_id: str
    subject: str
    total: int
    correct: int
    accuracy: float
    ts: float
    weak_kps: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    app: str
    powercontext_url: str
    powercontext_alive: bool
    scope_prefix: str


class StartSessionRequest(BaseModel):
    learner_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=64)
    size: int | None = Field(default=None, ge=1, le=20)


class SessionStartResponse(BaseModel):
    learner_id: str
    scope_id: str
    subject: str
    session_id: str
    is_diagnostic: bool
    remembered_summary: str
    recall_text: str | None = None
    weak_points: list[WeakPointOut] = Field(default_factory=list)
    questions: list[QuestionOut] = Field(default_factory=list)


class AnswerRequest(BaseModel):
    learner_id: str = Field(min_length=1, max_length=128)
    subject: str
    session_id: str
    question_id: str
    kp: str
    prompt: str
    options: list[str] = Field(default_factory=list)
    correct_index: int
    given_index: int


class AnswerResponse(BaseModel):
    is_correct: bool
    correct_index: int
    explanation: str
    weak_point: WeakPointOut | None = None
    session_progress: ProgressOut


class OverviewResponse(BaseModel):
    learner_id: str
    scope_id: str
    subjects: list[str] = Field(default_factory=list)
    weak_points: list[WeakPointOut] = Field(default_factory=list)
    wrong_questions: list[WrongQuestionEvent] = Field(default_factory=list)
    latest_progress: list[ProgressOut] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
