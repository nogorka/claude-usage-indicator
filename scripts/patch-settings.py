#!/usr/bin/env python3
"""Правка ~/.claude/settings.json: добавление или снятие блока statusLine.

Вынесено из install.sh/uninstall.sh отдельным скриптом ради tests/test_patch_settings.py —
логику json-правки нужно гонять в изоляции, с подставным путём settings.json,
не запуская install.sh/uninstall.sh целиком.

Ноль сети, ноль доступа к секретам — только чтение/запись одного JSON-файла.
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


class StatusLineConflict(RuntimeError):
    """statusLine в settings.json занят значением, которое поставили не мы."""


def _expected_block(command: str) -> dict[str, Any]:
    return {"type": "command", "command": command}


def load_settings(path: Path) -> dict[str, Any]:
    """Читает settings.json. Отсутствующий или пустой файл — пустой конфиг."""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: корень settings.json должен быть JSON-объектом")
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
        raise StatusLineConflict(
            f"statusLine уже занят: {json.dumps(current, ensure_ascii=False)}"
        )
    return {**settings, "statusLine": expected}


def plan_uninstall(settings: dict[str, Any], command: str) -> dict[str, Any] | None:
    """Новый словарь настроек без нашего statusLine, либо None — менять нечего.

    Чужой statusLine (не тот, что поставил бы install) не трогается: удаление
    чужой конфигурации — не наша забота, это не откат наших же изменений.
    """
    expected = _expected_block(command)
    if settings.get("statusLine") != expected:
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
    try:
        settings = load_settings(settings_path)
    except (OSError, ValueError) as exc:
        print(f"patch-settings: не удалось прочитать {settings_path}: {exc}", file=sys.stderr)
        return 1

    planner = plan_install if action == "install" else plan_uninstall
    try:
        new_settings = planner(settings, command)
    except StatusLineConflict as exc:
        print(f"patch-settings: {exc}", file=sys.stderr)
        print("Установка остановлена, файл не тронут.", file=sys.stderr)
        return 1

    if new_settings is None:
        print(f"patch-settings: изменений не требуется ({settings_path})")
        return 0

    verb = "установлен" if action == "install" else "снят"
    if dry_run:
        print(f"patch-settings: [dry-run] statusLine был бы {verb} в {settings_path}")
        return 0

    backup_path = backup_settings(settings_path)
    atomic_write(settings_path, new_settings)
    if backup_path is not None:
        print(f"patch-settings: резервная копия — {backup_path}")
    print(f"patch-settings: statusLine {verb} в {settings_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument(
        "--command", required=True, help="путь symlink-хука — значение statusLine.command"
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH)
    parser.add_argument(
        "--dry-run", action="store_true", help="только напечатать план, ничего не писать на диск"
    )
    args = parser.parse_args(argv)
    return _apply(args.action, args.command, args.settings, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
