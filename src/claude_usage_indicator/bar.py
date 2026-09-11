"""Чистые функции рендера панели и меню.

Ни GTK, ни файловой системы, ни `time.time()` внутри — текущее время всегда
приходит параметром, иначе функции нельзя было бы тестировать детерминированно.
Алгоритм бара здесь обязан совпадать бит-в-бит с bash-реализацией хука
statusLine: обе стороны рисуют один и тот же бар из общего файла состояния,
и расхождение ловится гардом на общей таблице фикстур.
"""
from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone

from .profiles import profile_sort_key
from .state import FIVE_HOUR, SEVEN_DAY, ProfileSnapshot, Reading, Snapshot, Window

_FULL_CELL = "▓"
_EMPTY_CELL = "░"
_SEPARATOR = " · "
_NO_DATA_LABEL = "Claude: no data"
_PANEL_SHORT_KEYS = {FIVE_HOUR: "5h", SEVEN_DAY: "7d"}
_ATTENTION_PREFIX = "⚠ "
_STALE_SUFFIX = " (stale)"
# Два профиля делят ширину панели пополам, поэтому несвежесть помечается одним символом.
# Легаси-суффикс ` (stale)` остаётся за одиночным режимом: критерий 5 требует от него
# посимвольного совпадения с сегодняшней строкой.
_STALE_MARK = "*"


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


def is_expired(window: Window, now_epoch: float) -> bool:
    """Срок окна истёк: сохранённый процент относится к уже закрытому окну."""
    return window.resets_epoch is not None and window.resets_epoch <= now_epoch


def effective_percent(window: Window, now_epoch: float) -> float:
    """Процент, который честно показать сейчас.

    После сброса сохранённое число заведомо неверно, а ноль — оценка: аккаунтом могли
    пользоваться с телефона или с claude.ai, и тогда расход больше нуля. Порог
    достоверности задан владелицей как «правдоподобно», и оценка ему отвечает,
    а старое число — нет.
    """
    return 0.0 if is_expired(window, now_epoch) else window.percent


def _iter_windows(snapshot: Snapshot, now_epoch: float) -> Iterator[tuple[str, Window, float]]:
    """Окна снимка в порядке `order`, вместе с их effective_percent.

    Общий обход для `binding_window`, `panel_label` и `menu_section_lines`: все три
    читают окна строго по `order`, пропуская ключи без окна, и `order` остаётся
    единственным источником и обхода, и tie-break'а — раздельные копии этого цикла
    расходились бы при следующей правке одной из трёх функций.
    """
    for key in snapshot.order:
        window = snapshot.windows.get(key)
        if window is None:
            continue
        yield key, window, effective_percent(window, now_epoch)


def binding_window(snapshot: Snapshot, now_epoch: float) -> tuple[str, Window] | None:
    """Окно, которое сейчас связывает: с наибольшим эффективным процентом.

    Ничья разрешается порядком из `order`, а не произвольным: метка панели не должна
    менять окно между тиками при равных числах.
    """
    best: tuple[str, Window, float] | None = None
    for key, window, percent in _iter_windows(snapshot, now_epoch):
        if best is None or percent > best[2]:
            best = (key, window, percent)
    return (best[0], best[1]) if best is not None else None


def panel_label(snapshot: Snapshot, now_epoch: float) -> str:
    """Текст метки панели по `order`; extra_usage сюда никогда не попадает (только в меню)."""
    parts = [
        f"{panel_key(key, window)} {render_bar(percent)} {round_percent(percent)}%"
        for key, window, percent in _iter_windows(snapshot, now_epoch)
    ]
    return _SEPARATOR.join(parts) if parts else _NO_DATA_LABEL


