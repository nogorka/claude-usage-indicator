"""Запуск сессии Claude в выбранном профиле.

Глобального переключения аккаунта не происходит и не предполагается: CLAUDE_CONFIG_DIR
действует на запускаемый процесс, поэтому обе сессии могут идти рядом. Решение, куда
идти, принимает человек — автоматической подмены нет.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from .profiles import DEFAULT_ID, Profile

_TERMINAL = "gnome-terminal"
_LOG = logging.getLogger(__name__)


def launch_command(profile: Profile, home: Path | None = None) -> list[str]:
    """Аргументы запуска терминала с сессией в этом профиле.

    Профиль по умолчанию запускается без CLAUDE_CONFIG_DIR: так работает обычный
    `claude`, и подменять его окружение незачем. `bash -lc` нужен ради login-shell:
    без него в PATH может не оказаться claude, установленного в ~/.local/bin.
    """
    base = home or Path.home()
    assignments = [f"CLAUDE_USAGE_PROFILE_LABEL={shlex.quote(profile.label)}"]
    if profile.id != DEFAULT_ID and profile.config_dir != base / ".claude":
        assignments.append(f"CLAUDE_CONFIG_DIR={shlex.quote(str(profile.config_dir))}")
    return [_TERMINAL, "--", "bash", "-lc", " ".join(["env", *assignments, "claude"])]


def launch(profile: Profile, spawn=subprocess.Popen, home: Path | None = None) -> str | None:
    """Запустить сессию. None при успехе, текст для человека при неудаче.

    Ошибка запуска не должна ронять демон: без окна пользователь останется, без панели —
    нет. Поэтому исключение превращается в текст, который вызывающий показывает диалогом,
    а подробность уходит в лог.
    """
    command = launch_command(profile, home)
    try:
        spawn(command, start_new_session=True)
    except OSError as error:
        _LOG.warning("failed to launch profile %s with %r: %s", profile.id, command, error)
        return f"Could not start a session for “{profile.label}”: {error}"
    return None
