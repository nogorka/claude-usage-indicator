"""Профили Claude на машине: идентификатор по каталогу конфига и поиск заведённых.

Идентификатор выводится только из пути каталога конфига — из той самой переменной,
которая физически разделяет аккаунты. Отдельного имени профиля намеренно нет: забытая
переменная означала бы два аккаунта в одном файле состояния и молча врущую панель.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path

_LOG = logging.getLogger(__name__)

DEFAULT_ID = "default"
_UNSAFE = re.compile(r"[^a-z0-9_-]+")
_CLAUDE_PREFIX = "claude-"
_HASH_LEN = 6
# str.lower() понижает регистр по Юникоду ("İ" -> "i" + комбинирующая точка),
# а bash-сторона под LC_ALL=C этого не делает — паритет реализаций требует
# одного отображения на любом вводе, поэтому регистр понижается только для ASCII.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    config_dir: Path


def profile_id_from_config_dir(config_dir: str | os.PathLike[str] | None) -> str:
    """Идентификатор профиля по каталогу конфига Claude Code.

    Пустое значение и $HOME/.claude дают DEFAULT_ID: так выглядит установка без
    CLAUDE_CONFIG_DIR, и её файл состояния не должен переезжать при апгрейде.

    Правило неинъективно только там, где санитизация ничего не теряет: слаг,
    изменённый очисткой, опустевший или совпавший с зарезервированным DEFAULT_ID,
    дополняется хэшем канонического пути — иначе разные каталоги молча писали бы
    в один и тот же файл состояния.
    """
    if not config_dir:
        return DEFAULT_ID
    # \n вырезается до канонизации: bash-сторона строит слаг через sed/tr
    # построчно и не видит перевод строки как часть санируемой строки, а
    # python re.sub видит — без общего среза здесь реализации расходятся на
    # путях с переводом строки внутри значения CLAUDE_CONFIG_DIR.
    raw = str(config_dir).replace("\n", "")
    if not raw:
        return DEFAULT_ID
    resolved = _resolve(Path(os.path.expanduser(raw)))
    if resolved == _resolve(Path.home() / ".claude"):
        return DEFAULT_ID
    name = resolved.name.lstrip(".").translate(_ASCII_LOWER)
    if name.startswith(_CLAUDE_PREFIX):
        name = name[len(_CLAUDE_PREFIX):]
    slug = _UNSAFE.sub("-", name).strip("-")
    # default зарезервирован за $HOME/.claude: каталог, чей слаг случайно
    # совпал со словом "default", не имеет права занять чужой файл состояния.
    if slug == name and slug and slug != DEFAULT_ID:
        return slug
    # Хэш — от канонического пути, а не от name/slug: два каталога с одним и
    # тем же лоссовым слагом (например, оба нечитаемых в ASCII) обязаны
    # разойтись, а хэш урезанного слага их бы не различил.
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:_HASH_LEN]
    return f"{slug}-{digest}" if slug else f"profile-{digest}"


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

    Сигнатура намеренно не меняется ради дополнительного канала сигнала: возврат
    остался списком, чтобы не тянуть правку в GTK-слой, поэтому коллизия id уходит
    через stdlib `logging`, единственный канал, доступный этому модулю без GTK.
    """
    base = home or Path.home()
    found: dict[str, Profile] = {}
    default_dir = base / ".claude"
    if default_dir.is_dir() and (default_dir / ".credentials.json").exists():
        found[DEFAULT_ID] = Profile(id=DEFAULT_ID, label=DEFAULT_ID, config_dir=default_dir)
    for candidate in sorted(base.glob(".claude-*")):
        if not candidate.is_dir() or not (candidate / ".credentials.json").exists():
            continue
        profile_id = profile_id_from_config_dir(candidate)
        existing = found.get(profile_id)
        if existing is not None:
            _LOG.warning(
                "profile id collision on %r: keeping %s, dropping %s",
                profile_id, existing.config_dir, candidate,
            )
            continue
        found[profile_id] = Profile(id=profile_id, label=profile_id, config_dir=candidate)
    return sorted(found.values(), key=lambda profile: profile_sort_key(profile.id))


_CACHE_TTL_S = 60
# TTL, а не инвалидация по mtime $HOME: заведение профиля — это mkdir ~/.claude-work,
# а затем запись маркер-файла ВНУТРЬ него (см. discover_profiles про признак-файл
# профиля); вторая операция mtime $HOME не меняет, поэтому кэш по mtime залипал бы
# на «профиля нет» до следующей посторонней записи в домашний каталог.


class ProfileCache:
    """Кэш discover_profiles() с TTL: набор заведённых профилей меняется раз в месяцы,
    а меню пересобирается каждые 10 секунд — обход диска на каждый тик того не стоит.

    Время приходит параметром, а не читается часами класса: у вызывающего (Indicator)
    now уже посчитан на тик, а тест без инъекции времени был бы либо медленным, либо флаки.
    """

    def __init__(self, home: Path | None = None) -> None:
        self._home = home
        self._profiles: list[Profile] = []
        self._expires_at = float("-inf")

    def get(self, now: float) -> list[Profile]:
        if now >= self._expires_at:
            self._profiles = discover_profiles(self._home)
            self._expires_at = now + _CACHE_TTL_S
        return self._profiles


def profile_sort_key(profile_id: str) -> tuple[int, str]:
    """Порядок профилей в интерфейсе: default первым, остальные по алфавиту.

    Порядок обязан быть стабильным между тиками — метки, прыгающие в панели местами,
    нечитаемы. Поэтому сортировка не зависит ни от свежести, ни от расхода.
    """
    return (0 if profile_id == DEFAULT_ID else 1, profile_id)
