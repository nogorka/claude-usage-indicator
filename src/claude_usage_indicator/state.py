"""Чтение и разбор файла состояния (Ш0). Чистые функции: ни GTK, ни сети.

Формат файла — единственный шов между хуком statusLine (bash) и этим демоном:

    {
      "schema": 1,
      "updated_epoch": 1788547623,
      "limits": {
        "five_hour":   {"percent": 42.3, "resets_epoch": 1788550200},
        "seven_day":   {"percent": 55.0, "resets_epoch": 1788700000},
        "model:fable": {"percent": 21.0, "resets_epoch": 1788700000, "label": "Fable"}
      },
      "order": ["five_hour", "seven_day", "model:fable"],
      "extra_usage": {"percent": 31.0, "used_credits": 12.4, "monthly_limit": 40.0, "currency": "USD"}
    }

`read_all_states` никогда не бросает исключение: файл, который не удалось
прочитать или разобрать, попадает в `unreadable` и не мешает остальным. Мусор
внутри отдельного окна или в `extra_usage` не портит остальной снимок — то, что
не удалось разобрать (включая NaN/Infinity в percent и запредельный
resets_epoch), просто выбрасывается.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TypeGuard

FIVE_HOUR = "five_hour"
SEVEN_DAY = "seven_day"
_MODEL_PREFIX = "model:"
_FIXED_LABELS = {FIVE_HOUR: "5 hours", SEVEN_DAY: "7 days"}
# Конец 9999 года в UTC минус запас в 14 часов (крайнее восточное смещение,
# Кирибати) — format_reset() конвертирует в локальную зону машины через
# astimezone(), и без запаса положительное смещение переносит результат за
# datetime.max, роняя OverflowError.
_MAX_RESETS_EPOCH = 253402300799 - 14 * 3600


@dataclass(frozen=True)
class Window:
    percent: float
    resets_epoch: int | None
    label: str


@dataclass(frozen=True)
class ExtraUsage:
    percent: float
    used_credits: float | None
    monthly_limit: float | None
    currency: str | None


@dataclass(frozen=True)
class Snapshot:
    updated_epoch: int | None
    windows: Mapping[str, Window]
    order: Sequence[str]
    extra_usage: ExtraUsage | None


def state_dir() -> Path:
    """Каталог файлов состояния — по файлу на профиль.

    CLAUDE_USAGE_STATE указывает на файл, а не на каталог: он остаётся точкой
    переопределения для тестов и ручного пиннинга, и тогда каталогом считается его
    родитель.
    """
    override = os.environ.get("CLAUDE_USAGE_STATE")
    if override:
        return Path(override).parent
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "claude-usage"


def _is_plain_number(value: object) -> TypeGuard[int | float]:
    """bool — подкласс int, исключаем явно; NaN/±Infinity — валидный float, но роняет round_percent/render_bar ниже по потоку."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return math.isfinite(value)


def _is_valid_epoch(value: object) -> TypeGuard[int]:
    """int в диапазоне [0, _MAX_RESETS_EPOCH]; всё остальное — как мусорное поле.

    Общий безопасный предел для любого epoch-поля схемы (resets_epoch,
    updated_epoch) — оба в итоге идут в datetime-конверсии ниже по потоку
    (format_reset/format_age), которые падают OverflowError за его пределами.
    """
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_RESETS_EPOCH


def _window_label(key: str, raw: Mapping[str, object]) -> str | None:
    """Ярлык окна: фиксированный текст для known-ключей, из файла — для model:*.

    Неизвестный ключ (не five_hour/seven_day/model:*) возвращает None и тем
    самым отбраковывает всё окно — набор поддерживаемых классов окон, а не
    произвольные имена.
    """
    if key in _FIXED_LABELS:
        return _FIXED_LABELS[key]
    if key.startswith(_MODEL_PREFIX):
        label = raw.get("label")
        return label if isinstance(label, str) and label else None
    return None


def _parse_window(key: str, raw: object) -> Window | None:
    """Разбирает одно окно; любая структурная несуразность — окно целиком отбрасывается."""
    if not isinstance(raw, dict):
        return None
    percent = raw.get("percent")
    if not _is_plain_number(percent):
        return None
    resets_epoch = raw.get("resets_epoch")
    if resets_epoch is not None and not _is_valid_epoch(resets_epoch):
        return None
    label = _window_label(key, raw)
    if label is None:
        return None
    return Window(percent=float(percent), resets_epoch=resets_epoch, label=label)


def _parse_windows(raw_limits: object) -> dict[str, Window]:
    """Разбирает limits целиком, пропуская мусорные окна — частичные данные лучше, чем никаких."""
    if not isinstance(raw_limits, dict):
        return {}
    windows: dict[str, Window] = {}
    for key, raw_window in raw_limits.items():
        if not isinstance(key, str):
            continue
        window = _parse_window(key, raw_window)
        if window is not None:
            windows[key] = window
    return windows


