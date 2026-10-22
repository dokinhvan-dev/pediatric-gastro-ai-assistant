"""Nâng cấp DB có dữ liệu, chạy lặp và rollback; chỉ dùng DB trong thư mục tạm."""
import contextlib
import datetime
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bitss_migration_test_")
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.url = "sqlite:///" + self.db_path.as_posix()
        # Nạp module riêng để không đổi engine của module khác trong cùng phiên kiểm tra.
        spec = importlib.util.spec_from_file_location("migration_fixture", ROOT / "setup_database.py")
        self.db = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"DATABASE_URL": self.url}):
            spec.loader.exec_module(self.db)
        self.addCleanup(self.db.engine.dispose)

    def init(self):
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            self.db.init_db()
        return captured.getvalue()

    def seed_family(self):
        with self.db.engine.begin() as conn:
            conn.execute(self.db.User.__table__.insert().values(
                id=1, email="fixture@example.invalid", password_hash="test-only", full_name="Fixture"))
            conn.execute(self.db.Child.__table__.insert().values(
                id=1, owner_user_id=1, name="Fixture child",
                date_of_birth=datetime.date(2025, 1, 1), feeding_type="formula"))
            conn.execute(self.db.StoolRecord.__table__.insert().values(
                id=1, child_id=1, age_months_at_observation=12,
                feeding_type_at_observation="formula", image_path="fixture.jpg"))

    def scalar(self, sql):
        with self.db.engine.connect() as conn:
            return conn.exec_driver_sql(sql).scalar()

    def assert_audit_immutable(self):
        with self.db.engine.begin() as conn:
            conn.execute(self.db.AuditLog.__table__.insert().values(
                id=101, actor_role="admin", action="fixture", outcome="success"))
        for sql in ("UPDATE audit_log SET action='changed' WHERE id=101",
                    "DELETE FROM audit_log WHERE id=101"):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError):
                with self.db.engine.begin() as conn:
                    conn.exec_driver_sql(sql)
        self.assertEqual(self.scalar("SELECT action FROM audit_log WHERE id=101"), "fixture")

    def test_fresh_and_repeat_have_all_tables_and_audit_triggers(self):
        self.init()
        self.assertEqual(self.db.kiem_tra_schema(), [])
        self.assertEqual(set(inspect(self.db.engine).get_table_names()),
                         set(self.db.Base.metadata.tables))
        self.seed_family()
        self.init()
        self.assertEqual(self.scalar("SELECT count(*) FROM stool_records"), 1)
        self.assert_audit_immutable()

    def test_legacy_six_tables_migrates_clinical_codes_preserves_records_and_repeats(self):
        tables = [t for name, t in self.db.Base.metadata.tables.items()
                  if name not in {"child_yeu_to", "audit_log", "worker_tokens"}]
        self.db.Base.metadata.create_all(self.db.engine, tables=tables)
        self.assertEqual(len(inspect(self.db.engine).get_table_names()), 6)
        self.seed_family()
        code = sorted(self.db._MA_YEU_TO_HOP_LE)[0]
        with self.db.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE children ADD COLUMN yeu_to_lam_sang TEXT")
            conn.exec_driver_sql("UPDATE children SET yeu_to_lam_sang=? WHERE id=1",
                                 (f"{code},{code},unknown_fixture",))
        warning = self.init()
        self.assertIn("unknown_fixture", warning)
        self.assertEqual(self.db.kiem_tra_schema(), [])
        self.assertEqual(self.scalar("SELECT ma FROM child_yeu_to WHERE child_id=1"), code)
        self.init()
        self.assertEqual(self.scalar("SELECT count(*) FROM child_yeu_to"), 1)
        self.assertEqual(self.scalar("SELECT image_path FROM stool_records WHERE id=1"), "fixture.jpg")
        self.assertEqual(self.scalar("SELECT email FROM users WHERE id=1"), "fixture@example.invalid")
        self.assert_audit_immutable()

    def test_main_seven_tables_upgrade(self):
        tables = [t for name, t in self.db.Base.metadata.tables.items()
                  if name not in {"audit_log", "worker_tokens"}]
        self.db.Base.metadata.create_all(self.db.engine, tables=tables)
        self.seed_family()
        self.init()
        self.assertEqual(self.db.kiem_tra_schema(), [])
        self.assertEqual(self.scalar("SELECT count(*) FROM children"), 1)
        self.assert_audit_immutable()

    def test_security_before_worker_id_keeps_old_audit_and_token(self):
        self.init()
        with self.db.engine.begin() as conn:
            conn.execute(self.db.AuditLog.__table__.insert().values(
                id=1, actor_role="admin", action="old-event", outcome="success"))
            conn.execute(self.db.WorkerToken.__table__.insert().values(
                id=1, ten="fixture", token_hash="a" * 64,
                expires_at=datetime.datetime(2030, 1, 1)))
            conn.exec_driver_sql("ALTER TABLE audit_log DROP COLUMN actor_worker_id")
        self.init()
        self.init()
        self.assertEqual(self.db.kiem_tra_schema(), [])
        self.assertEqual(self.scalar("SELECT action FROM audit_log WHERE id=1"), "old-event")
        self.assertIsNone(self.scalar("SELECT actor_worker_id FROM audit_log WHERE id=1"))
        self.assertEqual(self.scalar("SELECT token_hash FROM worker_tokens WHERE id=1"), "a" * 64)
        self.assert_audit_immutable()

    def test_failure_during_move_rolls_back_then_retry_succeeds(self):
        self.init()
        self.seed_family()
        code = sorted(self.db._MA_YEU_TO_HOP_LE)[0]
        with self.db.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE children ADD COLUMN yeu_to_lam_sang TEXT")
            conn.exec_driver_sql("UPDATE children SET yeu_to_lam_sang=? WHERE id=1", (code,))

        def interrupt(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("ALTER TABLE children DROP COLUMN"):
                raise RuntimeError("fixture: loi truoc DROP")

        event.listen(self.db.engine, "before_cursor_execute", interrupt)
        try:
            with self.assertRaisesRegex(RuntimeError, "loi truoc DROP"):
                self.init()
        finally:
            event.remove(self.db.engine, "before_cursor_execute", interrupt)
        self.assertEqual(self.scalar("SELECT yeu_to_lam_sang FROM children WHERE id=1"), code)
        self.assertEqual(self.scalar("SELECT count(*) FROM child_yeu_to"), 0)
        self.assertEqual(self.scalar("SELECT count(*) FROM stool_records"), 1)
        self.init()
        self.assertEqual(self.db.kiem_tra_schema(), [])
        self.assertEqual(self.scalar("SELECT count(*) FROM child_yeu_to"), 1)

    def test_incompatible_required_column_blocks_api_and_cli(self):
        self.init()
        with self.db.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE users DROP COLUMN password_hash")
        with self.assertRaisesRegex(self.db.SchemaChuaSanSang, "users.password_hash"):
            self.init()
        result = subprocess.run(
            [sys.executable, str(ROOT / "setup_database.py")], cwd=ROOT,
            env={**os.environ, "DATABASE_URL": self.url, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("users.password_hash", result.stderr)
        self.assertNotIn("Schema da san sang", result.stdout)


if __name__ == "__main__":
    unittest.main()
