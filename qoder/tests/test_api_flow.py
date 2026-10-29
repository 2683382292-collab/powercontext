# ruff: noqa: S101
"""HTTP 层端到端测试：用 FastAPI TestClient 打通 /api/* 与假 PowerContext。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import build_app
from conftest import FakePowerContext, make_settings


def _client(fake: FakePowerContext) -> TestClient:
    return TestClient(build_app(make_settings(), transport=fake.transport()))


def test_health_and_subjects():
    fake = FakePowerContext()
    with _client(fake) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["powercontext_alive"] is True
        assert body["powercontext_url"] == "http://testserver"

        subjects = client.get("/api/subjects").json()["subjects"]
        names = {s["name"] for s in subjects}
        assert "python" in names and "math" in names and "english" in names


def test_full_review_loop_over_http():
    fake = FakePowerContext()
    with _client(fake) as client:
        # 1) 开新会话（首次为诊断）
        start = client.post("/api/session/start", json={"learner_id": "eve", "subject": "python", "size": 3})
        assert start.status_code == 200
        data = start.json()
        assert data["is_diagnostic"] is True
        assert len(data["questions"]) == 3
        q = data["questions"][0]

        # 2) 答错一题 → 写入错题与薄弱点
        wrong = (q["correct_index"] + 1) % len(q["options"])
        ans = client.post("/api/answer", json={
            "learner_id": "eve",
            "subject": "python",
            "session_id": data["session_id"],
            "question_id": q["id"],
            "kp": q["kp"],
            "prompt": q["prompt"],
            "options": q["options"],
            "correct_index": q["correct_index"],
            "given_index": wrong,
        })
        assert ans.status_code == 200
        abody = ans.json()
        assert abody["is_correct"] is False
        assert abody["weak_point"]["misses"] == 1

        # 3) 总览反映已持久化的记忆
        ov = client.get("/api/overview", params={"learner_id": "eve"}).json()
        assert ov["totals"]["wrong_questions"] >= 1
        assert ov["totals"]["weak_points"] >= 1
        assert any(w["kp"] == q["kp"] for w in ov["weak_points"])

        # 4) 新会话自动读取历史并靶向薄弱点
        start2 = client.post("/api/session/start", json={"learner_id": "eve", "subject": "python", "size": 4})
        d2 = start2.json()
        assert d2["is_diagnostic"] is False
        assert any(item["kp"] == q["kp"] or item["id"] == q["id"] for item in d2["questions"])


def test_unsupported_subject_returns_400():
    fake = FakePowerContext()
    with _client(fake) as client:
        r = client.post("/api/session/start", json={"learner_id": "x", "subject": "quantum", "size": 3})
        assert r.status_code == 400


def test_powercontext_unreachable_returns_502():
    # 用一个对所有请求返回 500 的传输，模拟上游 PowerContext 故障
    import httpx

    def boom(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "internal", "message": "boom"}})

    app = build_app(make_settings(), transport=httpx.MockTransport(boom))
    with TestClient(app) as client:
        r = client.post("/api/session/start", json={"learner_id": "y", "subject": "python", "size": 3})
        assert r.status_code == 502
