"""SQLite metadata DB for cache entries."""
from __future__ import annotations
import sqlite3
import asyncio
from pathlib import Path
from typing import Optional


class CacheMetadataDB:
    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text_original TEXT NOT NULL,
                text_normalized TEXT NOT NULL,
                voice_id TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                audio_path TEXT NOT NULL,
                audio_format TEXT DEFAULT 'mp3',
                file_size INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                hit_count INTEGER DEFAULT 0,
                is_filler BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_hit_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_model ON cache_entries(voice_id, model)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_last_hit ON cache_entries(last_hit_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_normalized ON cache_entries(text_normalized)")
        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_entry(self, text_original: str, text_normalized: str, voice_id: str,
                  audio_path: str, model: str = "", audio_format: str = "mp3",
                  file_size: int = 0, is_filler: bool = False) -> int:
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO cache_entries (text_original, text_normalized, voice_id, model,
               audio_path, audio_format, file_size, is_filler)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (text_original, text_normalized, voice_id, model, audio_path, audio_format, file_size, int(is_filler))
        )
        conn.commit()
        entry_id = cursor.lastrowid
        conn.close()
        return entry_id

    def record_hit(self, text_normalized: str, voice_id: str):
        conn = self._get_conn()
        conn.execute(
            """UPDATE cache_entries SET hit_count = hit_count + 1, last_hit_at = CURRENT_TIMESTAMP
               WHERE text_normalized = ? AND voice_id = ?""",
            (text_normalized, voice_id)
        )
        conn.commit()
        conn.close()

    async def record_hit_async(self, text_normalized: str, voice_id: str):
        await asyncio.to_thread(self.record_hit, text_normalized, voice_id)

    def get_all_entries(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT text_normalized, voice_id, audio_path, is_filler FROM cache_entries").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT COUNT(*) as total_entries,
                   COALESCE(SUM(file_size), 0) as total_size_bytes,
                   COALESCE(SUM(hit_count), 0) as total_hits,
                   SUM(CASE WHEN is_filler = 1 THEN 1 ELSE 0 END) as filler_count
            FROM cache_entries
        """).fetchone()
        conn.close()
        return dict(row) if row else {"total_entries": 0, "total_size_bytes": 0, "total_hits": 0, "filler_count": 0}

    def delete_entry(self, entry_id: int) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT audio_path FROM cache_entries WHERE id = ?", (entry_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM cache_entries WHERE id = ?", (entry_id,))
            conn.commit()
            conn.close()
            return row["audio_path"]
        conn.close()
        return None

    def delete_all(self) -> list[str]:
        conn = self._get_conn()
        rows = conn.execute("SELECT audio_path FROM cache_entries").fetchall()
        paths = [r["audio_path"] for r in rows]
        conn.execute("DELETE FROM cache_entries")
        conn.commit()
        conn.close()
        return paths

    def get_eviction_candidates(self, max_entries: int, min_age_days: int) -> list[dict]:
        conn = self._get_conn()
        candidates = conn.execute(
            """SELECT id, audio_path FROM cache_entries
               WHERE is_filler = 0 AND hit_count = 0
               AND created_at < datetime('now', ?)
               ORDER BY created_at ASC""",
            (f"-{min_age_days} days",)
        ).fetchall()
        result = [dict(r) for r in candidates]
        current_count = conn.execute("SELECT COUNT(*) as c FROM cache_entries").fetchone()["c"]
        if current_count - len(result) > max_entries:
            extra_needed = current_count - len(result) - max_entries
            extra = conn.execute(
                """SELECT id, audio_path FROM cache_entries
                   WHERE is_filler = 0 ORDER BY last_hit_at ASC LIMIT ?""",
                (extra_needed,)
            ).fetchall()
            result.extend([dict(r) for r in extra])
        conn.close()
        return result
