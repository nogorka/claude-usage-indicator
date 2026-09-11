"""Профили Claude на машине: идентификатор по каталогу конфига и поиск заведённых.

Идентификатор выводится только из пути каталога конфига — из той самой переменной,
которая физически разделяет аккаунты. Отдельного имени профиля намеренно нет: забытая
переменная означала бы два аккаунта в одном файле состояния и молча врущую панель.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ID = "default"
_UNSAFE = re.compile(r"[^a-z0-9_-]+")
_CLAUDE_PREFIX = "claude-"


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    config_dir: Path


def profile_id_from_config_dir(config_dir: str | os.PathLike[str] | None) -> str:
    """Идентификатор профиля по каталогу конфига Claude Code.

    Пустое значение и $HOME/.claude дают DEFAULT_ID: так выглядит установка без
    CLAUDE_CONFIG_DIR, и её файл состояния не должен переезжать при апгрейде.
    """
    if not config_dir:
        return DEFAULT_ID
    resolved = _resolve(Path(os.path.expanduser(str(config_dir))))
    if resolved == _resolve(Path.home() / ".claude"):
        return DEFAULT_ID
    name = resolved.name.lstrip(".").lower()
    if name.startswith(_CLAUDE_PREFIX):
        name = name[len(_CLAUDE_PREFIX):]
    return _UNSAFE.sub("-", name).strip("-") or DEFAULT_ID


def _resolve(path: Path) -> Path:
    """Разрешение пути не должно падать на битом симлинке: сравнение важнее точности."""
    try:
        return path.resolve()
    except OSError:
        return path


def discover_profiles(home: Path | None = None) -> list[Profile]:
    """Профили, реально заведённые на диске.

    Признак — существование `.credentials.json` внутри каталога. Файл именно
    проверяется на существование и никогда не открывается: индикатор не имеет дела
    с учётными данными.
    """
    base = home or Path.home()
    found: dict[str, Profile] = {}
    default_dir = base / ".claude"
    if default_dir.is_dir() and (default_dir / ".credentials.json").exists():
        found["default"] = Profile(id="default", label="default", config_dir=default_dir)
    for candidate in sorted(base.glob(".claude-*")):
        if not candidate.is_dir() or not (candidate / ".credentials.json").exists():
            continue
        profile_id = profile_id_from_config_dir(candidate)
        found.setdefault(
            profile_id, Profile(id=profile_id, label=profile_id, config_dir=candidate)
        )
    return sorted(found.values(), key=lambda profile: profile_sort_key(profile.id))


def profile_sort_key(profile_id: str) -> tuple[int, str]:
    """Порядок профилей в интерфейсе: default первым, остальные по алфавиту.

    Порядок обязан быть стабильным между тиками — метки, прыгающие в панели местами,
    нечитаемы. Поэтому сортировка не зависит ни от свежести, ни от расхода.
    """
    return (0 if profile_id == DEFAULT_ID else 1, profile_id)
