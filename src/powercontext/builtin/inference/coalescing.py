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

"""Share identical embedding texts across callers and reuse successful vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from typing import TypeVar

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

_ModelT = TypeVar("_ModelT")
_Wait = asyncio.Future[tuple[EmbeddingVector, InferenceUsage]]


class CoalescingEmbeddingModel:
    """Batch identical texts across callers and cache vectors that succeeded."""

    def __init__(
        self,
        model: _ModelT,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number of milliseconds")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: dict[str, _Wait] = {}
        self._inflight: dict[str, _Wait] = {}
        self._flush_task: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, including duplicates, in input order."""

        if isinstance(texts, str) or not isinstance(texts, Sequence):
            raise TypeError("texts must be a sequence of strings")
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        loop = asyncio.get_running_loop()
        slots: list[tuple[EmbeddingVector | None, _Wait | None]] = []
        for text in texts:
            cached = self._cache_get(text)
            if cached is not None:
                slots.append((cached, None))
                continue
            waiter = self._inflight.get(text)
            if waiter is None:
                waiter = self._pending.get(text)
            if waiter is None:
                waiter = loop.create_future()
                self._pending[text] = waiter
            slots.append((None, waiter))
        if self._pending:
            self._ensure_flush()

        vectors: list[EmbeddingVector] = []
        usages: list[InferenceUsage] = []
        seen_usage: set[int] = set()
        for cached, waiter in slots:
            if waiter is None:
                if cached is None:
                    raise ValueError("cached embedding is missing")
                vectors.append(cached)
                continue
            # Shield the shared future so cancelling this caller leaves the batch running.
            vector, usage = await asyncio.shield(waiter)
            vectors.append(vector)
            marker = id(usage)
            if marker not in seen_usage:
                seen_usage.add(marker)
                usages.append(usage)
        return EmbeddingResult(vectors=tuple(vectors), usage=_merge_usage(usages))

    def _ensure_flush(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._flush(), name="embedding-coalesce")

    async def _flush(self) -> None:
        try:
            await asyncio.sleep(self._coalesce_seconds)
            while self._pending:
                batch = list(self._pending.items())[: self._max_batch_size]
                for text, _waiter in batch:
                    del self._pending[text]
                await self._embed_batch(batch)
        finally:
            self._flush_task = None
            if self._pending:
                self._ensure_flush()

    async def _embed_batch(self, batch: list[tuple[str, _Wait]]) -> None:
        texts = tuple(text for text, _waiter in batch)
        for text, waiter in batch:
            self._inflight[text] = waiter
        try:
            result = await self._model.embed(texts)
        except Exception as error:
            self._fail(batch, error)
            return
        if len(result.vectors) != len(texts):
            self._fail(batch, ValueError("embedding vector count does not match the batch"))
            return
        for (text, waiter), vector in zip(batch, result.vectors, strict=True):
            stored = tuple(vector)
            self._cache_put(text, stored)
            self._inflight.pop(text, None)
            if not waiter.done():
                waiter.set_result((stored, result.usage))

    def _fail(self, batch: list[tuple[str, _Wait]], error: Exception) -> None:
        for text, waiter in batch:
            self._inflight.pop(text, None)
            if not waiter.done():
                waiter.set_exception(error)

    def _cache_get(self, text: str) -> EmbeddingVector | None:
        if self._cache_size <= 0 or text not in self._cache:
            return None
        self._cache.move_to_end(text)
        return self._cache[text]

    def _cache_put(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _merge_usage(usages: list[InferenceUsage]) -> InferenceUsage:
    if not usages:
        return InferenceUsage(requests=0)
    input_tokens = None
    output_tokens = None
    if any(usage.input_tokens is not None for usage in usages):
        input_tokens = sum(usage.input_tokens or 0 for usage in usages)
    if any(usage.output_tokens is not None for usage in usages):
        output_tokens = sum(usage.output_tokens or 0 for usage in usages)
    return InferenceUsage(
        requests=sum(usage.requests for usage in usages),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


__all__ = ["CoalescingEmbeddingModel"]