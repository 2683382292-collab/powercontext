"""CLI for the SDD context module.

Commands:
  init       create an empty seekdb directory and PowerContext state file location
  import     load specs, code, and tests into seekdb
  context    print the context selected for the current task
  develop    send that context to MiniMax and write the edited module
  resume     restart PowerContext and use the saved state
  verify     run the sample from a clean ledger, including restart
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

from sddctx.checks import (
    format_log,
    run_suite,
    suite_passed,
    target_failed_for_behavior,
)
from sddctx.context import build_context, render_context
from sddctx.materials import MaterialStore
from sddctx.minimax_client import develop, load_saved_edit
from sddctx.paths import BEFORE, DATA, LEDGER, RECORDS, ROOT, STATE_DB, TASK
from sddctx.state import load_state, save_state

_UPDATED_SENTENCE = "金额下界按半开区间比较。标记 UPDATED-MIN-AMOUNT-EXCLUSIVE。"
_ORIGINAL_SENTENCE = "金额下界按闭区间比较。"


def main() -> None:
    parser = argparse.ArgumentParser(prog="sddctx")
    parser.add_argument(
        "command",
        choices=("init", "import", "context", "develop", "resume", "verify"),
    )
    args = parser.parse_args()
    if args.command == "init":
        _init()
    elif args.command == "import":
        print("\n".join(_import()))
    elif args.command == "context":
        print(render_context(_context()))
    elif args.command == "develop":
        _develop()
    elif args.command == "resume":
        print(render_context(_resume()))
    elif args.command == "verify":
        _verify()


def _init() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    RECORDS.mkdir(parents=True, exist_ok=True)
    (RECORDS / "seekdb").mkdir(exist_ok=True)
    (RECORDS / "powercontext").mkdir(exist_ok=True)
    (RECORDS / "minimax").mkdir(exist_ok=True)
    (RECORDS / "tests").mkdir(exist_ok=True)
    if STATE_DB.exists():
        STATE_DB.unlink()
    with MaterialStore() as store:
        store.reset()
    print("initialized")


def _import() -> list[str]:
    with MaterialStore() as store:
        imported = store.import_manifest()
        explain = store.explain("SPEC-AMT-001 min_amount filter_expenses")
    _write(RECORDS / "seekdb" / "import.json", {"imported": imported})
    _write(RECORDS / "seekdb" / "explain.txt", explain)
    return imported


def _context() -> dict:
    state = asyncio.run(load_state(STATE_DB))
    with MaterialStore() as store:
        context = build_context(store, state)
        _record_query(store, context["query"])
    _write(RECORDS / "context.json", context)
    return context


def _develop() -> None:
    context = _context()
    before = LEDGER.read_text(encoding="utf-8")
    updated, record = develop(context, before)
    LEDGER.write_text(updated, encoding="utf-8")
    record["diff"] = _unified(before, updated)
    _write(RECORDS / "minimax" / "development.json", record)
    _write(RECORDS / "minimax" / "applied.diff", record["diff"])
    print(record["diff"])


def _resume() -> dict:
    state = asyncio.run(load_state(STATE_DB))
    if state is None:
        raise SystemExit("PowerContext has no saved task state")
    with MaterialStore() as store:
        context = build_context(store, state)
    if context["state_source"] is None:
        raise SystemExit("saved state was loaded but not used")
    verification = context["verification"] or {}
    passed = verification.get("target") == "passed" and verification.get("regression") == "passed"
    if passed and context["pending"]:
        raise SystemExit("passed verification was not used to clear pending work")
    if not passed and not context["pending"]:
        raise SystemExit("unfinished state was not used to keep pending work")
    _write(RECORDS / "powercontext" / "resume.json", context)
    return context


def _verify() -> None:
    _init()
    _restore_ledger()
    _restore_spec()
    imported = _import()
    print("imported " + ", ".join(imported))

    before = run_suite("before")
    _write(RECORDS / "tests" / "before.log", format_log(before))
    if not target_failed_for_behavior(before):
        raise SystemExit("target tests did not fail because the behavior is missing")
    if "test_category" in before["output"] and "ERROR" in before["output"]:
        raise SystemExit("regression tests failed to start")
    category = _run_category()
    _write(RECORDS / "tests" / "before-category.log", format_log(category))
    if not suite_passed(category):
        raise SystemExit("category regression failed before the edit")

    first = _context()
    amount = _spec_excerpt(first, "SPEC-AMT-001")
    if _ORIGINAL_SENTENCE not in amount or _UPDATED_SENTENCE in amount:
        raise SystemExit("initial context did not use the imported amount spec")

    _replace_amount_sentence()
    _import()
    updated = _context()
    updated_amount = _spec_excerpt(updated, "SPEC-AMT-001")
    if _UPDATED_SENTENCE not in updated_amount or _ORIGINAL_SENTENCE in updated_amount:
        raise SystemExit("context kept the old spec after reimport")
    _write(
        RECORDS / "seekdb" / "spec-update.json",
        {"symbol": "SPEC-AMT-001", "excerpt": updated_amount},
    )
    _restore_spec()
    _import()

    edited = load_saved_edit(RECORDS / "minimax" / "development.json")
    if edited is None:
        context = _context()
        try:
            edited, record = develop(context, LEDGER.read_text(encoding="utf-8"))
        except RuntimeError as exc:
            raise SystemExit(
                "MiniMax 没有可复用的开发记录，并且本次调用失败："
                + str(exc)
                + "。请设置 MINIMAX_API_KEY 后执行 python -m sddctx develop，再重新执行 verify。"
            ) from exc
        record["diff"] = _unified(LEDGER.read_text(encoding="utf-8"), edited)
        _write(RECORDS / "minimax" / "development.json", record)
        _write(RECORDS / "minimax" / "applied.diff", record["diff"])
    LEDGER.write_text(edited, encoding="utf-8")

    after = run_suite("after")
    _write(RECORDS / "tests" / "after.log", format_log(after))
    category_after = _run_category()
    _write(RECORDS / "tests" / "after-category.log", format_log(category_after))
    if not suite_passed(after) or not suite_passed(category_after):
        raise SystemExit("tests failed after the edit")

    state = {
        "task_id": "add-min-amount",
        "sequence": 1,
        "completed": ["apply min_amount"],
        "verification": {"target": "passed", "regression": "passed"},
        "code_version": after["code_version"],
    }
    saved = asyncio.run(save_state(STATE_DB, state))
    _write(RECORDS / "powercontext" / "save.json", saved)

    resumed = json.loads(_spawn_resume())
    if resumed["verification"]["target"] != "passed" or resumed["pending"]:
        raise SystemExit("restarted module did not use the saved state")
    _write(RECORDS / "powercontext" / "reused.json", resumed)
    print("verify passed")


def _spawn_resume() -> str:
    import subprocess

    completed = subprocess.run(
        [sys.executable, "-m", "sddctx", "resume"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    _write(
        RECORDS / "powercontext" / "resume-process.log",
        f"command: {sys.executable} -m sddctx resume\nexit_code: {completed.returncode}\n----\n"
        + completed.stdout
        + completed.stderr,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or completed.stdout)
    return completed.stdout


def _run_category() -> dict:
    import subprocess

    from sddctx.checks import code_version

    command = [sys.executable, "-m", "unittest", "tests.test_category", "-v"]
    completed = subprocess.run(
        command,
        cwd=ROOT / "sample_app",
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return {
        "label": "category",
        "command": " ".join(command),
        "cwd": str(ROOT / "sample_app"),
        "exit_code": completed.returncode,
        "code_version": code_version(),
        "output": output,
    }


def _restore_ledger() -> None:
    shutil.copyfile(BEFORE, LEDGER)


def _restore_spec() -> None:
    path = ROOT / "sample_app" / "specs" / "amount.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace(_UPDATED_SENTENCE, _ORIGINAL_SENTENCE)
    path.write_text(text, encoding="utf-8")


def _replace_amount_sentence() -> None:
    path = ROOT / "sample_app" / "specs" / "amount.md"
    text = path.read_text(encoding="utf-8")
    if _ORIGINAL_SENTENCE not in text:
        raise SystemExit("amount spec does not contain the original sentence")
    path.write_text(text.replace(_ORIGINAL_SENTENCE, _UPDATED_SENTENCE), encoding="utf-8")


def _spec_excerpt(context: dict, symbol: str) -> str:
    matches = [item for item in context["specs"] if item["symbol"] == symbol]
    if len(matches) != 1:
        raise SystemExit(f"context did not select {symbol}")
    return matches[0]["excerpt"]


def _record_query(store: MaterialStore, query: str) -> None:
    hits = store.search(query)
    _write(
        RECORDS / "seekdb" / "query.json",
        {
            "statement": "MATCH(title, body) AGAINST(%s)",
            "query": query,
            "hits": [
                {"doc_id": hit.doc_id, "path": hit.source_path, "score": hit.score}
                for hit in hits
            ],
        },
    )


def _unified(before: str, after: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="sample_app/ledger.py",
            tofile="sample_app/ledger.py",
        )
    )


def _write(path: Path, value: dict | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