def _default_order(windows: Mapping[str, Window]) -> list[str]:
    """Порядок по умолчанию: фиксированные окна первыми, остальное — по алфавиту."""
    fixed = [key for key in (FIVE_HOUR, SEVEN_DAY) if key in windows]
    rest = sorted(key for key in windows if key not in (FIVE_HOUR, SEVEN_DAY))
    return fixed + rest


def _resolve_order(raw_order: object, windows: Mapping[str, Window]) -> list[str]:
    """order может отсутствовать, быть битым или ссылаться на несуществующий ключ.

    В любом из этих случаев порядок обязан покрывать ровно существующие окна:
    ссылки на отсутствующие ключи молча отфильтровываются, а окна, о которых
    order не упомянул, дописываются в конец по алфавиту.
    """
    if not isinstance(raw_order, list):
        return _default_order(windows)
    order: list[str] = []
    for entry in raw_order:
        if isinstance(entry, str) and entry in windows and entry not in order:
            order.append(entry)
    missing = sorted(key for key in windows if key not in order)
    return order + missing


def _optional_float(value: object) -> float | None:
    return float(value) if _is_plain_number(value) else None


def _parse_extra_usage(raw: object) -> ExtraUsage | None:
    """extra_usage — необязательный блок; битый percent роняет весь блок, не весь снимок."""
    if not isinstance(raw, dict):
        return None
    percent = raw.get("percent")
    if not _is_plain_number(percent):
        return None
    currency = raw.get("currency")
    return ExtraUsage(
        percent=float(percent),
        used_credits=_optional_float(raw.get("used_credits")),
        monthly_limit=_optional_float(raw.get("monthly_limit")),
        currency=currency if isinstance(currency, str) else None,
    )


def _parse_payload(payload: dict) -> Snapshot:
    """Разбирает тело снимка (updated_epoch/limits/order/extra_usage) без валидации schema.

    Валидация схемы остаётся снаружи: отсутствующие или мусорные limits здесь
    дают пустой снимок, а не ошибку, потому что решение «этот файл вообще не
    наш» принимается по полю schema, а не по содержимому тела.
    """
    windows = _parse_windows(payload.get("limits"))
    return Snapshot(
        updated_epoch=(payload.get("updated_epoch") if _is_valid_epoch(payload.get("updated_epoch")) else None),
        windows=MappingProxyType(windows),
        order=tuple(_resolve_order(payload.get("order"), windows)),
        extra_usage=_parse_extra_usage(payload.get("extra_usage")),
    )


_SUPPORTED_SCHEMAS = (1, 2)


@dataclass(frozen=True)
class ProfileSnapshot:
    profile_id: str
    label: str
    snapshot: Snapshot


@dataclass(frozen=True)
class Reading:
    """Снимок всего каталога состояния.

    Нечитаемые файлы держатся отдельным списком, а не приписываются какому-нибудь
    профилю: у файла, который не разобрался, профиля нет по определению.
    """

    profiles: Mapping[str, ProfileSnapshot]
    unreadable: Sequence[str]


def read_all_states(directory: Path | None = None) -> Reading:
    """Все профили каталога. Мусорный файл пропускается, а не роняет чтение целиком:
    панель с одним битым файлом обязана показывать остальные профили."""
    base = directory if directory is not None else state_dir()
    freshest: dict[str, tuple[ProfileSnapshot, int]] = {}
    unreadable: list[str] = []
    for path in _state_files(base):
        parsed = _read_profile_file(path)
        if parsed is None:
            unreadable.append(path.name)
            continue
        current = freshest.get(parsed.profile_id)
        age = parsed.snapshot.updated_epoch if parsed.snapshot.updated_epoch is not None else -1
        if current is None or age > current[1]:
            freshest[parsed.profile_id] = (parsed, age)
    return Reading(
        profiles=MappingProxyType({key: entry for key, (entry, _) in freshest.items()}),
        unreadable=tuple(unreadable),
    )


def _state_files(base: Path) -> list[Path]:
    try:
        return sorted(base.glob("*.json"))
    except OSError:
        return []


def _read_profile_file(path: Path) -> ProfileSnapshot | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") not in _SUPPORTED_SCHEMAS:
        return None
    block = payload.get("profile")
    if not isinstance(block, dict):
        block = {}
    profile_id = block.get("id")
    if not isinstance(profile_id, str) or not profile_id:
        profile_id = "default"
    # config_dir в файл не пишется: читателю он не нужен ни для чего. Путь к каталогу
    # конфигурации берётся с диска в discover_profiles, и запись его ещё и в состояние
    # создала бы второй источник правды, который некому сверять.
    label = block.get("label")
    if not isinstance(label, str) or not label:
        label = profile_id
    return ProfileSnapshot(profile_id=profile_id, label=label, snapshot=_parse_payload(payload))
