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

"""Propagate Source availability through a scope-local evidence graph."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.topic_memory import MAX_TOPIC_MEMORY_SEARCH_LIMIT
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

_PURPOSES = frozenset({"generation", "audit"})
_KIND = Literal["source", "memory", "topic"]


class SourceLifecycleError(ValueError):
    """A source-lifecycle registration or event violated its contract."""


class SourceLifecycleEvent(BaseModel):
    """One availability change for an already registered Source."""

    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str
    revision: int = Field(ge=1)
    sequence: int = Field(ge=1)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must contain non-whitespace text")
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def require_positive(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("must be a positive integer")
        return value


@dataclass(frozen=True)
class _Node:
    node_id: str
    kind: _KIND
    ordinal: int
    policy: str | None
    parents: tuple[str, ...]
    revision: int | None
    reference_json: str
    available: bool
    event_sequence: int | None
    event_available: bool | None
    event_reason: str | None

    def reference(self) -> SourceRef | MemoryCitation | ArtifactRef:
        payload = json.loads(self.reference_json)
        if self.kind == "source":
            return SourceRef.model_validate(payload)
        if self.kind == "memory":
            return MemoryCitation.model_validate(payload)
        return ArtifactRef.model_validate(payload)

    @property
    def entry_id(self) -> str:
        return str(json.loads(self.reference_json)["entry_id"])

    @property
    def entry_version_id(self) -> str:
        return str(json.loads(self.reference_json)["entry_version_id"])

    @property
    def artifact_id(self) -> str:
        return str(json.loads(self.reference_json)["artifact_id"])


class SourceLifecycleApplication:
    """Open a scope-local lifecycle service and hide withdrawn Topic projections."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime
        self._install_topic_filter()

    def for_scope(self, scope_id: str, /) -> SourceLifecycleService:
        return SourceLifecycleService(self._runtime, validate_scope_id(scope_id))

    def _install_topic_filter(self) -> None:
        current = self._runtime._topic_memory_search
        if current is None or getattr(current, "_source_lifecycle_filter", False):
            return

        async def filtering_search(scope_id: str, query: str, /, **kwargs: object) -> object:
            hidden = await _Store(self._runtime).hidden_topic_ids(scope_id)
            requested = kwargs.get("limit")
            if hidden and isinstance(requested, int) and requested < MAX_TOPIC_MEMORY_SEARCH_LIMIT:
                kwargs = {**kwargs, "limit": MAX_TOPIC_MEMORY_SEARCH_LIMIT}
            result = await current(scope_id, query, **kwargs)
            if not hidden:
                return result
            hits = tuple(hit for hit in result.hits if hit.artifact_ref.artifact_id not in hidden)
            if isinstance(requested, int):
                hits = hits[:requested]
            if hits == tuple(result.hits):
                return result
            return result.model_copy(update={"hits": hits})

        filtering_search._source_lifecycle_filter = True  # type: ignore[attr-defined]
        self._runtime._topic_memory_search = filtering_search


