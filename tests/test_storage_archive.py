from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from trend_analyzer.storage import ArchiveStore


class ArchiveStoreSchemaTests(unittest.TestCase):
    def test_compact_schema_and_meta_insert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.db"
            store = ArchiveStore(str(db_path))
            store.insert_batch(
                "profile-1",
                1000.0,
                [
                    ("sig-1", "Very long signal name 1", 1.23),
                    ("sig-2", "Very long signal name 2", 4.56),
                ],
            )
            store.flush()
            store.close()

            conn = sqlite3.connect(db_path)
            try:
                cols = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(samples)").fetchall()
                    if len(row) > 1
                }
                self.assertIn("profile_id", cols)
                self.assertIn("signal_id", cols)
                self.assertIn("ts", cols)
                self.assertIn("value", cols)
                self.assertNotIn("signal_name", cols)

                samples_count = int(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
                self.assertEqual(samples_count, 2)

                meta_rows = conn.execute(
                    "SELECT signal_id, signal_name FROM signals_meta WHERE profile_id = ? ORDER BY signal_id",
                    ("profile-1",),
                ).fetchall()
                self.assertEqual(
                    meta_rows,
                    [
                        ("sig-1", "Very long signal name 1"),
                        ("sig-2", "Very long signal name 2"),
                    ],
                )
            finally:
                conn.close()

    def test_delete_signals_removes_samples_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive_delete.db"
            store = ArchiveStore(str(db_path))
            store.insert_batch(
                "profile-1",
                1000.0,
                [
                    ("sig-1", "Signal 1", 1.0),
                    ("sig-2", "Signal 2", 2.0),
                    ("sig-3", "Signal 3", 3.0),
                ],
            )
            removed_samples, removed_meta = store.delete_signals("profile-1", ["sig-2", "sig-3"], vacuum=False)
            self.assertEqual(removed_samples, 2)
            self.assertEqual(removed_meta, 2)

            store.close()

            conn = sqlite3.connect(db_path)
            try:
                remaining_samples = int(
                    conn.execute("SELECT COUNT(*) FROM samples WHERE profile_id = ?", ("profile-1",)).fetchone()[0]
                )
                self.assertEqual(remaining_samples, 1)
                remaining_meta = int(
                    conn.execute("SELECT COUNT(*) FROM signals_meta WHERE profile_id = ?", ("profile-1",)).fetchone()[0]
                )
                self.assertEqual(remaining_meta, 1)
            finally:
                conn.close()

    def test_signals_meta_updated_only_when_name_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive_meta.db"
            store = ArchiveStore(str(db_path))

            store.insert_batch("profile-1", 1000.0, [("sig-1", "Signal A", 1.0)])
            store.flush()
            first_name, first_updated_at = store._conn.execute(  # noqa: SLF001 - test helper
                "SELECT signal_name, updated_at FROM signals_meta WHERE profile_id = ? AND signal_id = ?",
                ("profile-1", "sig-1"),
            ).fetchone()

            store.insert_batch("profile-1", 1010.0, [("sig-1", "Signal A", 2.0)])
            store.flush()
            second_name, second_updated_at = store._conn.execute(  # noqa: SLF001 - test helper
                "SELECT signal_name, updated_at FROM signals_meta WHERE profile_id = ? AND signal_id = ?",
                ("profile-1", "sig-1"),
            ).fetchone()

            self.assertEqual(first_name, "Signal A")
            self.assertEqual(second_name, "Signal A")
            self.assertEqual(float(first_updated_at), float(second_updated_at))

            store.insert_batch("profile-1", 1020.0, [("sig-1", "Signal A (renamed)", 3.0)])
            store.flush()
            third_name, third_updated_at = store._conn.execute(  # noqa: SLF001 - test helper
                "SELECT signal_name, updated_at FROM signals_meta WHERE profile_id = ? AND signal_id = ?",
                ("profile-1", "sig-1"),
            ).fetchone()

            self.assertEqual(third_name, "Signal A (renamed)")
            self.assertGreater(float(third_updated_at), float(second_updated_at))
            store.close()

    def test_legacy_samples_table_is_recreated_without_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive_legacy.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE samples (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id TEXT NOT NULL,
                        signal_id TEXT NOT NULL,
                        signal_name TEXT NOT NULL,
                        ts REAL NOT NULL,
                        value REAL NOT NULL
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO samples(profile_id, signal_id, signal_name, ts, value) VALUES (?, ?, ?, ?, ?)",
                    ("profile-legacy", "sig-a", "Signal A", 10.0, 1.0),
                )
                conn.execute(
                    "INSERT INTO samples(profile_id, signal_id, signal_name, ts, value) VALUES (?, ?, ?, ?, ?)",
                    ("profile-legacy", "sig-b", "Signal B", 11.0, 2.0),
                )
                conn.commit()
            finally:
                conn.close()

            store = ArchiveStore(str(db_path))
            store.close()

            conn = sqlite3.connect(db_path)
            try:
                cols = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(samples)").fetchall()
                    if len(row) > 1
                }
                self.assertNotIn("signal_name", cols)
                # Legacy rows are intentionally dropped: app supports only new schema.
                self.assertEqual(int(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]), 0)
                self.assertEqual(int(conn.execute("SELECT COUNT(*) FROM signals_meta").fetchone()[0]), 0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
