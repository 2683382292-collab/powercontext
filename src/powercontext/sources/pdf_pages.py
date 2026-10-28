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
        "