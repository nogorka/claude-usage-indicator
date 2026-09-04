"""Окно «Подробнее»: полный список лимитов с барами GTK.

GTK-код тестами не покрыт по той же причине, что и indicator.py — нужен
живой X11/Wayland-сеанс. Самодостаточен: читает снимок сам через
state.read_state(), не заглядывает во внутренности Indicator.
"""
from __future__ import annotations

import sys
import time
import traceback

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk

from . import bar, state

_POLL_INTERVAL_S = 10
_WINDOW_TITLE = "Claude Code — лимиты"

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
        print("claude-usage-indicator: ошибка сборки окна деталей, содержимое не обновлено:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return False
    return True


def _refresh_content(win: Gtk.Window) -> None:
    """Пересобирает содержимое целиком — набор окон (model:*) между тиками не фиксирован.

    Новый Box строится до того, как убирается старый: если сборка бросит исключение,
    окно останется с прежним содержимым, а не опустеет.
    """
    snapshot = state.read_state()
    now = time.time()
    content = _build_content(snapshot, now)
    old_child = win.get_child()
    if old_child is not None:
        win.remove(old_child)
        old_child.destroy()
    win.add(content)
    content.show_all()


def _build_content(snapshot: state.Snapshot, now: float) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for key in snapshot.order:
        _append_window_section(box, snapshot.windows[key], now)
    _append_problem_hint(box, snapshot)
    if snapshot.extra_usage is not None:
        _append_extra_usage(box, snapshot.extra_usage)
    _append_age(box, snapshot, now)
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


def _append_window_section(box: Gtk.Box, window: state.Window, now: float) -> None:
    _add_label(box, window.label)
    _add_level_bar(box, window.percent)
    _add_label(box, f"{bar.round_percent(window.percent)}%")
    _add_label(box, bar.format_reset(window.resets_epoch, now))
    box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)


def _append_extra_usage(box: Gtk.Box, extra: state.ExtraUsage) -> None:
    _add_label(box, "Доп. расход")
    _add_level_bar(box, extra.percent)
    _add_label(box, f"{bar.round_percent(extra.percent)}%")
    box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)


def _append_problem_hint(box: Gtk.Box, snapshot: state.Snapshot) -> None:
    """Когда окон нет, объясняет почему: «ещё не спрашивали» и «файл битый» иначе неотличимы."""
    if snapshot.windows:
        return
    text = bar.problem_text(snapshot.problem)
    if text is not None:
        _add_label(box, text)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)


def _append_age(box: Gtk.Box, snapshot: state.Snapshot, now: float) -> None:
    if snapshot.updated_epoch is None:
        _add_label(box, "данные отсутствуют")
    else:
        _add_label(box, f"данные {bar.format_age(snapshot.updated_epoch, now)}")
