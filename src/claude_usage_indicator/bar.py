"""Чистые функции рендера панели и меню.

Ни GTK, ни файловой системы, ни `time.time()` внутри — текущее время всегда
приходит параметром, иначе функции нельзя было бы тестировать детерминированно.
Алгоритм бара здесь обязан совпадать бит-в-бит с bash-реализацией хука
statusLine: обе стороны рисуют один и тот же бар из общего файла состояния,
и расхождение ловится гардом на общей таблице фикстур.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .state import FIVE_HOUR, SEVEN_DAY, Snapshot, Window

_FULL_CELL = "▓"
_EMPTY_CELL = "░"
_SEPARATOR = " · "
_NO_DATA_LABEL = "Claude: нет данных"
_PANEL_SHORT_KEYS = {FIVE_HOUR: "5h", SEVEN_DAY: "7d"}
_ATTENTION_PREFIX = "⚠ "
_STALE_SUFFIX = " (устарело)"


def _round_half_up(value: float) -> int:
    """round-half-up (не банковское округление builtin `round()`) — общее для процентов и возраста."""
    return math.floor(value + 0.5)


def render_bar(percent: float, cells: int = 8) -> str:
    """Бар из `cells` символов; filled = round-half-up(percent/100*cells), зажатый в 0..cells."""
    filled = _round_half_up(percent / 100 * cells)
    filled = max(0, min(cells, filled))
    return _FULL_CELL * filled + _EMPTY_CELL * (cells - filled)


def round_percent(percent: float) -> int:
    """Тот же round-half-up, что и в render_bar — текст и бар не должны расходиться на границе.

    Публичная, а не приватная: тот же процент показывается и в тексте панели,
    и в меню (indicator.py), и должен округляться одинаково в обоих местах —
    иначе для одного окна панель и меню разойдутся в отображаемой цифре.
    """
    return _round_half_up(percent)


def panel_key(key: str, window: Window) -> str:
    """Короткая метка в тексте панели: 5h/7d для фиксированных окон, иначе label окна."""
    return _PANEL_SHORT_KEYS.get(key, window.label)


def panel_label(snapshot: Snapshot) -> str:
    """Текст метки панели по `order`; extra_usage сюда никогда не попадает (только в меню)."""
    parts = []
    for key in snapshot.order:
        window = snapshot.windows.get(key)
        if window is None:
            continue
        text = f"{panel_key(key, window)} {render_bar(window.percent)} {round_percent(window.percent)}%"
        parts.append(text)
    return _SEPARATOR.join(parts) if parts else _NO_DATA_LABEL


def is_alarm(snapshot: Snapshot, threshold: float = 80.0) -> bool:
    """Тревога, если хоть одно окно (включая model:*) достигло порога."""
    return any(window.percent >= threshold for window in snapshot.windows.values())


def is_stale(snapshot: Snapshot, now_epoch: float, max_age_s: int = 3600) -> bool:
    """Данные устарели, если снимка нет вовсе или запись старше max_age_s."""
    if snapshot.updated_epoch is None:
        return True
    return (now_epoch - snapshot.updated_epoch) > max_age_s


def panel_state(snapshot: Snapshot, now_epoch: float) -> tuple[str, bool]:
    """Текст метки панели и статус тревоги на момент `now_epoch`.

    Вынесено отдельно от сборки меню: тревога и «устарело» — функции текущего
    времени, а не только снимка, и обязаны пересчитываться на каждый тик
    таймера опроса, даже когда сам снимок между тиками не изменился (закрытый
    Claude Code не пишет файл, но время идёт) — иначе «устарело» не появляется
    никогда.
    """
    alarm = is_alarm(snapshot)
    label = panel_label(snapshot)
    if alarm:
        label = _ATTENTION_PREFIX + label
    if is_stale(snapshot, now_epoch):
        label += _STALE_SUFFIX
    return label, alarm


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Согласование русских числительных: 1 — one, 2-4 — few, остальное — many.

    11-14 — исключение из общего правила по последней цифре (не «11 минуту»).
    """
    n_abs = abs(n)
    if 11 <= n_abs % 100 <= 14:
        return many
    last_digit = n_abs % 10
    if last_digit == 1:
        return one
    if 2 <= last_digit <= 4:
        return few
    return many


def format_age(updated_epoch: int, now_epoch: float) -> str:
    """Человеческое «N назад» на русском с согласованием числительных."""
    age_s = max(0.0, now_epoch - updated_epoch)
    if age_s < 60:
        return "только что"
    minutes = _round_half_up(age_s / 60)
    if minutes < 60:
        return f"{minutes} {_plural_ru(minutes, 'минуту', 'минуты', 'минут')} назад"
    hours = _round_half_up(age_s / 3600)
    if hours < 24:
        return f"{hours} {_plural_ru(hours, 'час', 'часа', 'часов')} назад"
    days = _round_half_up(age_s / 86400)
    return f"{days} {_plural_ru(days, 'день', 'дня', 'дней')} назад"


def format_reset(resets_epoch: int | None, now_epoch: float) -> str:
    """«сброс в HH:MM, через N ч M м» в локальном времени машины; None — окна сброса нет."""
    if resets_epoch is None:
        return "время сброса неизвестно"
    reset_dt = datetime.fromtimestamp(resets_epoch, tz=timezone.utc).astimezone()
    time_str = reset_dt.strftime("%H:%M")
    delta_s = resets_epoch - now_epoch
    if delta_s <= 0:
        return f"сброс в {time_str}"
    hours, remainder = divmod(int(delta_s), 3600)
    minutes = remainder // 60
    return f"сброс в {time_str}, через {hours} ч {minutes} м"


_PROBLEM_MESSAGES = {
    "no_file": "Claude Code ещё ни разу не запускался с установленным хуком",
    "read_error": "не удалось прочитать файл состояния — проверьте права доступа",
    "empty_file": "файл состояния пуст — хук ещё не записал данные",
    "bad_json": "файл состояния повреждён — в нём невалидный JSON",
    "bad_root": "файл состояния повреждён — неверная структура данных",
    "bad_schema": "файл состояния записан другой версией хука",
    "bad_encoding": "файл состояния повреждён — неверная кодировка",
    "no_limits": "цифры появятся после первого запроса в Claude Code",
}
_UNKNOWN_PROBLEM_MESSAGE = "не удалось прочитать данные об использовании"


def problem_text(problem: str | None) -> str | None:
    """Человеческое объяснение проблемы для пункта меню; None — данных не было, но и ошибки нет.

    Неизвестный код (будущая версия хука завела новый) получает общую
    формулировку, а не падение — тот же принцип, что и у разбора файла
    состояния: непонятное отбрасывается, а не роняет остальное.
    """
    if problem is None:
        return None
    return _PROBLEM_MESSAGES.get(problem, _UNKNOWN_PROBLEM_MESSAGE)
