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

`read_state` никогда не бросает исключение: любая структурная проблема (файл
отсутствует, битые байты не в UTF-8, пуст, битый JSON, чужая схема, нет
`limits`) превращается в Snapshot с заполненным `problem`. Мусор внутри
отдельного окна или в `extra_usage` не портит остальной снимок — то, что не
удалось разобрать (включая NaN/Infinity в percent и запредельный
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

FIVE_HOUR = "five_hour"
SEVEN_DAY = "seven_day"
_MODEL_PREFIX = "model:"
_FIXED_LABELS = {FIVE_HOUR: "5 часов", SEVEN_DAY: "7 дней"}
# Конец 9999 года в UTC минус запас в 14 часов (крайнее восточное смещение,
# Кирибати) — format_reset() конвертирует в локальную зону машины через
# astimezone(), и без запаса положительное смещение переносит результат за
# datetime.max, роняя OverflowError (находка повторного ревью после #3).
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
    problem: str | None


def state_path() -> Path:
    """Путь к файлу состояния: CLAUDE_USAGE_STATE важнее XDG_STATE_HOME важнее ~/.local/state."""
    override = os.environ.get("CLAUDE_USAGE_STATE")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "claude-usage" / "latest.json"


def _empty(problem: str) -> Snapshot:
    return Snapshot(updated_epoch=None, windows=MappingProxyType({}), order=(), extra_usage=None, problem=problem)


def _is_plain_number(value: object) -> bool:
    """bool — подкласс int, исключаем явно; NaN/±Infinity — валидный float, но роняет round_percent/render_bar ниже по потоку."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return math.isfinite(value)


def _is_valid_resets_epoch(value: object) -> bool:
    """int в диапазоне [0, _MAX_RESETS_EPOCH]; всё остальное — как мусорное поле."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_RESETS_EPOCH


def _window_label(key: str, raw: Mapping) -> str | None:
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
    if resets_epoch is not None and not _is_valid_resets_epoch(resets_epoch):
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


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def read_state(path: Path | None = None) -> Snapshot:
    """Читает и валидирует файл состояния. Контракт: никогда не бросает исключение."""
    target = path if path is not None else state_path()
    try:
        raw_text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty("no_file")
    except UnicodeDecodeError:
        # Подкласс ValueError, а не OSError — падает мимо ловли ниже, если её пропустить.
        return _empty("bad_encoding")
    except OSError:
        # Права, битый симлинк, каталог вместо файла — любая другая I/O-ошибка.
        return _empty("read_error")

    if not raw_text.strip():
        return _empty("empty_file")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return _empty("bad_json")

    if not isinstance(data, dict):
        return _empty("bad_root")
    if data.get("schema") != 1:
        return _empty("bad_schema")
    if "limits" not in data:
        return _empty("no_limits")

    windows = _parse_windows(data["limits"])
    return Snapshot(
        updated_epoch=_optional_int(data.get("updated_epoch")),
        windows=MappingProxyType(windows),
        order=tuple(_resolve_order(data.get("order"), windows)),
        extra_usage=_parse_extra_usage(data.get("extra_usage")),
        problem=None,
    )
