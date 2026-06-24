from __future__ import annotations

import csv
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VocabEntry:
    expression: str
    reading: str = ""
    meaning: str = ""
    sentence: str = ""
    source: str = ""


class VocabStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vocabulary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expression TEXT NOT NULL,
                    reading TEXT NOT NULL DEFAULT '',
                    meaning TEXT NOT NULL DEFAULT '',
                    sentence TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    review_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(expression, reading, sentence)
                )
                """
            )
            conn.commit()

    def add(self, entry: VocabEntry) -> bool:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO vocabulary
                    (expression, reading, meaning, sentence, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.expression.strip(),
                    entry.reading.strip(),
                    entry.meaning.strip(),
                    entry.sentence.strip(),
                    entry.source.strip(),
                ),
            )
            inserted = cursor.rowcount > 0
            conn.commit()
            return inserted

    def list_all(self) -> list[dict]:
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, expression, reading, meaning, sentence, source, created_at, review_count
                FROM vocabulary
                ORDER BY datetime(created_at) DESC, id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, entry_id: int) -> bool:
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM vocabulary WHERE id = ?", (entry_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    def export_csv(self, csv_path: Path) -> None:
        rows = self.list_all()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "expression",
                    "reading",
                    "meaning",
                    "sentence",
                    "source",
                    "created_at",
                    "review_count",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in writer.fieldnames})
