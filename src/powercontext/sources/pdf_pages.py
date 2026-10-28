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

"""Parse every physical PDF page into a captured Source and retrieve page citations."""

from __future__ import annotations

import hashlib
import io
import re
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
_SOURCE_ID_PREFIX = "pdf:"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_WORD = re.compile(r"[a-z]+|\d+")
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "from",
        "with",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "what",
        "which",
        "who",
        "how",
        "why",
        "when",
        "where",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "if",
        "then",
        "not",
        "any",
        "has",
        "have",
        "into",
        "than",
        "also",
        "such",
        "their",
        "there",
        "about",
        "using",
        "use",
        "only",
        "shown",
        "show",
        "answer",
        "response",
        "respond",
        "question",
        "calculate",
        "define",
        "relying",
        "rely",
        "details",
        "assuming",
        "perspective",
        "within",
        "explicitly",
        "directly",
        "outlined",
        "state",
        "give",
        "that",
        "primarily",
        "reported",
        "materially",
        "important",
        "ongoing",
    }
)
# Shared filing language. Phrases point at a statement or note; they never name a company or a page.
_COMPANY_SKIP = frozenset(
    {
        "sales",
        "income",
        "revenue",
        "margin",
        "assets",
        "liabilities",
        "capital",
        "operating",
        "operations",
        "statement",
        "statements",
        "consolidated",
        "financial",
        "company",
        "business",
        "total",
        "change",
        "growth",
        "costs",
        "cost",
    }
)


class PDFDocumentInput(BaseModel):
    """One complete original PDF, addressed by the caller's document id."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page, including blank pages, tied to the PDF byte digest."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured page evidence addressed by document, revision, and physical page."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @property
    def source_id(self) -> str:
        return self.name


class PDFPageTextEvidenceProjection:
    """Project one page as document id plus the extracted page text."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


