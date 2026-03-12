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
ARCHIVE_SCHEMA_VERSION = 2


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
        self._db_path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA foreign_keys=ON;")
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
        if self._current_schema_version() != ARCHIVE_SCHEMA_VERSION:
            self._reset_archive_schema()
        self._ensure_profiles_meta_table()
        self._ensure_signal_catalog_table()
        self._ensure_sample_rows_table()
        self._ensure_connection_event_rows_table()
        self._ensure_compatibility_views()
        self._conn.execute("DELETE FROM archive_schema;")
        self._conn.execute("INSERT INTO archive_schema(version) VALUES (?)", (int(ARCHIVE_SCHEMA_VERSION),))
        self._conn.commit()

    def insert_batch(self, profile_id: str, ts: float, samples: Iterable[tuple[str, str, float]]) -> None:
        rows = [
            (profile_id, str(signal_id), str(signal_name), float(ts), float(value))
            for signal_id, signal_name, value in samples
            if str(signal_id)
        ]
        if not rows:
            return

        profile_key = str(profile_id)
        profile_ref = self._ensure_profile_ref(profile_key, updated_at=float(ts))
        name_rows_map: dict[str, tuple[str, float]] = {}
        for _row_profile_id, row_signal_id, row_signal_name, row_ts, _row_value in rows:
            name_rows_map[row_signal_id] = (row_signal_name, float(row_ts))
        signal_refs = self._ensure_signal_refs(profile_ref, name_rows_map)

        compact_rows = [
            (profile_ref, int(signal_refs[row_signal_id]), row_ts, row_value)
            for _row_profile_id, row_signal_id, _row_signal_name, row_ts, row_value in rows
            if row_signal_id in signal_refs
        ]
        self._conn.executemany(
            """
            INSERT INTO sample_rows(profile_ref, signal_ref, ts, value)
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

    def _view_exists(self, view_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name=? LIMIT 1",
            (view_name,),
        ).fetchone()
        return row is not None

    def _table_columns(self, table_name: str) -> set[str]:
        if not self._table_exists(table_name):
            return set()
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows if len(row) > 1 and row[1]}

    def _current_schema_version(self) -> int:
        if not self._table_exists("archive_schema"):
            return 0
        try:
            row = self._conn.execute("SELECT version FROM archive_schema LIMIT 1").fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    def _reset_archive_schema(self) -> None:
        self._conn.execute("PRAGMA foreign_keys=OFF;")
        names = (
            "samples",
            "signals_meta",
            "connection_events",
            "archive_schema",
            "sample_rows",
            "connection_event_rows",
            "signal_catalog",
            "profiles_meta",
        )
        for name in names:
            row = self._conn.execute(
                "SELECT type FROM sqlite_master WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
            if not row:
                continue
            obj_type = str(row[0] or "").strip().lower()
            if obj_type == "view":
                self._conn.execute(f"DROP VIEW IF EXISTS {name}")
            elif obj_type == "table":
                self._conn.execute(f"DROP TABLE IF EXISTS {name}")
        self._conn.execute("PRAGMA foreign_keys=ON;")

    def _ensure_profiles_meta_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS archive_schema (
                version INTEGER NOT NULL
            );
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL UNIQUE,
                updated_at REAL NOT NULL
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_profiles_meta_updated
            ON profiles_meta(updated_at);
            """
        )

    def _ensure_signal_catalog_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_ref INTEGER NOT NULL,
                signal_id TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(profile_ref, signal_id),
                FOREIGN KEY(profile_ref) REFERENCES profiles_meta(id) ON DELETE CASCADE
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signal_catalog_profile_updated
            ON signal_catalog(profile_ref, updated_at);
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signal_catalog_profile_signal
            ON signal_catalog(profile_ref, signal_id);
            """
        )

    def _ensure_sample_rows_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sample_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_ref INTEGER NOT NULL,
                signal_ref INTEGER NOT NULL,
                ts REAL NOT NULL,
                value REAL NOT NULL,
                FOREIGN KEY(profile_ref) REFERENCES profiles_meta(id) ON DELETE CASCADE,
                FOREIGN KEY(signal_ref) REFERENCES signal_catalog(id) ON DELETE CASCADE
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sample_rows_profile_signal_ts
            ON sample_rows(profile_ref, signal_ref, ts);
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sample_rows_profile_ts
            ON sample_rows(profile_ref, ts);
            """
        )

    def _ensure_connection_event_rows_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connection_event_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_ref INTEGER NOT NULL,
                ts REAL NOT NULL,
                is_connected INTEGER NOT NULL,
                FOREIGN KEY(profile_ref) REFERENCES profiles_meta(id) ON DELETE CASCADE
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_connection_event_rows_profile_ts
            ON connection_event_rows(profile_ref, ts);
            """
        )

    def _ensure_compatibility_views(self) -> None:
        self._conn.execute("DROP VIEW IF EXISTS samples")
        self._conn.execute("DROP VIEW IF EXISTS signals_meta")
        self._conn.execute("DROP VIEW IF EXISTS connection_events")
        self._conn.execute(
            """
            CREATE VIEW samples AS
            SELECT
                sr.id AS id,
                p.profile_id AS profile_id,
                sc.signal_id AS signal_id,
                sr.ts AS ts,
                sr.value AS value
            FROM sample_rows AS sr
            JOIN profiles_meta AS p ON p.id = sr.profile_ref
            JOIN signal_catalog AS sc ON sc.id = sr.signal_ref
            """
        )
        self._conn.execute(
            """
            CREATE VIEW signals_meta AS
            SELECT
                p.profile_id AS profile_id,
                sc.signal_id AS signal_id,
                sc.signal_name AS signal_name,
                sc.updated_at AS updated_at
            FROM signal_catalog AS sc
            JOIN profiles_meta AS p ON p.id = sc.profile_ref
            """
        )
        self._conn.execute(
            """
            CREATE VIEW connection_events AS
            SELECT
                cer.id AS id,
                p.profile_id AS profile_id,
                cer.ts AS ts,
                cer.is_connected AS is_connected
            FROM connection_event_rows AS cer
            JOIN profiles_meta AS p ON p.id = cer.profile_ref
            """
        )

    def _ensure_profile_ref(self, profile_id: str, updated_at: float | None = None) -> int:
        profile_key = str(profile_id or "").strip()
        if not profile_key:
            raise RuntimeError("profile_id is empty")
        row = self._conn.execute(
            "SELECT id FROM profiles_meta WHERE profile_id = ? LIMIT 1",
            (profile_key,),
        ).fetchone()
        if row is not None:
            if updated_at is not None:
                self._conn.execute(
                    "UPDATE profiles_meta SET updated_at = ? WHERE id = ?",
                    (float(updated_at), int(row[0])),
                )
            return int(row[0])
        self._conn.execute(
            "INSERT INTO profiles_meta(profile_id, updated_at) VALUES (?, ?)",
            (profile_key, float(time.time() if updated_at is None else updated_at)),
        )
        return int(self._conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _ensure_signal_refs(self, profile_ref: int, names_by_signal_id: dict[str, tuple[str, float]]) -> dict[str, int]:
        if not names_by_signal_id:
            return {}
        for signal_id, (signal_name, updated_at) in names_by_signal_id.items():
            self._conn.execute(
                """
                INSERT INTO signal_catalog(profile_ref, signal_id, signal_name, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(profile_ref, signal_id) DO UPDATE SET
                    signal_name = excluded.signal_name,
                    updated_at = excluded.updated_at
                WHERE signal_catalog.signal_name != excluded.signal_name
                """,
                (int(profile_ref), str(signal_id), str(signal_name), float(updated_at)),
            )
        placeholders = ",".join("?" for _ in names_by_signal_id)
        rows = self._conn.execute(
            f"""
            SELECT signal_id, id
            FROM signal_catalog
            WHERE profile_ref = ? AND signal_id IN ({placeholders})
            """,
            [int(profile_ref), *names_by_signal_id.keys()],
        ).fetchall()
        return {str(signal_id): int(signal_ref) for signal_id, signal_ref in rows}

    def close(self) -> None:
        self.flush()
        self._conn.close()

    def insert_connection_event(self, profile_id: str, ts: float, is_connected: bool) -> None:
        profile_ref = self._ensure_profile_ref(str(profile_id), updated_at=float(ts))
        self._conn.execute(
            """
            INSERT INTO connection_event_rows(profile_ref, ts, is_connected)
            VALUES (?, ?, ?)
            """,
            (int(profile_ref), float(ts), 1 if is_connected else 0),
        )
        self._pending_rows += 1
        self._commit_if_needed()

    def prune_older_than(self, profile_id: str, cutoff_ts: float) -> tuple[int, int]:
        profile_ref = self._ensure_profile_ref(str(profile_id))
        cur_samples = self._conn.execute(
            """
            DELETE FROM sample_rows
            WHERE profile_ref = ? AND ts < ?
            """,
            (int(profile_ref), float(cutoff_ts)),
        )
        cur_conn = self._conn.execute(
            """
            DELETE FROM connection_event_rows
            WHERE profile_ref = ? AND ts < ?
            """,
            (int(profile_ref), float(cutoff_ts)),
        )
        self._conn.commit()
        self._pending_rows = 0
        self._last_commit_ts = time.monotonic()
        return int(cur_samples.rowcount or 0), int(cur_conn.rowcount or 0)

    def db_size_bytes(self) -> int:
        total = 0
        base = Path(self._db_path)
        for suffix in ("", "-wal", "-shm"):
            part = Path(str(base) + suffix)
            try:
                if part.exists():
                    total += int(part.stat().st_size)
            except Exception:
                continue
        return max(0, int(total))

    def prune_to_max_size(
        self,
        profile_id: str,
        max_size_bytes: int,
        *,
        chunk_samples: int = 4000,
        chunk_events: int = 800,
        max_steps: int = 48,
        vacuum: bool = False,
    ) -> tuple[int, int, int]:
        try:
            limit_bytes = int(max_size_bytes)
        except (TypeError, ValueError):
            limit_bytes = 0
        if limit_bytes <= 0:
            return 0, 0, self.db_size_bytes()

        self.flush()
        current_size = self.db_size_bytes()
        if current_size <= limit_bytes:
            return 0, 0, current_size

        removed_samples = 0
        removed_events = 0
        profile_ref = self._ensure_profile_ref(str(profile_id))
        for _step in range(max(1, int(max_steps))):
            current_size = self.db_size_bytes()
            if current_size <= limit_bytes:
                break

            cur_samples = self._conn.execute(
                """
                DELETE FROM sample_rows
                WHERE id IN (
                    SELECT id FROM sample_rows
                    WHERE profile_ref = ?
                    ORDER BY ts ASC, id ASC
                    LIMIT ?
                )
                """,
                (int(profile_ref), max(1, int(chunk_samples))),
            )
            cur_events = self._conn.execute(
                """
                DELETE FROM connection_event_rows
                WHERE id IN (
                    SELECT id FROM connection_event_rows
                    WHERE profile_ref = ?
                    ORDER BY ts ASC, id ASC
                    LIMIT ?
                )
                """,
                (int(profile_ref), max(1, int(chunk_events))),
            )
            step_samples = int(cur_samples.rowcount or 0)
            step_events = int(cur_events.rowcount or 0)
            if step_samples <= 0 and step_events <= 0:
                break
            removed_samples += step_samples
            removed_events += step_events
            self._conn.commit()
            self._pending_rows = 0
            self._last_commit_ts = time.monotonic()

            try:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
            except Exception:
                pass

        if vacuum and (removed_samples > 0 or removed_events > 0):
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception:
                pass
            try:
                self._conn.execute("VACUUM")
            except Exception:
                pass
            self._pending_rows = 0
            self._last_commit_ts = time.monotonic()
        return removed_samples, removed_events, self.db_size_bytes()

    def vacuum(self) -> None:
        self.flush()
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception:
            pass
        self._conn.execute("VACUUM")
        self._pending_rows = 0
        self._last_commit_ts = time.monotonic()

    def delete_signals(self, profile_id: str, signal_ids: Iterable[str], vacuum: bool = False) -> tuple[int, int]:
        ids = [str(item).strip() for item in signal_ids if str(item).strip()]
        if not ids:
            return 0, 0

        # Persist pending inserts first, otherwise a later commit could re-add
        # rows that were expected to be removed.
        self.flush()

        profile_ref = self._ensure_profile_ref(str(profile_id))
        signal_refs = self._signal_refs_for_ids(profile_ref, ids)
        if not signal_refs:
            return 0, 0
        placeholders = ",".join("?" for _ in signal_refs)
        removed_samples = int(
            self._conn.execute(
                f"DELETE FROM sample_rows WHERE profile_ref = ? AND signal_ref IN ({placeholders})",
                [int(profile_ref), *signal_refs],
            ).rowcount
            or 0
        )
        removed_meta = int(
            self._conn.execute(
                f"DELETE FROM signal_catalog WHERE profile_ref = ? AND id IN ({placeholders})",
                [int(profile_ref), *signal_refs],
            ).rowcount
            or 0
        )
        self._conn.commit()
        self._pending_rows = 0
        self._last_commit_ts = time.monotonic()

        if vacuum and (removed_samples > 0 or removed_meta > 0):
            self._conn.execute("VACUUM")

        return removed_samples, removed_meta

    def _signal_refs_for_ids(self, profile_ref: int, signal_ids: list[str]) -> list[int]:
        if not signal_ids:
            return []
        placeholders = ",".join("?" for _ in signal_ids)
        rows = self._conn.execute(
            f"""
            SELECT id
            FROM signal_catalog
            WHERE profile_ref = ? AND signal_id IN ({placeholders})
            """,
            [int(profile_ref), *signal_ids],
        ).fetchall()
        return [int(row[0]) for row in rows if row and row[0] is not None]

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
        profile_ref = self._ensure_profile_ref(str(profile_id))
        row = self._conn.execute(
            """
            SELECT MIN(ts)
            FROM sample_rows
            WHERE profile_ref = ?
            """,
            (int(profile_ref),),
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
