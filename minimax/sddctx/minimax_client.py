"""Ask MiniMax to edit ledger.py from the assembled context."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL)


def develop(context: dict, ledger_source: str) -> tuple[str, dict]:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MINIMAX_API_KEY is empty")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3").strip() or "MiniMax-M3"
    base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic").rstrip("/")
    prompt = _prompt(context, ledger_source)
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        base + "/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
       headers = {
    "X-Api-Key": "sk-api-qUipklTUx6oKqb0ZkIffDzaT1FZ5C_L1L_loBVIrYY2F671D52TAWdOd-c8KmuR1JigmMWgTtWmIpVvMns8DF0Pm-4d6wR6Ep4UzsqPXfFAeUJBlD6EeEus",
    "X-Group-ID": "571692052710653954",
    "Content-Type": "application/json"
}

        ,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MiniMax HTTP {exc.code}: {detail[:500]}") from exc
    body = json.loads(raw)
    text = _message_text(body)
    updated = _extract_python(text)
    _validate_edit(updated)
    record = {
        "provider": "MiniMax",
        "api": "anthropic-messages",
        "base_url": base,
        "model": model,
        "task_id": context["task_id"],
        "query": context["query"],
        "context": context,
        "ledger_before_sha256": _sha(ledger_source),
        "response_text": text,
        "ledger_after_sha256": _sha(updated),
    }
    return updated, record


def load_saved_edit(path: Path) -> str | None:
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    text = record.get("response_text") or ""
    updated = _extract_python(text)
    _validate_edit(updated)
    return updated


def _prompt(context: dict, ledger_source: str) -> str:
    return (
        "Edit the Python module so it satisfies the specs in the context.\n"
        "Keep every behavior the context says must be preserved.\n"
        "Return the complete replacement module in one python fence.\n"
        "Do not include prose outside the fence.\n\n"
        "CONTEXT:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
        + "\n\nCURRENT MODULE:\n```python\n"
        + ledger_source
        + "\n```\n"
    )


def _message_text(body: dict) -> str:
    content = body.get("content")
    if isinstance(content, str):
        text = content.strip()
    else:
        parts = [
            block.get("text", "")
            for block in content or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise RuntimeError("MiniMax response did not contain text")
    return text


def _extract_python(text: str) -> str:
    match = _FENCE.search(text)
    source = match.group(1) if match else text
    source = source.strip() + "\n"
    if "def filter_expenses" not in source:
        raise RuntimeError("MiniMax response did not contain filter_expenses")
    return source


def _validate_edit(source: str) -> None:
    compile(source, "ledger.py", "exec")
    if "min_amount" not in source or "category" not in source:
        raise RuntimeError("edited module dropped min_amount or category")
    if "del min_amount" in source:
        raise RuntimeError("edited module still ignores min_amount")


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
