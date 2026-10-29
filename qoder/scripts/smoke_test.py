"""端到端验证样例（对真实运行的服务发起请求）。

前置条件：
  1) 已启动 PowerContext Server（默认 http://127.0.0.1:8000）；
  2) 已启动复习助手后端（默认 http://127.0.0.1:8080，见 qoder/run.py）。

运行：
    uv run python scripts/smoke_test.py
    # 或指定地址：
    python scripts/smoke_test.py --assistant http://127.0.0.1:8080 --powercontext http://127.0.0.1:8000

脚本会依次验证：连接性 → 首次诊断出题 → 答错写入记忆 → 总览 → 新会话自动读历史并靶向薄弱点。
每一步打印 PASS/FAIL，全部通过返回退出码 0。
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

PASS = "[PASS]"
FAIL = "[FAIL]"


class Smoke:
    def __init__(self, assistant: str, powercontext: str) -> None:
        self.assistant = assistant.rstrip("/")
        self.powercontext = powercontext.rstrip("/")
        self.failures: list[str] = []
        self.learner = f"smoke-{int(time.time())}"

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        mark = PASS if condition else FAIL
        suffix = f" — {detail}" if detail else ""
        print(f"{mark} {name}{suffix}")
        if not condition:
            self.failures.append(name)
        return condition

    def run(self) -> int:
        with httpx.Client(timeout=15.0) as http:
            self._step_powercontext_alive(http)
            self._step_assistant_health(http)
            subjects = self._step_subjects(http)
            subject = subjects[0] if subjects else "python"
            session = self._step_first_session(http, subject)
            if session and session.get("questions"):
                self._step_answer_wrong(http, subject, session)
                self._step_overview(http)
                self._step_new_session_targets(http, subject, session)

        print()
        if self.failures:
            print(f"结果：{len(self.failures)} 步未通过 -> {', '.join(self.failures)}")
            return 1
        print(f"结果：全部通过 ✅  （学习者 scope 前缀键：{self.learner}）")
        return 0

    # ---- 各步骤 ---- #
    def _step_powercontext_alive(self, http: httpx.Client) -> None:
        try:
            r = http.get(f"{self.powercontext}/health/live")
            ok = r.status_code == 200
        except httpx.HTTPError as exc:
            self.check("PowerContext 存活探针", False, f"请求失败：{exc}")
            print("\nPowerContext 未就绪，请先启动：uv run powercontext server run")
            sys.exit(2)
        self.check("PowerContext 存活探针", ok, f"HTTP {r.status_code}")

    def _step_assistant_health(self, http: httpx.Client) -> None:
        try:
            r = http.get(f"{self.assistant}/api/health")
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.check("复习助手后端健康检查", False, f"请求失败：{exc}")
            print("\n复习助手后端未就绪，请先启动：uv run python run.py（在 qoder 目录）")
            sys.exit(2)
        self.check(
            "复习助手后端健康检查",
            r.status_code == 200 and data.get("powercontext_alive") is True,
            f"powercontext_alive={data.get('powercontext_alive')}",
        )

    def _step_subjects(self, http: httpx.Client) -> list[str]:
        r = http.get(f"{self.assistant}/api/subjects")
        names = [s["name"] for s in r.json().get("subjects", [])] if r.status_code == 200 else []
        self.check("题库学科可用", bool(names), f"subjects={names}")
        return names

    def _step_first_session(self, http: httpx.Client, subject: str) -> dict | None:
        r = http.post(
            f"{self.assistant}/api/session/start",
            json={"learner_id": self.learner, "subject": subject, "size": 3},
        )
        data = r.json() if r.status_code == 200 else {}
        ok = r.status_code == 200 and data.get("is_diagnostic") is True and len(data.get("questions", [])) == 3
        self.check("首次会话为诊断且下发 3 题", ok, f"HTTP {r.status_code}")
        return data or None

    def _step_answer_wrong(self, http: httpx.Client, subject: str, session: dict) -> None:
        q = session["questions"][0]
        wrong = (q["correct_index"] + 1) % len(q["options"])
        r = http.post(
            f"{self.assistant}/api/answer",
            json={
                "learner_id": self.learner,
                "subject": subject,
                "session_id": session["session_id"],
                "question_id": q["id"],
                "kp": q["kp"],
                "prompt": q["prompt"],
                "options": q["options"],
                "correct_index": q["correct_index"],
                "given_index": wrong,
            },
        )
        data = r.json() if r.status_code == 200 else {}
        self.check(
            "答错判分并写入薄弱点/错题记忆",
            r.status_code == 200 and data.get("is_correct") is False and data.get("weak_point", {}).get("misses") == 1,
            f"kp={q['kp']}",
        )

    def _step_overview(self, http: httpx.Client) -> None:
        r = http.get(f"{self.assistant}/api/overview", params={"learner_id": self.learner})
        data = r.json() if r.status_code == 200 else {}
        totals = data.get("totals", {})
        self.check(
            "总览读回持久化记忆",
            r.status_code == 200 and totals.get("wrong_questions", 0) >= 1 and totals.get("weak_points", 0) >= 1,
            f"totals={totals}",
        )

    def _step_new_session_targets(self, http: httpx.Client, subject: str, session: dict) -> None:
        r = http.post(
            f"{self.assistant}/api/session/start",
            json={"learner_id": self.learner, "subject": subject, "size": 4},
        )
        data = r.json() if r.status_code == 200 else {}
        first_q = session["questions"][0]
        targeted = any(item["kp"] == first_q["kp"] or item["id"] == first_q["id"] for item in data.get("questions", []))
        self.check(
            "新会话自动读历史并靶向薄弱点",
            r.status_code == 200 and data.get("is_diagnostic") is False and targeted,
            f"remembered='{data.get('remembered_summary', '')[:40]}...'",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="复习助手端到端验证样例")
    parser.add_argument("--assistant", default="http://127.0.0.1:8080")
    parser.add_argument("--powercontext", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    print(f"复习助手: {args.assistant}")
    print(f"PowerContext: {args.powercontext}\n")
    return Smoke(args.assistant, args.powercontext).run()


if __name__ == "__main__":
    raise SystemExit(main())