def is_alarm(snapshot: Snapshot, now_epoch: float, threshold: float = 80.0) -> bool:
    """Тревога, если хоть одно окно (включая model:*) достигло порога."""
    return any(
        effective_percent(window, now_epoch) >= threshold for window in snapshot.windows.values()
    )


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
    alarm = is_alarm(snapshot, now_epoch)
    label = panel_label(snapshot, now_epoch)
    if alarm:
        label = _ATTENTION_PREFIX + label
    if is_stale(snapshot, now_epoch):
        label += _STALE_SUFFIX
    return label, alarm


def panel_label_no_data() -> str:
    """Обёртка над `_NO_DATA_LABEL`, чтобы вызывающий код не зависел от приватного имени."""
    return _NO_DATA_LABEL


def panel_state_for(reading: Reading, now_epoch: float) -> tuple[str, bool]:
    """Текст метки и статус тревоги для всего каталога состояния.

    Один профиль отдаётся в прежний panel_state без изменений: пока второй аккаунт не
    заведён, панель обязана выглядеть ровно как раньше.
    """
    entries = [reading.profiles[key] for key in sorted(reading.profiles, key=profile_sort_key)]
    if not entries:
        return panel_label_no_data(), False
    if len(entries) == 1:
        return panel_state(entries[0].snapshot, now_epoch)
    alarm = any(is_alarm(entry.snapshot, now_epoch) for entry in entries)
    chunks = [_profile_chunk(entry, now_epoch) for entry in entries]
    label = _SEPARATOR.join(chunks)
    if alarm:
        label = _ATTENTION_PREFIX + label
    return label, alarm


def _profile_chunk(entry: ProfileSnapshot, now_epoch: float) -> str:
    """Один профиль в метке панели: связывающее окно, процент и компактная метка
    его сброса.

    Все окна каждого профиля в панель GNOME не помещаются; полная разбивка по всем
    окнам и полное время сброса каждого живут в меню и в окне «Подробнее».
    """
    binding = binding_window(entry.snapshot, now_epoch)
    if binding is None:
        return f"{entry.label} {_NO_DATA_LABEL}"
    key, window = binding
    percent = effective_percent(window, now_epoch)
    chunk = f"{entry.label} {panel_key(key, window)} {render_bar(percent)} {round_percent(percent)}%"
    if is_stale(entry.snapshot, now_epoch):
        chunk += _STALE_MARK
    reset_marker = format_reset_panel(window.resets_epoch, now_epoch)
    if reset_marker:
        chunk += " " + reset_marker
    return chunk


def _plural_en(n: int, singular: str) -> str:
    """Согласование английских числительных: 1 — singular, всё остальное — regular plural (+s)."""
    return singular if n == 1 else singular + "s"


def format_age(updated_epoch: int, now_epoch: float) -> str:
    """Человеческое «N назад» на английском с согласованием числительных."""
    age_s = max(0.0, now_epoch - updated_epoch)
    if age_s < 60:
        return "just now"
    minutes = _round_half_up(age_s / 60)
    if minutes < 60:
        return f"{minutes} {_plural_en(minutes, 'minute')} ago"
    hours = _round_half_up(age_s / 3600)
    if hours < 24:
        return f"{hours} {_plural_en(hours, 'hour')} ago"
    days = _round_half_up(age_s / 86400)
    return f"{days} {_plural_en(days, 'day')} ago"


def format_reset(resets_epoch: int | None, now_epoch: float) -> str:
    """«resets at HH:MM, in N h M m» в локальном времени машины; None — окна сброса нет."""
    if resets_epoch is None:
        return "reset time unknown"
    reset_dt = datetime.fromtimestamp(resets_epoch, tz=timezone.utc).astimezone()
    time_str = reset_dt.strftime("%H:%M")
    delta_s = resets_epoch - now_epoch
    if delta_s <= 0:
        date_str = reset_dt.strftime("%d.%m")
        return f"window reset at {time_str} on {date_str}; next window starts with the first session"
    hours, remainder = divmod(int(delta_s), 3600)
    minutes = remainder // 60
    return f"resets at {time_str}, in {hours}h {minutes}m"


