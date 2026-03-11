from __future__ import annotations

from pathlib import Path
import json
import os
import sqlite3
import sys
import tempfile
import time
from typing import Iterable

from .models import AppConfig, ProfileConfig, SignalConfig


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        # Portable mode: keep config/db next to executable.
        return Path(sys.executable).resolve().parent / "data"
    return Path.home() / ".trend_analyzer"


APP_DIR = _app_dir()
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_DB_PATH = APP_DIR / "archive.db"


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            encoding=encoding,
        ) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        if tmp_path is None:
            raise RuntimeError("Не удалось создать временный файл")
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path

    def load(self) -> AppConfig:
        if not self.path.exists():
            return self._default_config()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self._default_config()

        return AppConfig.from_dict(payload)

    def save(self, config: AppConfig) -> None:
        data = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
        atomic_write_text(self.path, data, encoding="utf-8")

    @staticmethod
    def _default_config() -> AppConfig:
        profile = ProfileConfig(
            name="Default",
            db_path=str(DEFAULT_DB_PATH),
            signals=[SignalConfig(name="Signal 1", address=0)],
        )
        return AppConfig(profiles=[profile], active_profile_id=profile.id)


class ArchiveStore:
    def __init__(self, db_path: str):
        path = Path(db_path or DEFAULT_DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA temp_store=MEMORY;")
        self._conn.execute("PRAGMA cache_size=-12000;")
        self._pending_rows = 0
        self._last_commit_ts = time.monotonic()
        self._commit_every_rows = 500
        self._commit_every_seconds = 0.8
        self._create_schema()

    def _create_schema(self) -> None:
        self._ensure_signals_meta_table()
        self._ensure_samples_table_compact()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                ts REAL NOT NULL,
                is_connected INTEGER NOT NULL
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_connection_events_profile_ts
            ON connection_events(profile_id, ts);
            """
        )
        self._conn.commit()

    def insert_batch(self, profile_id: str, ts: float, samples: Iterable[tuple[str, str, float]]) -> None:
        rows = [
            (profile_id, str(signal_id), str(signal_name), float(ts), float(value))
            for signal_id, signal_name, value in samples
            if str(signal_id)
        ]
        if not rows:
            return

        name_rows_map: dict[str, tuple[str, str, str, float]] = {}
        for row_profile_id, row_signal_id, row_signal_name, row_ts, _row_value in rows:
            name_rows_map[row_signal_id] = (row_profile_id, row_signal_id, row_signal_name, row_ts)
        self._conn.executemany(
            """
            INSERT INTO signals_meta(profile_id, signal_id, signal_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id, signal_id) DO UPDATE SET
                signal_name = excluded.signal_name,
                updated_at = excluded.updated_at
            WHERE signals_meta.signal_name != excluded.signal_name
            """,
            list(name_rows_map.values()),
        )

        compact_rows = [
            (row_profile_id, row_signal_id, row_ts, row_value)
            for row_profile_id, row_signal_id, _row_signal_name, row_ts, row_value in rows
        ]
        self._conn.executemany(
            """
            INSERT INTO samples(profile_id, signal_id, ts, value)
            VALUES (?, ?, ?, ?)
            """,
            compact_rows,
        )
        self._pending_rows += len(rows)
        self._commit_if_needed()

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _table_columns(self, table_name: str) -> set[str]:
        if not self._table_exists(table_name):
            return set()
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows if len(row) > 1 and row[1]}

    def _ensure_signals_meta_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals_meta (
                profile_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(profile_id, signal_id)
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signals_meta_profile_updated
            ON signals_meta(profile_id, updated_at);
            """
        )

    def _ensure_samples_table_compact(self) -> None:
        if self._table_exists("samples"):
            columns = self._table_columns("samples")
            # Development-only policy: legacy schema is not supported.
            # If detected, recreate archive table in compact format.
            if "signal_name" in columns:
                self._conn.execute("DROP TABLE samples")

        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                ts REAL NOT NULL,
                value REAL NOT NULL
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_samples_profile_signal_ts
            ON samples(profile_id, signal_id, ts);
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_samples_profile_ts
            ON samples(profile_id, ts);
            """
        )

    def close(self) -> None:
        self.flush()
        self._conn.close()

    def insert_connection_event(self, profile_id: str, ts: float, is_connected: bool) -> None:
        self._conn.execute(
            """
            INSERT INTO connection_events(profile_id, ts, is_connected)
            VALUES (?, ?, ?)
            """,
            (profile_id, float(ts), 1 if is_connected else 0),
        )
        self._pending_rows += 1
        self._commit_if_needed()

    def prune_older_than(self, profile_id: str, cutoff_ts: float) -> tuple[int, int]:
        cur_samples = self._conn.execute(
            """
            DELETE FROM samples
            WHERE profile_id = ? AND ts < ?
            """,
            (profile_id, float(cutoff_ts)),
        )
        cur_conn = self._conn.execute(
            """
            DELETE FROM connection_events
            WHERE profile_id = ? AND ts < ?
            """,
            (profile_id, float(cutoff_ts)),
        )
        self._conn.commit()
        self._pending_rows = 0
        self._last_commit_ts = time.monotonic()
        return int(cur_samples.rowcount or 0), int(cur_conn.rowcount or 0)

    def delete_signals(self, profile_id: str, signal_ids: Iterable[str], vacuum: bool = False) -> tuple[int, int]:
        ids = [str(item).strip() for item in signal_ids if str(item).strip()]
        if not ids:
            return 0, 0

        # Persist pending inserts first, otherwise a later commit could re-add
        # rows that were expected to be removed.
        self.flush()

        removed_samples = self._delete_signals_from_table("samples", profile_id, ids)
        removed_meta = self._delete_signals_from_table("signals_meta", profile_id, ids)
        self._conn.commit()
        self._pending_rows = 0
        self._last_commit_ts = time.monotonic()

        if vacuum and (removed_samples > 0 or removed_meta > 0):
            self._conn.execute("VACUUM")

        return removed_samples, removed_meta

    def _delete_signals_from_table(self, table_name: str, profile_id: str, signal_ids: list[str]) -> int:
        if not signal_ids or not self._table_exists(table_name):
            return 0

        total_removed = 0
        chunk_size = 300
        for start in range(0, len(signal_ids), chunk_size):
            chunk = signal_ids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"DELETE FROM {table_name} WHERE profile_id = ? AND signal_id IN ({placeholders})"
            cursor = self._conn.execute(sql, [profile_id, *chunk])
            total_removed += int(cursor.rowcount or 0)
        return total_removed

    def _commit_if_needed(self, force: bool = False) -> None:
        if self._pending_rows <= 0 and not force:
            return
        now = time.monotonic()
        if not force:
            if self._pending_rows < self._commit_every_rows and (now - self._last_commit_ts) < self._commit_every_seconds:
                return
        self._conn.commit()
        self._pending_rows = 0
        self._last_commit_ts = now

    def flush(self) -> None:
        self._commit_if_needed(force=True)

    def min_sample_ts(self, profile_id: str) -> float | None:
        row = self._conn.execute(
            """
            SELECT MIN(ts)
            FROM samples
            WHERE profile_id = ?
            """,
            (str(profile_id),),
        ).fetchone()
        if not row:
            return None
        value = row[0]
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
