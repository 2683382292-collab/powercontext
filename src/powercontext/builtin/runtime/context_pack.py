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

"""Incremental context packs: chunk, checksum, and rebuild without shared objects."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from powercontext.errors import PowerContextError

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(PowerContextError):
    """Raised when a pack or delta fails the wire protocol."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete, self-checking context payload."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a pack from original chunks and the canonical wire byte count."""

        normalized = _require_chunks(items)
        encoded = [_encode_chunk(item) for item in normalized]
        digest = _digest(encoded)
        _, byte_count = _pack_wire(encoded, digest)
        return cls(items=normalized, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        """Split text into deterministic UTF-8 chunks that rejoin to the original."""

        if not isinstance(content, str):
            raise ContextPackError("content must be text")
        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")
        chunks: list[bytes] = []
        current = bytearray()
        for character in content:
            try:
                encoded = character.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ContextPackError("content is not valid Unicode") from error
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot hold the next UTF-8 character")
            if current and len(current) + len(encoded) > chunk_bytes:
                chunks.append(bytes(current))
                current.clear()
            current.extend(encoded)
        if current:
            chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        """Return the canonical pack JSON, including its own byte count."""

        encoded = [_encode_chunk(item) for item in self.items]
        wire, byte_count = _pack_wire(encoded, self.digest)
        if byte_count != self.byte_count or _digest(encoded) != self.digest:
            raise ContextPackError("pack digest or byte count is invalid")
        return wire

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode one canonical pack and reject a bad digest, length, or schema."""

        payload = _load_json(data)
        if set(payload) != _PACK_FIELDS or payload.get("schema") != PACK_SCHEMA:
            raise ContextPackError("pack fields are invalid")
        items = _decode_chunks(payload.get("items"))
        digest = payload.get("digest")
        byte_count = payload.get("byte_count")
        if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
            raise ContextPackError("pack digest is invalid")
        if type(byte_count) is not int or byte_count < 0:
            raise ContextPackError("pack byte count is invalid")
        pack = cls.build(items)
        if pack.digest != digest or pack.byte_count != byte_count or pack.to_bytes() != data:
            raise ContextPackError("pack digest or byte count is invalid")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A prefix length plus the target suffix needed to rebuild one pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        appended = tuple(self.appended)
        if any(type(item) is not bytes for item in appended):
            raise ContextPackError("appended items must be bytes")
        object.__setattr__(self, "appended", appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Keep the longest shared chunk prefix and send the remaining target chunks."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta endpoints must be packs")
        prefix = 0
        limit = min(len(base.items), len(target.items))
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        """Return the canonical delta JSON. The byte count stays off the wire."""

        payload = {
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_chunk(item) for item in self.appended],
        }
        return _dumps(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode a delta. Equivalent JSON is accepted; the fields are strict."""

        payload = _load_json(data)
        if set(payload) != _DELTA_FIELDS or payload.get("schema") != DELTA_SCHEMA:
            raise ContextPackError("delta fields are invalid")
        base_digest = payload.get("base_digest")
        target_digest = payload.get("target_digest")
        prefix_items = payload.get("prefix_items")
        if not isinstance(base_digest, str) or _DIGEST_RE.fullmatch(base_digest) is None:
            raise ContextPackError("base digest is invalid")
        if not isinstance(target_digest, str) or _DIGEST_RE.fullmatch(target_digest) is None:
            raise ContextPackError("target digest is invalid")
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        return cls(base_digest, target_digest, prefix_items, _decode_chunks(payload.get("appended")))

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a serialized base and a serialized delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta inputs are invalid")
    if base.digest != delta.base_digest:
        raise ContextPackError("base digest does not match")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix exceeds the base pack")
    rebuilt = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("target digest does not match")
    return rebuilt


def _require_chunks(items: tuple[bytes, ...] | Sequence[bytes]) -> tuple[bytes, ...]:
    if isinstance(items, str) or isinstance(items, bytes) or not isinstance(items, Sequence):
        raise ContextPackError("pack items must be a sequence of bytes")
    normalized = tuple(items)
    if any(type(item) is not bytes for item in normalized):
        raise ContextPackError("pack items must be bytes")
    return normalized


def _encode_chunk(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _decode_chunks(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("chunks must be a list of base64 strings")
    chunks: list[bytes] = []
    for item in value:
        if not isinstance(item, str):
            raise ContextPackError("chunks must be a list of base64 strings")
        try:
            chunks.append(base64.b64decode(item, validate=True))
        except ValueError as error:
            raise ContextPackError("chunk base64 is invalid") from error
    return tuple(chunks)


def _digest(encoded_items: Sequence[str]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": list(encoded_items)}
    return "sha256:" + hashlib.sha256(_dumps(unsigned)).hexdigest()


def _pack_wire(encoded_items: Sequence[str], digest: str) -> tuple[bytes, int]:
    items = list(encoded_items)
    skeleton = _dumps({"digest": digest, "items": items, "schema": PACK_SCHEMA})
    prefix = b'{"byte_count":'
    suffix = b"," + skeleton[1:]
    width = 1
    for _ in range(12):
        byte_count = len(prefix) + width + len(suffix)
        if len(str(byte_count)) != width:
            width = len(str(byte_count))
            continue
        wire = _dumps(
            {
                "byte_count": byte_count,
                "digest": digest,
                "items": items,
                "schema": PACK_SCHEMA,
            }
        )
        if len(wire) != byte_count:
            raise ContextPackError("pack byte count is inconsistent")
        return wire, byte_count
    raise ContextPackError("pack byte count is inconsistent")


def _dumps(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_json(data: bytes) -> dict[str, object]:
    if not isinstance(data, bytes):
        raise ContextPackError("wire payload must be bytes")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, ValueError) as error:
        raise ContextPackError("wire payload is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError("wire payload must be an object")
    return payload


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]