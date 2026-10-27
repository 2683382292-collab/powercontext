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

"""Row-level CSV Sources whose identity is the document and primary key."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
_SOURCE_ID_PREFIX = "csv:"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated data row, with columns and values in header order."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured row evidence addressed by a stable document and key identity."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @property
    def source_id(self) -> str:
        return self.name


class CSVRowTextEvidenceProjection:
    """Project one row as ordered JSON text plus the fields needed to look it up."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = _row_object(source.columns, source.values)
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class CSVRowSourceAdapter:
    """Resolve a validated row into a captured Source and read that row back."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        if value.primary_key not in value.columns:
            raise ValueError("primary_key must name a column exactly")
        if len(value.values) != len(value.columns):
            raise ValueError("row values must match the header width")
        key_index = value.columns.index(value.primary_key)
        if value.values[key_index] != value.primary_key_value:
            raise ValueError("primary_key_value must be the original key field")
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    version="1",
    projections=(CSVRowTextEvidenceProjection(),),
)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build ``csv:{quoted document}:{quoted key}`` without trimming the key."""

    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and must not have surrounding whitespace")
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must contain a non-whitespace character")
    source_id = f"{_SOURCE_ID_PREFIX}{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("csv row source_id must not exceed 256 characters")
    return source_id


def parse_csv_document(document: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one offline CSV document into validated row inputs, or fail entirely."""

    document_id = document.document_id
    primary_key = document.primary_key
    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and must not have surrounding whitespace")
    if not isinstance(primary_key, str) or not primary_key.strip():
        raise ValueError("primary_key must contain a non-whitespace character")

    try:
        text = document.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV document must be strict UTF-8") from error

    try:
        records = list(
            csv.reader(
                io.StringIO(text, newline=""),
                delimiter=",",
                quotechar='"',
                doublequote=True,
                strict=True,
                skipinitialspace=False,
            )
        )
    except csv.Error as error:
        raise ValueError("CSV document is malformed") from error

    if not records or not records[0]:
        raise ValueError("CSV document must contain a header row")
    columns = tuple(records[0])
    _validate_header(columns)
    if primary_key not in columns:
        raise ValueError("primary_key must match a header field exactly")
    key_index = columns.index(primary_key)

    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError("every data record must have the same width as the header")
        primary_key_value = record[key_index]
        if not primary_key_value.strip():
            raise ValueError("primary key values must not be empty or whitespace")
        if primary_key_value in seen:
            raise ValueError("primary key values must be unique within the document")
        seen.add(primary_key_value)
        csv_row_source_id(document_id, primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def _validate_header(columns: tuple[str, ...]) -> None:
    if len(set(columns)) != len(columns):
        raise ValueError("header fields must be unique")
    for column in columns:
        if not column.strip():
            raise ValueError("header fields must not be empty or whitespace")


def _row_object(columns: tuple[str, ...], values: tuple[str, ...]) -> dict[str, str]:
    return {column: value for column, value in zip(columns, values, strict=True)}


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
