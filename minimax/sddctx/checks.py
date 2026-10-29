"""Run the sample tests and record the command, exit code, and code version."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from sddctx.paths import LEDGER, SAMPLE


def code_version(path: Path = LEDGER) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + digest


def run_suite(label: str, *, cwd: Path = SAMPLE) -> dict:
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return {
        "label": label,
        "command": " ".join(command),
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "code_version": code_version(),
        "output": output,
    }


def target_failed_for_behavior(log: dict) -> bool:
    text = log["output"]
    return (
        log["exit_code"] != 0
        and "AssertionError" in text
        and "test_min_amount" in text
        and "ModuleNotFoundError" not in text
        and "ImportError" not in text
    )


def suite_passed(log: dict) -> bool:
    return log["exit_code"] == 0 and "FAILED" not in log["output"]


def format_log(log: dict) -> str:
    return (
        f"label: {log['label']}\n"
        f"command: {log['command']}\n"
        f"cwd: {log['cwd']}\n"
        f"exit_code: {log['exit_code']}\n"
        f"code_version: {log['code_version']}\n"
        "----\n"
        f"{log['output']}"
    )
