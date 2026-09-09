"""Lock configuration tests never read a project .env or open the default lock."""

import fcntl
import importlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import procurement


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/report_procurement_migration.py"


class WriteLockConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env_file = self.root / ".env"
        self.db = self.root / "report.db"
        with sqlite3.connect(self.db) as conn:
            procurement.ensure_procurement_tables(conn)
        self.shell_lock = self.root / "shell.lock"
        self.dotenv_lock = self.root / "dotenv.lock"
        self.shell_lock.touch()
        self.dotenv_lock.touch()
        self.env = dict(os.environ, JIEDE_WRITE_LOCK_PATH=str(self.shell_lock), PYTHONPATH=str(ROOT))
        self.cmd = [sys.executable, str(CLI), "--database", str(self.db), "--env-file", str(self.env_file)]

    def resolver(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime_config"), "app and CLI need a shared lock-path resolver")
        return importlib.import_module("runtime_config").resolve_write_lock_path

    def configure_dotenv(self):
        self.env_file.write_text(f'LOCK_FOLDER="{self.root}"\nJIEDE_WRITE_LOCK_PATH=${{LOCK_FOLDER}}/dotenv.lock\nSECRET_KEY=synthetic-secret-not-for-cli\n', encoding="utf-8")

    def copied_app_configuration(self, *, cwd=None):
        # A copied app's BASE_DIR is temporary; import does not initialize its DB.
        copied_app = self.root / "isolated_app.py"
        shutil.copyfile(ROOT / "app.py", copied_app)
        code = """
import importlib.util, json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path = [p for p in sys.path if Path(p or os.getcwd()).resolve() != root]
import PIL
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location('isolated_app', sys.argv[2])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print(json.dumps({'lock': str(module.app.config['WRITE_LOCK_PATH']), 'other_config_preserved': module.app.config['SECRET_KEY'] == 'synthetic-secret-not-for-cli'}))
"""
        result = subprocess.run([sys.executable, "-c", code, str(ROOT), str(copied_app)], cwd=cwd, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "data/manuals.db").exists())
        return json.loads(result.stdout)

    def test_dotenv_overrides_shell_for_app_and_cli_and_cli_waits_on_that_lock(self):
        self.configure_dotenv()
        app_config = self.copied_app_configuration()
        self.assertEqual(app_config, {"lock": str(self.dotenv_lock), "other_config_preserved": True})
        isolated_cli = self.root / "scripts" / CLI.name
        isolated_cli.parent.mkdir()
        shutil.copyfile(CLI, isolated_cli)
        before = (self.db.read_bytes(), self.db.stat().st_mtime_ns)
        with self.dotenv_lock.open("rb") as held_lock:
            fcntl.flock(held_lock, fcntl.LOCK_EX)
            reporter = subprocess.Popen([sys.executable, str(isolated_cli), "--database", str(self.db)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([reporter.stderr], [], [], 5)[0])
                diagnostic = reporter.stderr.readline()
                self.assertIn(str(self.dotenv_lock), diagnostic)
                with self.assertRaises(subprocess.TimeoutExpired):
                    reporter.communicate(timeout=0.2)
            finally:
                fcntl.flock(held_lock, fcntl.LOCK_UN)
                stdout, stderr = reporter.communicate(timeout=5)
        self.assertEqual(reporter.returncode, 0, stderr)
        self.assertIsInstance(json.loads(stdout)["sources"], dict)
        self.assertNotIn("synthetic-secret-not-for-cli", diagnostic + stdout + stderr)
        self.assertEqual(before, (self.db.read_bytes(), self.db.stat().st_mtime_ns))

    def test_explicit_cli_lock_overrides_dotenv_even_while_dotenv_lock_is_held(self):
        self.configure_dotenv()
        with self.dotenv_lock.open("rb") as held_lock:
            fcntl.flock(held_lock, fcntl.LOCK_EX)
            result = subprocess.run(self.cmd + ["--lock-path", str(self.shell_lock)], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.shell_lock), result.stderr)
        self.assertNotIn("synthetic-secret-not-for-cli", result.stdout + result.stderr)

    def test_relative_dotenv_lock_is_shared_when_app_and_cli_have_different_cwds(self):
        self.env_file.write_text("JIEDE_WRITE_LOCK_PATH=write.lock\nSECRET_KEY=synthetic-secret-not-for-cli\n", encoding="utf-8")
        app_config = self.copied_app_configuration(cwd=self.root)
        project_lock = self.root / "write.lock"
        project_lock.touch()
        other_cwd = self.root / "operator-directory"
        other_cwd.mkdir()
        decoy_lock = other_cwd / "write.lock"
        decoy_lock.write_bytes(b"must not select this lock")
        isolated_cli = self.root / "scripts" / CLI.name
        isolated_cli.parent.mkdir()
        shutil.copyfile(CLI, isolated_cli)
        paths = (self.db, project_lock, decoy_lock, self.env_file)
        before = [(path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino) for path in paths]
        with project_lock.open("rb") as held_lock:
            fcntl.flock(held_lock, fcntl.LOCK_EX)
            reporter = subprocess.Popen([sys.executable, str(isolated_cli), "--database", str(self.db)], cwd=other_cwd, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([reporter.stderr], [], [], 5)[0])
                diagnostic = reporter.stderr.readline()
                with self.assertRaises(subprocess.TimeoutExpired):
                    reporter.communicate(timeout=0.2)
                self.assertIn(str(project_lock), diagnostic)
            finally:
                fcntl.flock(held_lock, fcntl.LOCK_UN)
                stdout, stderr = reporter.communicate(timeout=5)
        self.assertEqual(reporter.returncode, 0, stderr)
        self.assertEqual(app_config, {"lock": str(project_lock), "other_config_preserved": True})
        self.assertIsInstance(json.loads(stdout)["sources"], dict)
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino) for path in paths])

    def test_relative_shell_lock_is_project_anchored_and_canonical_from_any_cwd(self):
        other_cwd = self.root / "operator-directory"
        other_cwd.mkdir()
        alias_root = self.root / "project-alias"
        alias_root.symlink_to(self.root, target_is_directory=True)
        code = """
import json, os, sys
from runtime_config import resolve_write_lock_path
before = dict(os.environ)
path = resolve_write_lock_path(sys.argv[1])
print(json.dumps({'lock': str(path), 'environment_unchanged': before == dict(os.environ), 'app_imported': 'app' in sys.modules}))
"""
        for cwd in (self.root, other_cwd):
            with self.subTest(cwd=cwd):
                result = subprocess.run([sys.executable, "-c", code, str(alias_root)], cwd=cwd, env=dict(self.env, JIEDE_WRITE_LOCK_PATH="state/../write.lock"), capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"lock": str(self.root / "write.lock"), "environment_unchanged": True, "app_imported": False})
        self.assertFalse((self.root / "write.lock").exists())
        self.assertFalse((other_cwd / "write.lock").exists())

    def test_explicit_relative_cli_lock_uses_callers_cwd_and_is_canonical(self):
        self.configure_dotenv()
        other_cwd = self.root / "operator-directory"
        other_cwd.mkdir()
        (other_cwd / "sub").mkdir()
        selected_lock = other_cwd / "local.lock"
        selected_lock.write_bytes(b"operator-selected lock")
        paths = (self.db, selected_lock, self.dotenv_lock)
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        with self.dotenv_lock.open("rb") as held_lock:
            fcntl.flock(held_lock, fcntl.LOCK_EX)
            result = subprocess.run(self.cmd + ["--lock-path", "sub/../local.lock"], cwd=other_cwd, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(selected_lock), result.stderr)
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths])
        failed = subprocess.run(self.cmd + ["--lock-path", "missing.lock"], cwd=other_cwd, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((other_cwd / "missing.lock").exists())
        self.assertFalse((self.root / "missing.lock").exists())

    def test_missing_dotenv_uses_shell_then_default_without_opening_default_lock(self):
        resolve = self.resolver()
        with patch.dict(os.environ, {"JIEDE_WRITE_LOCK_PATH": str(self.shell_lock)}, clear=True):
            self.assertEqual(resolve(self.root), self.shell_lock)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve(self.root), Path("/tmp").resolve() / "jiede-web-write.lock")
        result = subprocess.run(self.cmd, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.shell_lock), result.stderr)

    def test_resolver_only_returns_lock_without_loading_other_dotenv_values(self):
        resolve = self.resolver()
        self.configure_dotenv()
        with patch.dict(os.environ, {"JIEDE_WRITE_LOCK_PATH": str(self.shell_lock), "SECRET_KEY": "outer-value"}, clear=True):
            before = dict(os.environ)
            self.assertEqual(resolve(self.root), self.dotenv_lock)
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(resolve(self.root, explicit_lock_path=str(self.shell_lock)), self.shell_lock)

    def test_empty_or_invalid_lock_paths_fail_clearly_without_fallback(self):
        resolve = self.resolver()
        for value in ("", "   ", "bad\x00path", "bad\npath"):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "JIEDE_WRITE_LOCK_PATH"):
                    resolve(self.root, explicit_lock_path=value)
                if "\x00" not in value:  # Operating systems reject NUL in env values.
                    with patch.dict(os.environ, {"JIEDE_WRITE_LOCK_PATH": value}, clear=True):
                        with self.assertRaisesRegex(ValueError, "JIEDE_WRITE_LOCK_PATH"):
                            resolve(self.root)
        self.env_file.write_text("JIEDE_WRITE_LOCK_PATH=\n", encoding="utf-8")
        failed = subprocess.run(self.cmd, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("JIEDE_WRITE_LOCK_PATH", failed.stderr)
        explicit_empty = subprocess.run(self.cmd + ["--lock-path", ""], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(explicit_empty.returncode, 0)
        self.assertIn("JIEDE_WRITE_LOCK_PATH", explicit_empty.stderr)
        self.assertFalse((self.root / "data/manuals.db").exists())


if __name__ == "__main__":
    unittest.main()
