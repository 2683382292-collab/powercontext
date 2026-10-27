# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Capture the selected Pi session branch as immutable raw-event evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    TextEvidence,
)
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
_SOURCE_ID_PREFIX = "pi:"
_CHECKPOINT_VERSION = 1
_VISIBLE_TYPES = frozenset({"message", "custom_message", "compaction", "branch_summary"})
_IGNORED_BLOCK_TYPES = frozenset({"thinking", "image"})
_IGNORED_ROLES = frozenset({"thinking", "image"})


class PiSessionInput(BaseModel):
    """One complete Pi v3 JSONL session and the leaf that selects its branch."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, including its original record."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Pi event addressed only by its session id and event id."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @property
    def source_id(self) -> str:
        return self.name


class PiEventTextEvidenceProjection:
    """Project the full event text. Memory chunking must not truncate this Source."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class PiEventSourceAdapter:
    """Resolve a parsed Pi event into a captured Source and read that event back."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=pi_event_source_id(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    version="1",
    projections=(PiEventTextEvidenceProjection(),),
)


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build ``pi:{quoted session}:{quoted event}`` from the two identity fields only."""

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    source_id = f"{_SOURCE_ID_PREFIX}{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("pi event source_id must not exceed 256 characters")
    return source_id


def parse_pi_session(session: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return visible events on the selected leaf's ancestor chain, root first."""

    try:
        text = session.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session must be strict UTF-8") from error

    lines = _jsonl_lines(text)
    if not lines:
        raise ValueError("Pi session must start with a version 3 session header")
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ValueError("Pi session contains malformed JSON") from error
    session_id = _session_id(header)

    nodes: dict[str, dict[str, JsonValue]] = {}
    order: list[str] = []
    for line in lines[1:]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session contains malformed JSON") from error
        event_id, parent_id = _graph_identity(record)
        if event_id in nodes:
            raise ValueError("Pi event ids must be unique")
        if parent_id is not None and parent_id not in nodes:
            raise ValueError("Pi event parent is missing or creates a cycle")
        if not isinstance(record, dict):
            raise ValueError("Pi event must be a JSON object")
        nodes[event_id] = record
        order.append(event_id)

    leaf_id = session.leaf_id if session.leaf_id is not None else (order[-1] if order else None)
    if leaf_id is None or leaf_id not in nodes:
        raise ValueError("Pi session leaf is missing")

    events: list[PiEventInput] = []
    for record in _ancestor_chain(nodes, leaf_id):
        rendered = _visible_event(record)
        if rendered is None:
            continue
        role, body = rendered
        event_id = record["id"]
        parent_id = record["parentId"]
        if not isinstance(event_id, str) or (parent_id is not None and not isinstance(parent_id, str)):
            raise ValueError("Pi event identity is invalid")
        event_type = record["type"]
        if not isinstance(event_type, str):
            raise ValueError("Pi event type is invalid")
        pi_event_source_id(session_id, event_id)
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parent_id,
                event_type=event_type,
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiSessionConnector:
    """Import new visible events from one selected branch and persist that progress."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = path
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = Path(self.path).read_bytes()
        parsed = parse_pi_session(PiSessionInput(content=raw, leaf_id=self.leaf_id))
        records = _all_records(raw)
        imported = _checkpoint_events(session.checkpoint)
        for record in records:
            source_id = pi_event_source_id(_session_id_of(raw), record["id"])
            digest = _record_digest(record)
            previous = imported.get(source_id)
            if previous is not None and previous != digest:
                raise ValueError("imported Pi event was rewritten")

        updated = dict(imported)
        for event in parsed:
            source_id = pi_event_source_id(event.session_id, event.event_id)
            if source_id in updated:
                continue
            await session.submit(source_id, PI_EVENT_SOURCE_NAME, event)
            updated[source_id] = _record_digest(event.record)

        checkpoint: JsonValue = {"version": _CHECKPOINT_VERSION, "events": updated}
        if checkpoint == session.checkpoint:
            checkpoint = session.checkpoint
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)


def _jsonl_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if line.strip():
            lines.append(line)
    return lines


def _session_id(header: object) -> str:
    if not isinstance(header, dict) or header.get("type") != "session" or type(header.get("version")) is not int:
        raise ValueError("Pi session must start with a version 3 session header")
    if header.get("version") != 3:
        raise ValueError("Pi session version must be 3")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session id must be a non-empty string")
    return session_id


def _graph_identity(record: object) -> tuple[str, str | None]:
    if not isinstance(record, dict) or "id" not in record or "parentId" not in record:
        raise ValueError("Pi event must have an id and parentId")
    event_id = record["id"]
    parent_id = record["parentId"]
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Pi event id must be a non-empty string")
    if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
        raise ValueError("Pi event parentId must be null or a non-empty string")
    return event_id, parent_id


def _ancestor_chain(
    nodes: Mapping[str, dict[str, JsonValue]],
    leaf_id: str,
) -> tuple[dict[str, JsonValue], ...]:
    chain: list[dict[str, JsonValue]] = []
    seen: set[str] = set()
    cursor: str | None = leaf_id
    while cursor is not None:
        if cursor in seen or cursor not in nodes:
            raise ValueError("Pi session leaf ancestry is cyclic or incomplete")
        seen.add(cursor)
        record = nodes[cursor]
        chain.append(record)
        parent_id = record["parentId"]
        cursor = parent_id if isinstance(parent_id, str) else None
    chain.reverse()
    return tuple(chain)


def _visible_event(record: Mapping[str, JsonValue]) -> tuple[str, str] | None:
    event_type = record.get("type")
    if event_type not in _VISIBLE_TYPES or not isinstance(event_type, str):
        return None
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        return event_type, summary
    if event_type == "custom_message":
        body = _join_visible(record.get("content"))
        if body is None:
            return None
        return "custom", body
    message = record.get("message")
    if not isinstance(message, dict):
        raise ValueError("Pi message event must contain a message object")
    role = message.get("role")
    if not isinstance(role, str) or not role:
        raise ValueError("Pi message role must be a non-empty string")
    if role in _IGNORED_ROLES:
        return None
    if role == "toolResult":
        return role, _tool_result_body(message)
    if role == "bashExecution":
        return role, _bash_body(message)
    body = _join_visible(message.get("content"))
    if body is None:
        return None
    return role, body


def _tool_result_body(message: Mapping[str, JsonValue]) -> str:
    tool_name = message.get("toolName")
    tool_call_id = message.get("toolCallId")
    if not isinstance(tool_name, str) or not isinstance(tool_call_id, str):
        raise ValueError("Pi tool result must name its tool and call")
    text = _join_visible(message.get("content")) or ""
    return f"[tool_result {tool_name} {tool_call_id}] {text}"


def _bash_body(message: Mapping[str, JsonValue]) -> str:
    command = message.get("command")
    output = message.get("output")
    if not isinstance(command, str) or not isinstance(output, str):
        raise ValueError("Pi bash execution must include command and output strings")
    return f"$ {command}\n{output}"


def _join_visible(content: object) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content if content else None
    if not isinstance(content, list):
        raise ValueError("Pi event content must be a string or a list of blocks")
    pieces: list[str] = []
    for block in content:
        piece = _block_text(block)
        if piece is not None:
            pieces.append(piece)
    if not any(pieces):
        return None
    return "\n".join(pieces)


def _block_text(block: object) -> str | None:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        raise ValueError("Pi content block must be a string or object")
    block_type = block.get("type")
    if block_type in _IGNORED_BLOCK_TYPES:
        return None
    if block_type == "text":
        text = block.get("text", "")
        if not isinstance(text, str):
            raise ValueError("Pi text block must contain a string")
        return text
    if block_type == "toolCall":
        name = block.get("name")
        call_id = block.get("id")
        if not isinstance(name, str) or not isinstance(call_id, str):
            raise ValueError("Pi tool call must include a name and id")
        arguments = block.get("arguments", {})
        return f"[tool_call {name} {call_id}] {_compact_json(arguments)}"
    return None


def _compact_json(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("Pi tool call arguments must be JSON") from error


def _record_digest(record: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _all_records(raw: bytes) -> tuple[dict[str, JsonValue], ...]:
    text = raw.decode("utf-8-sig")
    records: list[dict[str, JsonValue]] = []
    for line in _jsonl_lines(text)[1:]:
        record = json.loads(line)
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records.append(record)
    return tuple(records)


def _session_id_of(raw: bytes) -> str:
    text = raw.decode("utf-8-sig")
    lines = _jsonl_lines(text)
    return _session_id(json.loads(lines[0]))


def _checkpoint_events(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Pi session checkpoint is invalid")
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        raise ValueError("Pi session checkpoint is invalid")
    imported: dict[str, str] = {}
    for source_id, digest in events.items():
        if not isinstance(source_id, str) or not isinstance(digest, str):
            raise ValueError("Pi session checkpoint is invalid")
        imported[source_id] = digest
    return imported


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]