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

"""Score Memory candidates with an HTTP reranker, across batches, without changing identity."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

_DEFAULT_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Call one offline HTTP scoring service and select by the pooled scores."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = _DEFAULT_POLICY_ID,
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty string")
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        self.endpoint = endpoint
        self.policy_id = policy_id
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every original rank, then keep the top limit under a stable order."""

        if len(candidates) < 1:
            raise ValueError("rerank requires at least one candidate")
        if type(limit) is not int or limit < 1 or limit > len(candidates):
            raise ValueError("rerank limit must be between one and the candidate count")

        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = tuple(
                {"rank": rank, "text": candidate.text}
                for rank, candidate in enumerate(candidates[start : start + self._batch_size], start=start + 1)
            )
            response = await self._client.post(self.endpoint, json={"query": query, "candidates": list(batch)})
            requests += 1
            response.raise_for_status()
            scores.update(_scores_for_batch(response, batch))

        selected = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank)))[:limit]
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=requests),
        )


def _scores_for_batch(response: httpx.Response, batch: Sequence[Mapping[str, object]]) -> dict[int, float]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("reranker response is not valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain only scores")
    raw_scores = payload["scores"]
    if not isinstance(raw_scores, list):
        raise ValueError("reranker scores must be a list")
    expected = {int(item["rank"]) for item in batch}
    parsed: dict[int, float] = {}
    for item in raw_scores:
        if not isinstance(item, dict) or set(item) != {"rank", "score"}:
            raise ValueError("each reranker score must contain only rank and score")
        rank = item["rank"]
        score = item["score"]
        if type(rank) is not int or rank not in expected or rank in parsed:
            raise ValueError("reranker ranks must cover this batch exactly once")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("reranker score must be a finite number")
        parsed[rank] = float(score)
    if set(parsed) != expected:
        raise ValueError("reranker ranks must cover this batch exactly once")
    return parsed


__all__ = ["HTTPMemoryReranker"]