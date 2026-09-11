"""Окно «Подробнее»: по секции на профиль, текст каждой собирает bar.py.

GTK-код тестами не покрыт по той же причине, что и indicator.py — нужен
живой X11/Wayland-сеанс. Самодостаточен: читает состояние само через
state.read_all_states(), не заглядывает во внутренности Indicator.
"""
from __future__ import annotations

import sys
import time
import traceback

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from . import bar, state
from .profiles import profile_sort_key

_POLL_INTERVAL_S = 10
_WINDOW_TITLE = "Claude Code — Limits"

# Единственная переменная модуля: живой Gtk.Window или None, если окно закрыто.
# Второго счётчика для таймера не нужно — колбэк сверяет захваченный им же
# экземпляр окна с этой переменной и сам снимает себя, когда они разошлись.
_window: Gtk.Window | None = None


def show_details_window() -> None:
    """Показывает окно деталей: поднимает существующее, создаёт новое, либо, если начальное
    содержимое не собралось (битый файл состояния), не показывает ничего — пустое окно без
    таймера обновления хуже, чем отсутствие окна до следующего клика.
    """
    global _window
    if _window is not None:
        _window.present()
        return
    win = Gtk.Window(title=_WINDOW_TITLE)
    win.set_default_size(420, 320)
    win.set_border_width(12)
    win.connect("destroy", _on_destroy)
    if not _safe_refresh_content(win):
        win.destroy()
        return
    _window = win
    GLib.timeout_add_seconds(_POLL_INTERVAL_S, lambda: _on_timeout(win))
    win.show_all()


def _on_destroy(_widget: Gtk.Window) -> None:
    global _window
    _window = None


def _on_timeout(win: Gtk.Window) -> bool:
    if _window is not win:
        return False  # окно уже закрыто (и не факт, что не открыто заново) — этот таймер отслужил
    if _safe_refresh_content(win):
        return True
    # Неудачное обновление закрывает окно вместо того, чтобы оставлять его
    # висеть с застывшим содержимым: пустое окно без таймера — тот же
    # инвариант, что и в show_details_window при первом открытии (см. её
    # докстринг). destroy() сам сбросит _window через _on_destroy.
    # Подавление ниже — ложное срабатывание: mypy сужает win по "is"-сравнению
    # с _window (Gtk.Window | None, оба типа Any из-за нестабленного gi) и
    # протаскивает None в тип win, хотя сюда всегда приходит живое окно.
    win.destroy()  # type: ignore[union-attr]
    return False


def _safe_refresh_content(win: Gtk.Window) -> bool:
    """Ошибка сборки не должна ронять таймер обновления окна."""
    try:
        _refresh_content(win)
    except Exception:
        print("claude-usage-indicator: details window build failed, content not updated:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return False
    return True


def _refresh_content(win: Gtk.Window) -> None:
    """Пересобирает содержимое целиком — набор профилей и их окон (model:*) между тиками
    не фиксирован.

    Новый Box строится до того, как убирается старый: если сборка бросит исключение,
    окно останется с прежним содержимым, а не опустеет.
    """
    reading = state.read_all_states()
    now = time.time()
    content = _build_content(reading, now)
    old_child = win.get_child()
    if old_child is not None:
        win.remove(old_child)
        old_child.destroy()
    win.add(content)
    content.show_all()


def _build_content(reading: state.Reading, now: float) -> Gtk.Box:
    """Секция на каждый найденный профиль, в порядке `profile_sort_key`; если профилей нет
    вовсе — строка первого запуска; затем строка про файлы, которые не разобрались (если
    такие есть)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for profile_id in sorted(reading.profiles, key=profile_sort_key):
        _append_profile_section(box, reading.profiles[profile_id], now)
    no_profiles = bar.no_profiles_line(reading)
    if no_profiles is not None:
        _add_label(box, no_profiles)
    unreadable = bar.unreadable_line(reading.unreadable)
    if unreadable is not None:
        _add_label(box, unreadable)
    return box


def _add_label(box: Gtk.Box, text: str) -> None:
    box.pack_start(Gtk.Label(label=text, xalign=0), False, False, 0)


def _add_level_bar(box: Gtk.Box, percent: float) -> None:
    # Значение зажимается в 0..1: source-данные не гарантируют percent в 0..100
    # (как и render_bar/round_percent в bar.py), а LevelBar варнит в stderr на выходе за max.
    level = Gtk.LevelBar()
    level.set_min_value(0.0)
    level.set_max_value(1.0)
    level.set_value(max(0.0, min(1.0, percent / 100)))
    box.pack_start(level, False, False, 0)


def _append_profile_section(box: Gtk.Box, entry: state.ProfileSnapshot, now: float) -> None:
    """Секция одного профиля: заголовок, окна и возраст снимка — готовый текст из
    `bar.menu_section_lines`, тот же, что и в меню трея, — плюс `extra_usage` между окнами
    и возрастом, которого в этом тексте нет.

    `menu_section_lines` всегда кладёт строку возраста последней в списке — единственная,
    которую добавляет после цикла по окнам, — поэтому она безопасно отделяется срезом.
    """
    lines = bar.menu_section_lines(entry, now)
    for line in lines[:-1]:
        _add_label(box, line)
    if entry.snapshot.extra_usage is not None:
        _append_extra_usage(box, entry.snapshot.extra_usage)
    _add_label(box, lines[-1])
    box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)


def _append_extra_usage(box: Gtk.Box, extra: state.ExtraUsage) -> None:
    _add_label(box, "Extra usage")
    _add_level_bar(box, extra.percent)
    _add_label(box, f"{bar.round_percent(extra.percent)}%")