def format_reset_panel(resets_epoch: int | None, now_epoch: float) -> str:
    """Компактная метка сброса связывающего окна для панели: `↻HH:MM`/`↻DD.MM`.

    Не полная форма `format_reset` — та несёт «через Nч Mм» и остаётся только в меню,
    где ширина не ограничена. Здесь ширина панели фиксирована: дальше суток точность
    падает до дня. Окно уже сброшено или срок неизвестен — пустая строка: следующее
    время сброса демону неизвестно, пока новая сессия не запишет состояние, а врать
    нельзя.
    """
    if resets_epoch is None or resets_epoch <= now_epoch:
        return ""
    reset_dt = datetime.fromtimestamp(resets_epoch, tz=timezone.utc).astimezone()
    delta_s = resets_epoch - now_epoch
    if delta_s < 86400:
        return "↻" + reset_dt.strftime("%H:%M")
    return "↻" + reset_dt.strftime("%d.%m")


def menu_section_lines(entry: ProfileSnapshot, now_epoch: float) -> list[str]:
    """Секция одного профиля: метка, все окна с процентом и сбросом, возраст снимка.

    В отличие от панели — там только связывающее окно и компактный маркер его сброса —
    здесь показываются все окна с полным временем сброса каждого: это то, ради чего меню
    открывают, и прятать его за выбором одного окна нельзя.
    """
    snapshot = entry.snapshot
    lines = [entry.label]
    for key, window, percent in _iter_windows(snapshot, now_epoch):
        lines.append(
            f"{panel_key(key, window)} {render_bar(percent)} {round_percent(percent)}% · "
            f"{format_reset(window.resets_epoch, now_epoch)}"
        )
    lines.append(f"as of {format_age(snapshot.updated_epoch, now_epoch)}")
    return lines


def unreadable_line(names: Sequence[str]) -> str | None:
    """Одна строка про файлы, которые не разобрались. None, если таких нет.

    Молчать о них нельзя: пропавший профиль иначе неотличим от профиля, которым
    сегодня просто не пользовались.
    """
    if not names:
        return None
    return "couldn't read: " + ", ".join(names)


_PROBLEM_MESSAGES = {
    "no_file": "Claude Code has never run with the hook installed",
    "read_error": "couldn't read the state file — check file permissions",
    "empty_file": "state file is empty — the hook hasn't written data yet",
    "bad_json": "state file is corrupted — invalid JSON",
    "bad_root": "state file is corrupted — invalid data structure",
    "bad_schema": "state file was written by a different hook version",
    "bad_encoding": "state file is corrupted — invalid encoding",
    "no_limits": "numbers will appear after the first request in Claude Code",
}
_UNKNOWN_PROBLEM_MESSAGE = "couldn't read usage data"


def problem_text(problem: str | None) -> str | None:
    """Человеческое объяснение проблемы для пункта меню; None — данных не было, но и ошибки нет.

    Неизвестный код (будущая версия хука завела новый) получает общую
    формулировку, а не падение — тот же принцип, что и у разбора файла
    состояния: непонятное отбрасывается, а не роняет остальное.
    """
    if problem is None:
        return None
    return _PROBLEM_MESSAGES.get(problem, _UNKNOWN_PROBLEM_MESSAGE)


def no_profiles_line(reading: Reading) -> str | None:
    """Каталог состояния целиком пуст: ни одного профиля, ни одного нечитаемого файла —
    первый запуск до того, как хук что-либо записал. None, если каталог не пуст (профиль
    нашёлся, или хотя бы один файл там есть, просто не разобрался — за то сообщение
    отвечает unreadable_line).

    Текст берётся из того же места, что problem_text("no_file") для одиночного файла
    состояния: ситуация та же, просто смотрим на каталог целиком, а не на один путь.
    """
    if reading.profiles or reading.unreadable:
        return None
    return _PROBLEM_MESSAGES["no_file"]
