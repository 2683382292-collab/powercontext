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

"""Choose literal evidence spans from retrieved memory text for one query.

Every returned interval is a half-open character span of one input document. The
caller decides whether a set of spans fits by measuring the fully rendered context.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence

_STOP = frozenset(
    """
    a an the of and or to in for on with by from as at into over after before between
    without within than then that this these those it its their his her they them we
    our you your is are was were be been being not no nor but if so such too very can
    may might must should could would will just also about across among per via using
    used use which who whom whose what when where why how there here all any each few
    more most other some only same do does did done having have has
    """.split()
)
_ABBREV = frozenset(
    """
    fig figs al vs dr mr mrs prof eq eqs cf ca pp vol no nos ed eds eg ie etc st jr sr
    mt approx dept inc ltd ref refs
    """.split()
)
_TOKEN = re.compile(r"[a-z]+|\d+|[\u4e00-\u9fff]")
_STEM_RULES = (
    ("ational", "ate"),
    ("tional", "tion"),
    ("enci", "ence"),
    ("anci", "ance"),
    ("izer", "ize"),
    ("ization", "ize"),
    ("isation", "ize"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iviti", "ive"),
    ("biliti", "ble"),
    ("ation", "ate"),
    ("ator", "ate"),
    ("alism", "al"),
    ("icate", "ic"),
    ("ative", ""),
    ("alize", "al"),
    ("iciti", "ic"),
    ("ical", "ic"),
    ("ness", ""),
    ("ment", ""),
    ("ingly", ""),
    ("edly", ""),
    ("ing", ""),
    ("ies", "y"),
    ("ied", "y"),
    ("ed", ""),
    ("ly", ""),
    ("es", ""),
    ("s", ""),
)
_BACK_SENTENCES = 1
_FORWARD_SENTENCES = 3
_MAX_FRAGMENTS = 3


def select_evidence_spans(
    query: str,
    documents: Sequence[str],
    *,
    fits: Callable[[Sequence[tuple[int, int, int]]], bool],
) -> tuple[tuple[int, int, int], ...]:
    """Return ``(document_index, start, end)`` spans that ``fits`` accepts.

    The best lexical sentence in each document is expanded to the preceding sentence
    and the following sentences, then shrunk from the front until the rendered
    context fits. Spans do not overlap, and they stay in relevance order.
    """

    if not documents:
        return ()
    sentence_bounds = [_split_sentences(text) for text in documents]
    sentence_terms: list[list[list[str]]] = []
    document_frequency: Counter[str] = Counter()
    for doc_index, bounds in enumerate(sentence_bounds):
        rows: list[list[str]] = []
        for start, end in bounds:
            terms = _content_terms(documents[doc_index][start:end])
            rows.append(terms)
            document_frequency.update(set(terms))
        sentence_terms.append(rows)
    sentence_count = max(1, sum(len(bounds) for bounds in sentence_bounds))

    def inverse_frequency(term: str) -> float:
        return math.log((sentence_count + 1) / (document_frequency[term] + 0.5)) + 1.0

    query_weights: dict[str, float] = {}
    for term in _content_terms(query) or _TOKEN.findall(query.lower()):
        query_weights[term] = query_weights.get(term, 0.0) + inverse_frequency(term)
    weight_total = sum(query_weights.values()) or 1.0

    windows: list[tuple[float, int, int, frozenset[str]]] = []
    for doc_index, bounds in enumerate(sentence_bounds):
        ranked: list[tuple[float, int, frozenset[str]]] = []
        for sentence_index, terms in enumerate(sentence_terms[doc_index]):
            covered = {term: weight for term, weight in query_weights.items() if term in terms}
            coverage = sum(covered.values()) / weight_total
            if coverage <= 0 and query_weights:
                continue
            frequencies = Counter(terms)
            length_terms = max(1, len(terms))
            bm25 = 0.0
            for term, weight in query_weights.items():
                frequency = frequencies[term]
                if frequency:
                    denominator = frequency + 1.2 * (0.25 + 0.75 * length_terms / 18)
                    bm25 += weight * (frequency * 2.2) / denominator
            position = (sentence_index + 0.5) / max(1, len(bounds))
            score = coverage * 12 + bm25 * 0.04 + 0.5 * position
            ranked.append((score, sentence_index, frozenset(covered)))
        if not ranked:
            continue
        ranked.sort(key=lambda item: (-item[0], -item[1]))
        score, center, covered_terms = ranked[0]
        windows.append((score, doc_index, center, covered_terms))
    windows.sort(key=lambda item: -item[0])

    chosen: list[tuple[int, int, int]] = []
    covered_terms: set[str] = set()
    best_score = windows[0][0] if windows else 0.0
    for score, doc_index, center, terms in windows:
        novel = len(terms - covered_terms) / max(1, len(query_weights))
        if chosen and novel < 0.05 and score < best_score * 0.75:
            continue
        span = _fit_window(sentence_bounds[doc_index], doc_index, center, chosen, fits)
        if span is None:
            continue
        chosen.append(span)
        covered_terms.update(terms)
        if len(chosen) >= _MAX_FRAGMENTS:
            break
    if chosen:
        return tuple(chosen)
    return _fallback_span(documents, sentence_bounds, fits)


def _fit_window(
    bounds: Sequence[tuple[int, int]],
    doc_index: int,
    center: int,
    base: Sequence[tuple[int, int, int]],
    fits: Callable[[Sequence[tuple[int, int, int]]], bool],
) -> tuple[int, int, int] | None:
    """Keep the center sentence and as many neighbors as the rendered budget allows."""

    low = max(0, center - _BACK_SENTENCES)
    high = min(len(bounds), center + 1 + _FORWARD_SENTENCES)
    while high > low:
        start = bounds[low][0]
        end = bounds[high - 1][1]
        if fits((*base, (doc_index, start, end))):
            return (doc_index, start, end)
        if low < center:
            low += 1
        elif high - 1 > center:
            high -= 1
        else:
            return None
    return None


def _fallback_span(
    documents: Sequence[str],
    sentences: Sequence[Sequence[tuple[int, int]]],
    fits: Callable[[Sequence[tuple[int, int, int]]], bool],
) -> tuple[tuple[int, int, int], ...]:
    """Keep one literal prefix when no scored window fits the rendered budget."""

    for doc_index, spans in enumerate(sentences):
        regions = spans or ((0, len(documents[doc_index])),)
        for start, end in regions:
            if start >= end:
                continue
            fitted = _largest_fitting_prefix(doc_index, start, end, fits)
            if fitted is not None:
                return (fitted,)
    if documents and documents[0]:
        fitted = _largest_fitting_prefix(0, 0, len(documents[0]), fits)
        if fitted is not None:
            return (fitted,)
    return ()


def _largest_fitting_prefix(
    doc_index: int,
    start: int,
    end: int,
    fits: Callable[[Sequence[tuple[int, int, int]]], bool],
) -> tuple[int, int, int] | None:
    if end <= start or not fits(((doc_index, start, start + 1),)):
        return None
    low = start + 1
    high = end
    best = low
    while low <= high:
        mid = (low + high) // 2
        if fits(((doc_index, start, mid),)):
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return (doc_index, start, best)


def _split_sentences(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        boundary = character in ".!?\u3002\uff01\uff1f"
        if boundary and index + 1 < length and text[index + 1].isspace():
            word_start = index - 1
            while word_start >= start and (text[word_start].isalpha() or text[word_start] == "."):
                word_start -= 1
            word = text[word_start + 1 : index].lower().strip(".")
            cursor = index + 1
            while cursor < length and text[cursor].isspace():
                cursor += 1
            nxt = text[cursor] if cursor < length else ""
            continues = bool(nxt) and not (
                nxt.isupper() or nxt.isdigit() or nxt in "\"\u201c([\u2018" or ord(nxt) > 127
            )
            if word in _ABBREV or (len(word) <= 2 and word.isalpha()) or continues:
                index += 1
                continue
            spans.append((start, index + 1))
            start = cursor
            index = cursor
            continue
        index += 1
    if start < length and text[start:].strip():
        spans.append((start, length))
    return tuple((begin, end) for begin, end in spans if text[begin:end].strip())


def _content_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token.isdigit() or "\u4e00" <= token <= "\u9fff":
            terms.append(token)
        elif token not in _STOP:
            terms.append(_stem(token))
    return terms


def _stem(word: str) -> str:
    if len(word) <= 3:
        return word
    for suffix, replacement in _STEM_RULES:
        if word.endswith(suffix) and len(word) - len(suffix) + len(replacement) >= 4:
            return word[: -len(suffix)] + replacement
    return word