"""Store engineering materials in seekdb and retrieve them with FULLTEXT."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pylibseekdb
import pymysql

from sddctx.paths import MANIFEST, ROOT, SEEKDB

_CREATE = """
CREATE TABLE IF NOT EXISTS materials (
    doc_id VARCHAR(64) PRIMARY KEY,
    kind VARCHAR(16) NOT NULL,
    source_path VARCHAR(255) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    FULLTEXT INDEX idx_materials (title, body) WITH PARSER SPACE
)
"""
_SEARCH = """
SELECT doc_id, kind, source_path, symbol, title, body,
       MATCH(title, body) AGAINST(%s) AS score
FROM materials
WHERE MATCH(title, body) AGAINST(%s)
ORDER BY score DESC, doc_id ASC
LIMIT %s
"""


@dataclass(frozen=True)
class Material:
    doc_id: str
    kind: str
    source_path: str
    symbol: str
    title: str
    body: str
    score: float


class MaterialStore:
    def __init__(self, directory: Path = SEEKDB) -> None:
        self.directory = directory
        self._instance = None
        self._connection = None

    def open(self) -> MaterialStore:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._instance = pylibseekdb.open(str(self.directory))
        options = self._instance.connection_options()
        self._connection = pymysql.connect(
            user=options["user"],
            unix_socket=options["unix_socket"],
            database="test",
            autocommit=True,
            charset="utf8mb4",
        )
        with self._connection.cursor() as cursor:
            cursor.execute(_CREATE)
        return self

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._instance is not None:
            self._instance.close()
            self._instance = None

    def __enter__(self) -> MaterialStore:
        return self.open()

    def __exit__(self, *_args) -> None:
        self.close()

    def reset(self) -> None:
        self.close()
        if self.directory.exists():
            for path in sorted(self.directory.rglob("*"), reverse=True):
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()
            self.directory.rmdir()

    def import_manifest(self, manifest: Path = MANIFEST) -> list[str]:
        documents = json.loads(manifest.read_text(encoding="utf-8"))
        imported: list[str] = []
        with self._connection.cursor() as cursor:
            for item in documents:
                source = ROOT / item["path"]
                if not source.is_file():
                    raise FileNotFoundError(source)
                body = source.read_text(encoding="utf-8")
                cursor.execute("DELETE FROM materials WHERE doc_id = %s", (item["doc_id"],))
                cursor.execute(
                    """
                    INSERT INTO materials (doc_id, kind, source_path, symbol, title, body)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item["doc_id"],
                        item["kind"],
                        item["path"],
                        item["symbol"],
                        item["title"],
                        body,
                    ),
                )
                imported.append(item["doc_id"])
        return imported

    def search(self, query: str, *, limit: int = 8) -> list[Material]:
        with self._connection.cursor() as cursor:
            cursor.execute(_SEARCH, (query, query, limit))
            rows = cursor.fetchall()
        return [
            Material(
                doc_id=row[0],
                kind=row[1],
                source_path=row[2],
                symbol=row[3],
                title=row[4],
                body=row[5],
                score=float(row[6]),
            )
            for row in rows
        ]

    def explain(self, query: str) -> str:
        with self._connection.cursor() as cursor:
            cursor.execute("EXPLAIN " + _SEARCH, (query, query, 8))
            return "\n".join(str(row) for row in cursor.fetchall())
