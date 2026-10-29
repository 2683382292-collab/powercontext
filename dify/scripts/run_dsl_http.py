"""Execute the current innkeeper DSL over HTTP.

Code nodes are taken from ``dsl/grumpy-innkeeper.yml`` and run as the Chatflow
would. The search and remember steps are real HTTP calls to the URLs in that
DSL. The model step only turns the search response into the JSON object the
next code node already parses; the ledger itself comes back from PowerContext.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import textwrap
import urllib.request
from pathlib import Path

import yaml

from serve_powercontext_http import PowerContextHttpServer

ROOT = Path(__file__).resolve().parents[1]
DSL_PATH = ROOT / "dsl" / "grumpy-innkeeper.yml"
TURNS = ("我把一只青蛙放进了你的汤里", "再来一碗，这次我付钱")


def _load_code(node_id: str) -> str:
    document = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    for node in document["workflow"]["graph"]["nodes"]:
        if node["id"] == node_id and node["data"]["type"] == "code":
            return textwrap.dedent(node["data"]["code"])
    raise SystemExit(f"DSL is missing code node {node_id}")


def _urls() -> tuple[str, str]:
    document = yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))
    found = {}
    for node in document["workflow"]["graph"]["nodes"]:
        if node["data"]["type"] == "http-request":
            found[node["id"]] = node["data"]["url"]
    search = found["1003"]
    remember = found["1006"]
    if not search.endswith("/v1/memory/search") or not remember.endswith("/v1/memory/remember"):
        raise SystemExit(f"DSL HTTP urls changed: {search} {remember}")
    return search, remember


def _compile(source: str, name: str):
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - the code under test is the submitted DSL node
    if "main" not in namespace:
        raise SystemExit(f"{name} does not define main")
    return namespace["main"]


def _post(base: str, path: str, payload: dict) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read().decode("utf-8")


def _model_json(search_response: str, user_text: str) -> str:
    """Stand in for the Chatflow model node. It may only use the HTTP search body."""

    hits = json.loads(search_response).get("hits") or []
    ledger = [str(hit.get("text") or "") for hit in hits]
    if any("青蛙" in line for line in ledger):
        reply = f"青蛙的账还在。你现在说「{user_text}」，汤可以续，但这只青蛙我记得。"
    elif ledger:
        reply = f"旧账还在：{'；'.join(ledger)}。这回你说「{user_text}」。"
    else:
        reply = f"第一次进门。你说「{user_text}」，这笔记下了。"
    return json.dumps({"reply": reply, "memory": user_text}, ensure_ascii=False)


def run(base: str) -> dict:
    _urls()
    prepare = _compile(_load_code("1002"), "1002")
    parse = _compile(_load_code("1005"), "1005")
    status, scope_body = _post(
        base,
        "/v1/scopes",
        {
            "title": "老汤的记仇酒馆",
            "summary": "一位旅客的旧账",
            "idempotency_key": f"dsl-http-{os.getpid()}",
        },
    )
    scope_id = json.loads(scope_body)["scope_id"]
    turns = []
    for user_text in TURNS:
        prepared = prepare(user_text, scope_id)
        search_status, search_body = _post(base, "/v1/memory/search", json.loads(prepared["search_body"]))
        model_text = _model_json(search_body, prepared["user_text"])
        parsed = parse(model_text, prepared["user_text"], scope_id)
        remember_status, remember_body = _post(base, "/v1/memory/remember", json.loads(parsed["remember_body"]))
        turns.append(
            {
                "conversation": "new",
                "user": user_text,
                "node_1002_search_body": prepared["search_body"],
                "node_1003_http": {"status": search_status, "response": json.loads(search_body)},
                "node_1004_model_json": json.loads(model_text),
                "node_1005_remember_body": parsed["remember_body"],
                "node_1006_http": {"status": remember_status, "response": json.loads(remember_body)},
                "node_1007_answer": parsed["reply"],
            }
        )
    second_hits = "\n".join(hit["text"] for hit in turns[1]["node_1003_http"]["response"]["hits"])
    if "青蛙" not in second_hits:
        raise SystemExit("second conversation search did not return the frog ledger")
    if "青蛙" not in turns[1]["node_1007_answer"]:
        raise SystemExit("second answer did not use the searched ledger")
    if status != 200:
        raise SystemExit("scope creation failed")
    return {
        "path": "dsl-code-nodes-and-http",
        "dsl": "dify/dsl/grumpy-innkeeper.yml",
        "node_order": ["1002", "1003", "1004", "1005", "1006", "1007"],
        "http_routes": ["/v1/scopes", "/v1/memory/search", "/v1/memory/remember"],
        "base_url": base,
        "scope_id": scope_id,
        "turns": turns,
        "ok": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "dsl-http-run.json")
    args = parser.parse_args()
    base = os.environ.get("POWERCONTEXT_URL", "").strip()
    server = None
    if not base:
        directory = Path(tempfile.mkdtemp(prefix="innkeeper-http-"))
        server = PowerContextHttpServer(str(directory / "inn.db"))
        base = server.start()
    try:
        report = run(base)
        if server is not None:
            report["server_access_log"] = server.access_log
    finally:
        if server is not None:
            server.stop()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text + "\n", encoding="utf-8")
    log_path = args.output.with_suffix(".log")
    lines = [
        f"DSL {report['dsl']}",
        f"HTTP {report['base_url']}",
        f"scope {report['scope_id']}",
        "nodes " + " -> ".join(report["node_order"]),
    ]
    for turn in report["turns"]:
        lines.append("")
        lines.append(f"USER {turn['user']}")
        lines.append(f"SEARCH {turn['node_1003_http']['status']} {turn['node_1002_search_body']}")
        lines.append("SEARCH_RESPONSE " + json.dumps(turn["node_1003_http"]["response"], ensure_ascii=False))
        lines.append("ANSWER " + turn["node_1007_answer"])
        lines.append(f"REMEMBER {turn['node_1006_http']['status']} {turn['node_1005_remember_body']}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