class PDFPageSourceAdapter:
    """Resolve a parsed page into a captured Source and read that page back."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=pdf_page_source_id(value.document_id, value.document_sha256, value.page_number),
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version="1",
    projections=(PDFPageTextEvidenceProjection(),),
)


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build ``pdf:{quoted document}:{sha256}:{page}`` from the revision identity."""

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must contain a non-whitespace character")
    if not isinstance(document_sha256, str) or _SHA256_HEX.fullmatch(document_sha256) is None:
        raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive physical page")
    source_id = f"{_SOURCE_ID_PREFIX}{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("pdf page source_id must not exceed 256 characters")
    return source_id


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page in order, or fail without returning a partial result."""

    document_id = value.document_id
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must contain a non-whitespace character")
    content = value.content
    if not isinstance(content, bytes) or not content:
        raise ValueError("PDF content is empty or invalid")
    document_sha256 = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF cannot be read")
        page_count = len(reader.pages)
        if page_count < 1:
            raise ValueError("PDF has no pages")
        texts: list[str] = []
        for index in range(page_count):
            extracted = reader.pages[index].extract_text()
            texts.append(extracted if isinstance(extracted, str) else "")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF cannot be parsed") from error

    pages: list[PDFPageInput] = []
    for page_number, text in enumerate(texts, start=1):
        pdf_page_source_id(document_id, document_sha256, page_number)
        pages.append(
            PDFPageInput(
                document_id=document_id,
                document_sha256=document_sha256,
                page_number=page_number,
                text=text,
            )
        )
    return tuple(pages)


async def search_pdf_pages(memory, question: str, limit: int = 10):
    """Return up to ``limit`` distinct page citations from scoped Memory."""

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if type(limit) is not int or limit < 1 or not isinstance(question, str) or not question.strip():
        return ()
    scores: dict[tuple[str, str], float] = {}
    hits: dict[tuple[str, str], object] = {}
    for query in _search_queries(question):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=40))
        for rank, hit in enumerate(page.hits, start=1):
            key = (hit.entry_id, hit.entry_version_id)
            scores[key] = scores.get(key, 0.0) + 1.0 / (5.0 + rank)
            hits.setdefault(key, hit)
    ranked = sorted(
        hits,
        key=lambda key: (_page_score(question, hits[key].text) + scores[key] * 16.0, scores[key]),
        reverse=True,
    )

    citations: list[MemoryCitation] = []
    seen_sources: set[str] = set()
    for key in ranked:
        hit = hits[key]
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        sources = record.entry.sources
        if len(sources) != 1:
            continue
        source_id = sources[0].source_id
        if source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)


def _content_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN.findall(question):
        key = token.casefold()
        if key in _STOP_WORDS or (len(key) <= 2 and not any(character.isdigit() for character in key)):
            continue
        if key in seen:
            continue
        seen.add(key)
        terms.append(key)
    return tuple(terms)


def _stems(token: str) -> tuple[str, ...]:
    stems = [token]
    if token.endswith("ies") and len(token) > 6:
        stems.append(token[:-3] + "y")
    elif token.endswith("ity") and len(token) > 7:
        stems.append(token[:-3])
    elif token.endswith("ing") and len(token) > 6:
        stems.append(token[:-3])
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 5:
        stems.append(token[:-1])
    return tuple(dict.fromkeys(stems))


def _search_queries(question: str) -> tuple[str, ...]:
    terms = _content_terms(question)
    phrases = _intent_phrases(question)
    queries: list[str] = []
    if terms:
        queries.append(" ".join(terms))
    queries.extend(phrases)
    longest = sorted(terms, key=len, reverse=True)
    for token in longest[:6]:
        if token.isdigit() and len(token) == 4:
            continue
        queries.extend(_stems(token))
    unique: list[str] = []
    emitted: set[str] = set()
    for query in queries:
        if query and query not in emitted:
            emitted.add(query)
            unique.append(query)
    return tuple(unique[:16])


def _intent_phrases(question: str) -> tuple[str, ...]:
    """Map a financial question onto statement and note wording shared by filings."""

    folded = question.casefold()
    phrases: list[str] = []

    def add(*items: str) -> None:
        for item in items:
            if item not in phrases:
                phrases.append(item)

    if ("capital" in folded and "intensive" in folded) or "capex" in folded or "capital expenditure" in folded:
        add(
            "statement of cash flows",
            "balance sheet",
            "statement of income",
            "purchases of property",
            "depreciation and amortization",
        )
    if "payable" in folded or "dpo" in folded or "inventory" in folded:
        add("accounts payable", "inventories", "cost of sales", "balance sheet", "statements of operations")
    if "quick ratio" in folded or "liquidity" in folded or "current ratio" in folded:
        add("current assets", "current liabilities", "balance sheet")
    if "gross margin" in folded or "gross profit" in folded:
        add("cost of sales", "cost of products", "statements of operations", "statement of income", "total revenues")
    if "operating margin" in folded or "operating income" in folded:
        add("operating income", "operating margin", "statement of income")
    if "ebitda" in folded or "depreciation" in folded:
        add("operating income", "depreciation and amortization", "statement of cash flows")
    if "tax rate" in folded or "effective tax" in folded:
        add("income tax", "statements of operations", "statement of income")
    if "cash flow" in folded or ("investing" in folded and "financing" in folded):
        add("cash flows from operating", "investing activities", "financing activities")
    if "restructur" in folded:
        add("restructuring", "severance", "statement of income")
    if "concentration" in folded:
        add("accounted for", "customer")
    elif "customer" in folded:
        add("customers", "commercial airlines", "u.s. government", "government contracts")
    if "legal" in folded or "litigation" in folded or "lawsuit" in folded:
        add("legal proceedings", "legal actions", "litigation")
    if "m&a" in folded or "acquisition" in folded or "merger" in folded:
        add("acquisitions", "organic sales", "business segment")
    if "segment" in folded or "product categor" in folded or "service categor" in folded or "20%" in folded:
        add("business segment", "net sales", "revenues")
    if "geograph" in folded or ("operat" in folded and "region" in folded):
        add("north america", "latin america", "europe", "asia", "africa", "middle east", "international")
    if "cyclic" in folded or "seasonal" in folded:
        add("cyclical", "seasonal")
    if "production rate" in folded or "deliveries" in folded:
        add("production rate", "deliveries")
    if "industry" in folded or "products and services" in folded or ("products" in folded and "services" in folded):
        add("products", "services", "business")
    if ("revenue" in folded or "net sales" in folded) and ("change" in folded or "year" in folded):
        add("total net sales", "net sales", "statements of operations", "statement of income")
    return tuple(phrases)


_GENERIC_PHRASES = frozenset({"customers", "products", "services", "business", "revenues", "net sales"})
_STATEMENT_TITLE = re.compile(r"consolidated (?:statements? of|balance sheets?)")


def _page_score(question: str, text: str) -> float:
    folded = " ".join(text.casefold().split())
    phrases = _intent_phrases(question)
    specific = [phrase for phrase in phrases if phrase not in _GENERIC_PHRASES]
    specific_hits = sum(1 for phrase in specific if phrase in folded)
    generic_hits = sum(1 for phrase in phrases if phrase in _GENERIC_PHRASES and phrase in folded)
    score = specific_hits * 8.0 + generic_hits * 2.0
    found = set(_WORD.findall(folded))
    for token in _content_terms(question):
        if any(stem in found for stem in _stems(token)):
            score += 2.0 if len(token) >= 8 else 0.5
    score += _company_bonus(question, text)
    score += _statement_bonus(folded, specific)
    return score


def _statement_bonus(folded: str, phrases: list[str]) -> float:
    """Prefer the face of a financial statement over a note that only mentions it."""

    if not any("statement" in phrase or phrase == "balance sheet" for phrase in phrases):
        return 0.0
    if _STATEMENT_TITLE.search(folded) is None:
        return 0.0
    digits = sum(character.isdigit() for character in folded)
    words = len(_WORD.findall(folded))
    if words and digits / words >= 0.9:
        return 24.0
    return 0.0


def _company_bonus(question: str, text: str) -> float:
    """Prefer the filing named in the question. The document id is the first text line."""

    head = text.split("\n", 1)[0].casefold()
    head_tokens = set(_TOKEN.findall(head))
    flat = head.replace("_", "")
    for token in _content_terms(question):
        if token.isdigit() or token in _COMPANY_SKIP:
            continue
        if len(token) >= 3 and token in head_tokens:
            return 16.0
        if len(token) >= 5 and token in flat:
            return 16.0
    return 0.0


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
