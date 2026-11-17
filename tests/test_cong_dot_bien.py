"""Regression cho cổng nghiệm thu: không được biến crash/test thiếu thành PASS."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from danh_gia import kiem_thu_dot_bien as gate


def output(passed=True):
    return ("PASS" if passed else "FAIL") + ": hop dong\n" + ("1" if passed else "0") + "/1 PASS\n"


class DocKetQuaTests(unittest.TestCase):
    def test_full_pass_and_functional_failure(self):
        for ok, code in ((True, 0), (False, 1)):
            with self.subTest(ok=ok):
                result = gate.doc_ket_qua(output(ok), code)
                self.assertIsNone(result["error"])
                self.assertEqual(result["failures"], [] if ok else ["hop dong"])
        self.assertIsNone(gate.doc_ket_qua(output().replace("\n", "\r\n"), 0)["error"])

    def test_crash_missing_truncated_duplicate_or_exit_mismatch_is_error(self):
        cases = [
            ("", 1), ("[FAIL] loi\n0/1 PASS (bo kiem tra DUNG GIUA CHUNG)\n", 1),
            ("FAIL: hop dong\n0/2 PASS\n", 1), (output() + "1/1 PASS\n", 0),
            (output(), 1), (output(False), 0), ("0/0 PASS\n", 0),
            ("PASS: hop dong\n2/1 PASS\n", 0), ("FAIL: hop dong\n1/1 PASS\n", 0),
        ]
        for stdout, code in cases:
            with self.subTest(stdout=stdout, code=code):
                self.assertIsNotNone(gate.doc_ket_qua(stdout, code)["error"])

    def test_classification_checks_names_and_exemptions(self):
        baseline = gate.doc_ket_qua(output(), 0)
        failed = gate.doc_ket_qua(output(False), 1)
        self.assertEqual(gate.phan_loai("D01", failed, baseline)[0], "killed")
        self.assertEqual(gate.phan_loai("D01", baseline, baseline)[0], "survived")
        self.assertEqual(gate.phan_loai("D07", baseline, baseline)[0], "equivalent")
        self.assertEqual(gate.phan_loai("D07", failed, baseline)[0], "error")
        changed = gate.doc_ket_qua(output(False).replace("hop dong", "kiem tra khac"), 1)
        self.assertEqual(gate.phan_loai("D01", changed, baseline)[0], "error")

    def test_gate_exit_codes(self):
        baseline = gate.doc_ket_qua(output(), 0)
        for status, code in (("killed", 0), ("equivalent", 0), ("survived", 1), ("error", 2)):
            with self.subTest(status=status):
                self.assertEqual(gate.ma_thoat(baseline, [{"status": status}]), code)
        self.assertEqual(gate.ma_thoat(gate.doc_ket_qua(output(False), 1), []), 2)
        self.assertEqual(gate.ma_thoat(gate.doc_ket_qua("", 1), []), 2)

    def test_timeout_and_spawn_failure_keep_logs_and_fail(self):
        for exc in (
            subprocess.TimeoutExpired("test", 1, output=b"partial", stderr=b"timeout"),
            OSError("khong chay duoc"),
        ):
            with self.subTest(exc=type(exc).__name__), tempfile.TemporaryDirectory() as tmp:
                with patch.object(gate.subprocess, "run", side_effect=exc):
                    result = gate.chay_kiem_tra(tmp, tmp, "D01", 1)
                self.assertIsNotNone(result["error"])
                self.assertTrue((Path(tmp) / "D01.stdout.log").exists())
                self.assertTrue((Path(tmp) / "D01.stderr.log").exists())


class PipelineTests(unittest.TestCase):
    """Chạy thật subprocess trên fixture nhỏ, bao gồm mã thoát và JSON cuối."""

    def run_fixture(self, root, replacement="bad", broken_baseline=False, old="good"):
        root = Path(root)
        (root / "tests").mkdir()
        (root / "subject.txt").write_text("bad" if broken_baseline else "good", encoding="utf-8")
        (root / "tests" / "verify_task.py").write_text(
            "from pathlib import Path\nimport sys\n"
            "ok = Path('subject.txt').read_text() == 'good'\n"
            "print(('PASS' if ok else 'FAIL') + ': hop dong')\n"
            "print(('1' if ok else '0') + '/1 PASS')\n"
            "sys.exit(0 if ok else 1)\n", encoding="utf-8")
        logs = root / "logs"
        with patch.multiple(gate, GOC=str(root), CHEP=["tests", "subject.txt"],
                            DOT_BIEN=[("D01 fixture", "subject.txt", old, replacement)]):
            with contextlib.redirect_stdout(io.StringIO()):
                code = gate.main(["--output-dir", str(logs), "--workers", "1"])
        report = json.loads((logs / "ket_qua.json").read_text(encoding="utf-8"))
        return code, report

    def test_killed_real_process_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self.run_fixture(tmp)
            self.assertEqual(code, 0)
            self.assertEqual(report["counts"], {"killed": 1})
            self.assertEqual(len(report["source_sha256"]), 64)
            self.assertEqual((Path(tmp) / "subject.txt").read_text(), "good")

    def test_survived_returns_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self.run_fixture(tmp, replacement="good")
            self.assertEqual(code, 1)
            self.assertEqual(report["counts"], {"survived": 1})

    def test_failed_baseline_aborts_before_mutants(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self.run_fixture(tmp, broken_baseline=True)
            self.assertEqual(code, 2)
            self.assertEqual(report["results"], [])
            self.assertFalse((Path(tmp) / "logs" / "D01.stdout.log").exists())

    def test_invalid_mutation_returns_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self.run_fixture(tmp, old="not-present")
            self.assertEqual(code, 2)
            self.assertEqual(report["counts"], {"error": 1})

    def test_cannot_overwrite_old_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "ket_qua.json"
            sentinel.write_text("giu nguyen", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                gate.main(["--output-dir", tmp])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "giu nguyen")


if __name__ == "__main__":
    unittest.main()
