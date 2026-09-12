#!/usr/bin/env python3
"""Patch ~/.claude/settings.json: add or remove the statusLine block.

Split out of install.sh/uninstall.sh into a separate script for the sake of
tests/test_patch_settings.py — the json-patching logic needs to run in
isolation, against a stand-in settings.json path, without running
install.sh/uninstall.sh in full.

Zero network access, zero access to secrets — only reads/writes a single JSON file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
# Таймер statusLine тикает независимо от ввода в сессии (см. доку Claude Code
# про refreshInterval) — без него хук молчит, пока пользователь не наберёт
# что-то в терминальном Claude Code, и не срабатывает вовсе для сессий
# VS Code extension. 10 секунд — та же частота, с которой демон перечитывает
# файл состояния (_POLL_INTERVAL_S в indicator.py/window.py); чаще смысла нет.
_REFRESH_INTERVAL_S = 10


class StatusLineConflict(RuntimeError):
    """statusLine в settings.json занят значением, которое поставили не мы."""


class SettingsIsSymlink(RuntimeError):
    """Цель — символьная ссылка, а запись идёт через временный файл и replace.

    Такая запись заменила бы ссылку обычным файлом, и профили, делящие один
    settings.json, разъехались бы молча. Правится настоящий файл, не ссылка.
    """


def _expected_block(command: str) -> dict[str, Any]:
    return {"type": "command", "command": command, "refreshInterval": _REFRESH_INTERVAL_S}


def load_settings(path: Path) -> dict[str, Any]:
    """Читает settings.json. Отсутствующий или пустой файл — пустой конфиг."""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: settings.json root must be a JSON object")
    return data


def plan_install(settings: dict[str, Any], command: str) -> dict[str, Any] | None:
    """Новый словарь настроек с добавленным statusLine, либо None — менять нечего.

    StatusLineConflict — statusLine уже занят чужим значением, вызывающий
    код обязан остановиться и ничего не писать на диск.
    """
    expected = _expected_block(command)
    current = settings.get("statusLine")
    if current == expected:
        return None
    if current is not None:
        # Содержимое current не идёт в сообщение: command может нести секрет
        # аргументом (например, токен), а это исключение всплывает в stderr.
        raise StatusLineConflict("statusLine is already set to a value we didn't write")
    return {**settings, "statusLine": expected}


def plan_uninstall(settings: dict[str, Any], command: str) -> dict[str, Any] | None:
    """Новый словарь настроек без нашего statusLine, либо None — менять нечего.

    Опознание идёт по command (путь нашего хука), а не по точному совпадению
    всего блока: форма блока менялась (631a9ae добавил refreshInterval), и
    инсталляции старше этого коммита несут на диске двухключевой вариант.
    Чужой statusLine (другая command) не трогается: удаление чужой
    конфигурации — не наша забота, это не откат наших же изменений.
    """
    current = settings.get("statusLine")
    if not isinstance(current, dict) or current.get("command") != command:
        return None
    return {key: value for key, value in settings.items() if key != "statusLine"}


def backup_settings(path: Path) -> Path | None:
    """Копия файла до правки. None, если исходного файла ещё не было — копировать нечего."""
    if not path.exists():
        return None
    backup_path = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup_path)
    return backup_path


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Временный файл в целевом каталоге + os.replace — та же схема, что в bin/claude-statusline.sh.

    settings.json не наш файл: mkstemp создаёт временный файл с правами 0600,
    и без явного chmod os.replace() тихо сузил бы права уже существующего
    файла на каждый прогон. Права нового файла (его ещё не было) намеренно
    остаются дефолтными 0600 — сужать нечего, консервативный выбор безопаснее.
    """
    original_mode = path.stat().st_mode if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        if original_mode is not None:
            os.chmod(tmp_name, stat.S_IMODE(original_mode))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _apply(action: str, command: str, settings_path: Path, dry_run: bool) -> int:
    """Читает settings.json, планирует правку и — если не dry-run — применяет её."""
    if settings_path.is_symlink():
        raise SettingsIsSymlink(
            f"{settings_path} is a symlink to {settings_path.resolve()}; "
            f"patch the real file instead: --settings {settings_path.resolve()}"
        )

    try:
        settings = load_settings(settings_path)
    except (OSError, ValueError) as exc:
        print(f"patch-settings: couldn't read {settings_path}: {exc}", file=sys.stderr)
        return 1

    planner = plan_install if action == "install" else plan_uninstall
    try:
        new_settings = planner(settings, command)
    except StatusLineConflict as exc:
        print(f"patch-settings: {exc}", file=sys.stderr)
        print(f"Inspect {settings_path} to see what's there.", file=sys.stderr)
        print("Installation stopped, file untouched.", file=sys.stderr)
        return 1

    if new_settings is None:
        print(f"patch-settings: no changes needed ({settings_path})")
        return 0

    verb = "installed" if action == "install" else "removed"
    if dry_run:
        print(f"patch-settings: [dry-run] statusLine would be {verb} in {settings_path}")
        return 0

    backup_path = backup_settings(settings_path)
    atomic_write(settings_path, new_settings)
    if backup_path is not None:
        print(f"patch-settings: backup created — {backup_path}")
    print(f"patch-settings: statusLine {verb} in {settings_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument(
        "--command", required=True, help="path to the hook symlink — value of statusLine.command"
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH)
    parser.add_argument(
        "--dry-run", action="store_true", help="only print the plan, don't write anything to disk"
    )
    args = parser.parse_args(argv)
    try:
        return _apply(args.action, args.command, args.settings, args.dry_run)
    except SettingsIsSymlink as exc:
        print(f"patch-settings: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
