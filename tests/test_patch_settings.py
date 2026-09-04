"""Тесты scripts/patch-settings.py — правка ~/.claude/settings.json в изоляции.

Файл называется через дефис, а не как пакетный модуль, поэтому импортируется
через importlib по пути, а не обычным import.
Ни один тест не трогает настоящий ~/.claude/settings.json — только tempfile.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "patch-settings.py"
_spec = importlib.util.spec_from_file_location("patch_settings", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
patch_settings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patch_settings)

COMMAND = "/home/user/.local/bin/claude-statusline.sh"
FOREIGN_BLOCK = {"type": "command", "command": "/opt/other/hook.sh"}


class LoadSettingsTests(unittest.TestCase):
    def test_missing_file_is_empty_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertEqual(patch_settings.load_settings(path), {})

    def test_empty_file_is_empty_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("   \n", encoding="utf-8")
            self.assertEqual(patch_settings.load_settings(path), {})

    def test_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                patch_settings.load_settings(path)

    def test_non_object_root_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_settings.load_settings(path)

    def test_reads_existing_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
            self.assertEqual(patch_settings.load_settings(path), {"foo": "bar"})


class PlanInstallTests(unittest.TestCase):
    def test_adds_block_to_empty_settings(self) -> None:
        result = patch_settings.plan_install({}, COMMAND)
        self.assertEqual(result, {"statusLine": patch_settings._expected_block(COMMAND)})

    def test_preserves_other_keys(self) -> None:
        original = {"theme": "dark", "model": "opus"}
        result = patch_settings.plan_install(original, COMMAND)
        self.assertEqual(
            result,
            {"theme": "dark", "model": "opus", "statusLine": patch_settings._expected_block(COMMAND)},
        )
        # исходный словарь не мутирован — иммутабельность по умолчанию (CLAUDE.md)
        self.assertEqual(original, {"theme": "dark", "model": "opus"})

    def test_noop_when_already_installed_by_us(self) -> None:
        settings = {"statusLine": patch_settings._expected_block(COMMAND)}
        self.assertIsNone(patch_settings.plan_install(settings, COMMAND))

    def test_conflict_when_statusline_occupied_by_other(self) -> None:
        settings = {"statusLine": FOREIGN_BLOCK}
        with self.assertRaises(patch_settings.StatusLineConflict):
            patch_settings.plan_install(settings, COMMAND)

    def test_conflict_does_not_mutate_input(self) -> None:
        settings = {"statusLine": FOREIGN_BLOCK}
        with self.assertRaises(patch_settings.StatusLineConflict):
            patch_settings.plan_install(settings, COMMAND)
        self.assertEqual(settings, {"statusLine": FOREIGN_BLOCK})


class PlanUninstallTests(unittest.TestCase):
    def test_removes_matching_block_and_keeps_other_keys(self) -> None:
        settings = {"theme": "dark", "statusLine": patch_settings._expected_block(COMMAND)}
        result = patch_settings.plan_uninstall(settings, COMMAND)
        self.assertEqual(result, {"theme": "dark"})

    def test_noop_when_statusline_absent(self) -> None:
        self.assertIsNone(patch_settings.plan_uninstall({"theme": "dark"}, COMMAND))

    def test_leaves_foreign_block_untouched(self) -> None:
        settings = {"statusLine": FOREIGN_BLOCK}
        self.assertIsNone(patch_settings.plan_uninstall(settings, COMMAND))


class BackupAndAtomicWriteTests(unittest.TestCase):
    def test_backup_returns_none_when_no_original_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertIsNone(patch_settings.backup_settings(path))

    def test_backup_copies_original_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            backup_path = patch_settings.backup_settings(path)
            self.assertEqual(backup_path, path.with_name("settings.json.bak"))
            self.assertEqual(backup_path.read_text(encoding="utf-8"), '{"a": 1}')

    def test_atomic_write_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "settings.json"
            patch_settings.atomic_write(path, {"a": 1})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})

    def test_atomic_write_leaves_no_tmp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            patch_settings.atomic_write(path, {"a": 1})
            leftovers = [p for p in Path(tmp).iterdir() if p.name != "settings.json"]
            self.assertEqual(leftovers, [])

    def test_atomic_write_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"old": true}', encoding="utf-8")
            patch_settings.atomic_write(path, {"new": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"new": True})

    def test_atomic_write_preserves_existing_file_permissions(self) -> None:
        # mkstemp создаёт временный файл с правами 0600 — без явного chmod
        # os.replace() тихо сузил бы права уже существующего settings.json
        # на каждый прогон.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"old": true}', encoding="utf-8")
            os.chmod(path, 0o644)
            patch_settings.atomic_write(path, {"new": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


class CliEndToEndTests(unittest.TestCase):
    """Прогон через subprocess — проверяет и wiring argparse, и --dry-run."""

    def test_install_dry_run_does_not_touch_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            result = _run_cli(
                "install", "--command", COMMAND, "--settings", str(settings_path), "--dry-run"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(settings_path.exists())

    def test_install_writes_block_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
            result = _run_cli("install", "--command", COMMAND, "--settings", str(settings_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            written = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(written["statusLine"], patch_settings._expected_block(COMMAND))
            self.assertEqual(written["theme"], "dark")
            backup_path = settings_path.with_name("settings.json.bak")
            self.assertEqual(json.loads(backup_path.read_text(encoding="utf-8")), {"theme": "dark"})

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            _run_cli("install", "--command", COMMAND, "--settings", str(settings_path))
            first = settings_path.read_text(encoding="utf-8")
            result = _run_cli("install", "--command", COMMAND, "--settings", str(settings_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(settings_path.read_text(encoding="utf-8"), first)

    def test_install_conflict_exits_nonzero_and_leaves_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            original = json.dumps({"statusLine": FOREIGN_BLOCK})
            settings_path.write_text(original, encoding="utf-8")
            result = _run_cli("install", "--command", COMMAND, "--settings", str(settings_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("statusLine уже занят", result.stderr)
            self.assertEqual(settings_path.read_text(encoding="utf-8"), original)
            self.assertFalse(settings_path.with_name("settings.json.bak").exists())

    def test_uninstall_removes_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"theme": "dark", "statusLine": patch_settings._expected_block(COMMAND)}),
                encoding="utf-8",
            )
            result = _run_cli("uninstall", "--command", COMMAND, "--settings", str(settings_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            written = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(written, {"theme": "dark"})

    def test_uninstall_dry_run_does_not_touch_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            original = json.dumps({"statusLine": patch_settings._expected_block(COMMAND)})
            settings_path.write_text(original, encoding="utf-8")
            result = _run_cli(
                "uninstall", "--command", COMMAND, "--settings", str(settings_path), "--dry-run"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(settings_path.read_text(encoding="utf-8"), original)

    def test_uninstall_is_idempotent_when_run_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"statusLine": patch_settings._expected_block(COMMAND)}), encoding="utf-8"
            )
            _run_cli("uninstall", "--command", COMMAND, "--settings", str(settings_path))
            result = _run_cli("uninstall", "--command", COMMAND, "--settings", str(settings_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8")), {})

    def test_uninstall_leaves_foreign_statusline_and_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            original = json.dumps({"statusLine": FOREIGN_BLOCK})
            settings_path.write_text(original, encoding="utf-8")
            result = _run_cli("uninstall", "--command", COMMAND, "--settings", str(settings_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(settings_path.read_text(encoding="utf-8"), original)

    def test_install_missing_file_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "nested" / "settings.json"
            result = _run_cli("install", "--command", COMMAND, "--settings", str(settings_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            written = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(written, {"statusLine": patch_settings._expected_block(COMMAND)})


if __name__ == "__main__":
    unittest.main()