class SourceLifecycleService:
    """Register one scope's evidence graph and apply Source availability events."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id
        self._store = _Store(runtime)

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        node_id = _node_id(node_id)
        revision = _positive(revision, "revision")
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source reference is not readable")
        async with self._runtime._context(self.scope_id):
            async with self._runtime._locked(self.scope_id):
                await self._require_source(source_ref)
                nodes = await self._store.load(self.scope_id)
                if _idempotent(nodes, node_id, "source", (), None, source_ref, revision):
                    return
                _reject_duplicate(nodes, node_id)
                await self._store.insert(
                    self.scope_id,
                    _Node(
                        node_id=node_id,
                        kind="source",
                        ordinal=_next_ordinal(nodes),
                        policy=None,
                        parents=(),
                        revision=revision,
                        reference_json=_dump(source_ref),
                        available=True,
                        event_sequence=None,
                        event_available=None,
                        event_reason=None,
                    ),
                )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        await self._register_derived(node_id, citation, parents, policy, "memory")

    async def register_topic(
        self,
        node_id: str,
        reference: ArtifactRef,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        await self._register_derived(node_id, reference, parents, policy, "topic")

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")
        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            async with self._runtime._locked(self.scope_id):
                nodes = await self._store.load(self.scope_id)
                source = _require_source_node(nodes, event)
                if source.event_sequence is not None and event.sequence < source.event_sequence:
                    raise SourceLifecycleError("event sequence is stale")
                if source.event_sequence is not None and event.sequence == source.event_sequence:
                    if event.available == source.event_available and event.reason == source.event_reason:
                        return _active_ids(nodes)
                    raise SourceLifecycleError("event sequence was already used")
                updated = _propagate(nodes, event.node_id, event.available)
                await _align_memories(context, updated, event.reason)
                await self._store.save_propagation(self.scope_id, updated, event)
                return _active_ids(updated)

    async def resolve(self, node_id: str, /, *, purpose: str) -> SourceRef | MemoryCitation | ArtifactRef:
        node_id = _node_id(node_id)
        if purpose not in _PURPOSES:
            raise SourceLifecycleError("unknown lifecycle purpose")
        async with self._runtime._context(self.scope_id):
            nodes = await self._store.load(self.scope_id)
        node = next((item for item in nodes if item.node_id == node_id), None)
        if node is None:
            raise SourceLifecycleError("unknown lifecycle node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("node is not available for generation")
        return node.reference()

    async def _register_derived(
        self,
        node_id: str,
        reference: MemoryCitation | ArtifactRef,
        parents: Sequence[str],
        policy: str,
        kind: _KIND,
    ) -> None:
        node_id = _node_id(node_id)
        parent_ids = _parent_ids(parents)
        selected = _policy(policy)
        if kind == "memory" and not isinstance(reference, MemoryCitation):
            raise SourceLifecycleError("memory citation is not readable")
        if kind == "topic" and not isinstance(reference, ArtifactRef):
            raise SourceLifecycleError("topic reference is not readable")
        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            async with self._runtime._locked(self.scope_id):
                if kind == "memory":
                    await _require_memory(context, reference)
                else:
                    await self._require_topic(reference)
                nodes = await self._store.load(self.scope_id)
                if _idempotent(nodes, node_id, kind, parent_ids, selected, reference, None):
                    return
                _reject_duplicate(nodes, node_id)
                _validate_parents(node_id, parent_ids, nodes)
                available = _supported(nodes, parent_ids, selected)
                if kind == "memory" and not available:
                    await _set_entry_state(context, reference, active=False, reason="source lifecycle")
                await self._store.insert(
                    self.scope_id,
                    _Node(
                        node_id=node_id,
                        kind=kind,
                        ordinal=_next_ordinal(nodes),
                        policy=selected,
                        parents=parent_ids,
                        revision=None,
                        reference_json=_dump(reference),
                        available=available,
                        event_sequence=None,
                        event_available=None,
                        event_reason=None,
                    ),
                )

    async def _require_source(self, source_ref: SourceRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type,
                source_ref.source_id,
            )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("source reference is not readable") from error

    async def _require_topic(self, reference: ArtifactRef) -> None:
        if reference.family != "topic-memory":
            raise SourceLifecycleError("topic reference is not readable")
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                reference.family,
                reference.artifact_id,
                reference.revision,
            )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("topic reference is not readable") from error


class _Store:
    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    async def load(self, scope_id: str) -> tuple[_Node, ...]:
        async with self._database().transaction() as connection:
            await _ensure_schema(connection)
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT node_id, kind, ordinal, policy, parents_json, lifecycle_revision,
                               reference_json, available, event_sequence, event_available, event_reason
                        FROM pc_source_lifecycle_nodes
                        WHERE scope_id = :scope_id
                        ORDER BY ordinal
                        """
                    ),
                    {"scope_id": scope_id},
                )
            ).mappings()
        return tuple(_node_from_row(row) for row in rows)

    async def insert(self, scope_id: str, node: _Node) -> None:
        async with self._database().transaction() as connection:
            await _ensure_schema(connection)
            await connection.execute(
                text(
                    """
                    INSERT INTO pc_source_lifecycle_nodes (
                        scope_id, node_id, kind, ordinal, policy, parents_json, lifecycle_revision,
                        reference_json, available, event_sequence, event_available, event_reason
                    ) VALUES (
                        :scope_id, :node_id, :kind, :ordinal, :policy, :parents_json, :lifecycle_revision,
                        :reference_json, :available, :event_sequence, :event_available, :event_reason
                    )
                    """
                ),
                _node_params(scope_id, node),
            )

    async def save_propagation(
        self,
        scope_id: str,
        nodes: tuple[_Node, ...],
        event: SourceLifecycleEvent,
    ) -> None:
        async with self._database().transaction() as connection:
            await _ensure_schema(connection)
            for node in nodes:
                await connection.execute(
                    text(
                        """
                        UPDATE pc_source_lifecycle_nodes
                        SET available = :available,
                            event_sequence = :event_sequence,
                            event_available = :event_available,
                            event_reason = :event_reason
                        WHERE scope_id = :scope_id AND node_id = :node_id
                        """
                    ),
                    _node_params(scope_id, node, event if node.node_id == event.node_id else None),
                )

    async def hidden_topic_ids(self, scope_id: str) -> set[str]:
        async with self._database().transaction() as connection:
            await _ensure_schema(connection)
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT reference_json
                        FROM pc_source_lifecycle_nodes
                        WHERE scope_id = :scope_id AND kind = 'topic' AND available = 0
                        """
                    ),
                    {"scope_id": scope_id},
                )
            ).mappings()
        return {str(json.loads(str(row["reference_json"]))["artifact_id"]) for row in rows}

    def _database(self):
        database = getattr(self._runtime._provider, "database", None)
        if database is None:
            raise SourceLifecycleError("lifecycle storage is unavailable")
        return database


def _node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("node id must contain non-whitespace text")
    return value


def _positive(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError(f"{label} must be a positive integer")
    return value


def _policy(value: str) -> str:
    if value not in {"all", "any"}:
        raise SourceLifecycleError("policy must be all or any")
    return value


def _parent_ids(parents: Sequence[str]) -> tuple[str, ...]:
    if isinstance(parents, str) or not isinstance(parents, Sequence):
        raise SourceLifecycleError("parents must be a sequence of node ids")
    values = tuple(parents)
    if not values:
        raise SourceLifecycleError("a derived node needs a parent")
    if any(not isinstance(parent, str) or not parent.strip() for parent in values):
        raise SourceLifecycleError("parent id must contain non-whitespace text")
    if len(values) != len(set(values)):
        raise SourceLifecycleError("parent nodes must be unique")
    return values


def _dump(value: SourceRef | MemoryCitation | ArtifactRef) -> str:
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _next_ordinal(nodes: tuple[_Node, ...]) -> int:
    if not nodes:
        return 1
    return max(node.ordinal for node in nodes) + 1


def _reject_duplicate(nodes: tuple[_Node, ...], node_id: str) -> None:
    if any(node.node_id == node_id for node in nodes):
        raise SourceLifecycleError("node binding conflicts with an existing registration")


def _idempotent(
    nodes: tuple[_Node, ...],
    node_id: str,
    kind: _KIND,
    parents: tuple[str, ...],
    policy: str | None,
    reference: SourceRef | MemoryCitation | ArtifactRef,
    revision: int | None,
) -> bool:
    existing = next((node for node in nodes if node.node_id == node_id), None)
    if existing is None:
        return False
    same = (
        existing.kind == kind
        and existing.parents == parents
        and existing.policy == policy
        and existing.revision == revision
        and existing.reference_json == _dump(reference)
    )
    if not same:
        raise SourceLifecycleError("node binding conflicts with an existing registration")
    return True


def _validate_parents(node_id: str, parents: tuple[str, ...], nodes: tuple[_Node, ...]) -> None:
    if node_id in parents:
        raise SourceLifecycleError("dependency graph contains a cycle")
    by_id = {node.node_id: node for node in nodes}
    missing = [parent for parent in parents if parent not in by_id]
    if missing:
        raise SourceLifecycleError("parent node is not registered")
    seen: set[str] = set()
    stack = list(parents)
    while stack:
        current = stack.pop()
        if current == node_id:
            raise SourceLifecycleError("dependency graph contains a cycle")
        if current in seen:
            continue
        seen.add(current)
        stack.extend(by_id[current].parents)


def _supported(nodes: tuple[_Node, ...], parents: tuple[str, ...], policy: str) -> bool:
    by_id = {node.node_id: node for node in nodes}
    flags = [by_id[parent].available for parent in parents]
    return all(flags) if policy == "all" else any(flags)


def _require_source_node(nodes: tuple[_Node, ...], event: SourceLifecycleEvent) -> _Node:
    node = next((item for item in nodes if item.node_id == event.node_id), None)
    if node is None or node.kind != "source":
        raise SourceLifecycleError("event does not match a registered source")
    if node.revision != event.revision:
        raise SourceLifecycleError("event revision does not match the source lifecycle revision")
    return node


def _propagate(nodes: tuple[_Node, ...], source_id: str, available: bool) -> tuple[_Node, ...]:
    active: set[str] = set()
    updated: list[_Node] = []
    for node in nodes:
        if node.kind == "source":
            flag = available if node.node_id == source_id else node.available
        else:
            flags = [parent in active for parent in node.parents]
            flag = all(flags) if node.policy == "all" else any(flags)
        if flag:
            active.add(node.node_id)
        updated.append(replace(node, available=flag))
    return tuple(updated)


def _active_ids(nodes: tuple[_Node, ...]) -> list[str]:
    return sorted(node.node_id for node in nodes if node.available)


async def _require_memory(context: object, citation: MemoryCitation) -> None:
    try:
        service = context.artifacts.memory
        memory = await service.revision(citation.memory_ref)
    except SourceLifecycleError:
        raise
    except Exception as error:
        raise SourceLifecycleError("memory citation is not readable") from error
    found = any(
        item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id
        for item in memory.content.manifest.entries
    )
    if not found:
        raise SourceLifecycleError("memory citation is not readable")


async def _align_memories(context: object, nodes: tuple[_Node, ...], reason: str) -> None:
    memories = [node for node in nodes if node.kind == "memory"]
    if not memories:
        return
    service = context.artifacts.memory
    current = await service.head(context.artifacts.memory_artifact_id)
    for node in memories:
        manifest = {item.entry_id: item for item in current.content.manifest.entries}
        item = manifest.get(node.entry_id)
        if item is None or item.entry_version_id != node.entry_version_id:
            raise SourceLifecycleError("registered memory entry is not current")
        desired = "active" if node.available else "inactive"
        if item.state == desired:
            continue
        entry = next(entry for entry in await service.entries(current) if entry.entry_id == node.entry_id)
        if desired == "active":
            current = await service.reactivate(current, entries=(entry,), reason=reason)
        else:
            current = await service.forget(current, entries=(entry,), reason=reason)


async def _set_entry_state(context: object, citation: MemoryCitation, *, active: bool, reason: str) -> None:
    service = context.artifacts.memory
    current = await service.head(context.artifacts.memory_artifact_id)
    manifest = {item.entry_id: item for item in current.content.manifest.entries}
    item = manifest.get(citation.entry_id)
    if item is None or item.entry_version_id != citation.entry_version_id:
        raise SourceLifecycleError("memory citation is not readable")
    desired = "active" if active else "inactive"
    if item.state == desired:
        return
    entry = next(entry for entry in await service.entries(current) if entry.entry_id == citation.entry_id)
    if active:
        await service.reactivate(current, entries=(entry,), reason=reason)
    else:
        await service.forget(current, entries=(entry,), reason=reason)


def _node_from_row(row: object) -> _Node:
    sequence = row["event_sequence"]
    event_available = row["event_available"]
    revision = row["lifecycle_revision"]
    return _Node(
        node_id=str(row["node_id"]),
        kind=row["kind"],
        ordinal=int(row["ordinal"]),
        policy=None if row["policy"] is None else str(row["policy"]),
        parents=tuple(json.loads(str(row["parents_json"]))),
        revision=None if revision is None else int(revision),
        reference_json=str(row["reference_json"]),
        available=bool(row["available"]),
        event_sequence=None if sequence is None else int(sequence),
        event_available=None if event_available is None else bool(event_available),
        event_reason=None if row["event_reason"] is None else str(row["event_reason"]),
    )


def _node_params(scope_id: str, node: _Node, event: SourceLifecycleEvent | None = None) -> dict[str, object]:
    sequence = node.event_sequence if event is None else event.sequence
    available_event = node.event_available if event is None else event.available
    reason = node.event_reason if event is None else event.reason
    return {
        "scope_id": scope_id,
        "node_id": node.node_id,
        "kind": node.kind,
        "ordinal": node.ordinal,
        "policy": node.policy,
        "parents_json": json.dumps(list(node.parents), separators=(",", ":")),
        "lifecycle_revision": node.revision,
        "reference_json": node.reference_json,
        "available": int(node.available),
        "event_sequence": sequence,
        "event_available": None if available_event is None else int(available_event),
        "event_reason": reason,
    }


async def _ensure_schema(connection: object) -> None:
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS pc_source_lifecycle_nodes (
                scope_id VARCHAR(256) NOT NULL,
                node_id VARCHAR(256) NOT NULL,
                kind VARCHAR(16) NOT NULL,
                ordinal INTEGER NOT NULL,
                policy VARCHAR(8) NULL,
                parents_json TEXT NOT NULL,
                lifecycle_revision INTEGER NULL,
                reference_json TEXT NOT NULL,
                available INTEGER NOT NULL,
                event_sequence INTEGER NULL,
                event_available INTEGER NULL,
                event_reason TEXT NULL,
                PRIMARY KEY (scope_id, node_id)
            )
            """
        )
    )


__all__ = ["SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent", "SourceLifecycleService"]