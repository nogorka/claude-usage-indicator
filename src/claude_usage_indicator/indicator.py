"""AyatanaAppIndicator3-обвязка: метка в панели, меню, таймер опроса раз в 10 секунд.

GTK-код тестами не покрыт — нужен живой X11/Wayland-сеанс с шиной indicator,
проверяется живым прогоном в Task 5. Здесь сознательно тонкий слой поверх
bar.py/state.py: вся логика, которую можно протестировать без GTK, живёт там.
"""
from __future__ import annotations

import subprocess
import sys
import time
import traceback
from types import MappingProxyType

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import GLib, Gtk
from gi.repository import AyatanaAppIndicator3 as AppIndicator3

from . import bar, state

_APP_ID = "claude-usage-indicator"
_ICON_NORMAL = "utilities-system-monitor"
_ICON_ALARM = "dialog-warning"
_POLL_INTERVAL_S = 10
_UNIT_NAME = "claude-usage-indicator.service"
# Снимок для честного «нет данных», когда рендер реального снимка упал (находка ревью #3):
# panel_label на пустых windows/order гарантированно не бросает — сам по себе fallback безопасен.
_RENDER_FAILED_SNAPSHOT = state.Snapshot(
    updated_epoch=None, windows=MappingProxyType({}), order=(), extra_usage=None, problem="read_error"
)


def autostart_enabled() -> bool:
    """Включён ли автозапуск — статус юнита systemd --user (без sudo, без системных путей).

    Юнит ставит install.sh в ~/.config/systemd/user. Любая ошибка вызова
    (systemctl недоступен, юнит ещё не установлен) читается как «выключен» —
    это не поломка демона, а нормальное состояние до первой установки.
    """
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-enabled", "--quiet", _UNIT_NAME],
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("claude-usage-indicator: не удалось проверить автозапуск (systemctl):", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return False
    return result.returncode == 0


def set_autostart(enabled: bool) -> None:
    """Переключает автозапуск через systemctl --user enable|disable.

    Без --now: чекбокс в меню про будущие входы в систему, а не про то, жив
    ли текущий процесс демона — гасить его этим действием не нужно.
    """
    action = "enable" if enabled else "disable"
    try:
        subprocess.run(["systemctl", "--user", action, "--quiet", _UNIT_NAME], timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        print(f"claude-usage-indicator: не удалось {action} автозапуск (systemctl):", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


def on_details() -> None:
    """Заглушка: окно «Подробнее» откроет Task 4."""
    return None


class Indicator:
    """Держит AppIndicator3, последний снимок состояния и таймер опроса."""

    def __init__(self) -> None:
        self._indicator = AppIndicator3.Indicator.new(
            _APP_ID, _ICON_NORMAL, AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        # Обе иконки задаются один раз: AppIndicator сам показывает attention-иконку,
        # когда статус переключается в ATTENTION — не нужно менять иконку на каждый refresh.
        self._indicator.set_icon_full(_ICON_NORMAL, "лимиты в норме")
        self._indicator.set_attention_icon_full(_ICON_ALARM, "лимит почти исчерпан")
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._last_snapshot: state.Snapshot | None = None
        self.refresh()
        GLib.timeout_add_seconds(_POLL_INTERVAL_S, self._on_timeout)

    def _on_timeout(self) -> bool:
        self.refresh()
        return True  # GLib держит таймер, пока колбэк возвращает True

    def refresh(self) -> None:
        """Перечитывает файл состояния каждый тик; меню пересобирает только при смене снимка.

        Метка и статус тревоги — функции текущего времени (is_stale/panel_state),
        поэтому красятся на каждый тик независимо от того, поменялся ли снимок:
        иначе закрытый Claude Code (файл больше не пишется, снимок равен
        самому себе) никогда не показал бы «(устарело)» (находка ревью #2).
        """
        snapshot = state.read_state()
        rebuild_menu = snapshot != self._last_snapshot
        self._last_snapshot = snapshot
        self._apply(snapshot, rebuild_menu)

    def _apply(self, snapshot: state.Snapshot, rebuild_menu: bool) -> None:
        now = time.time()
        label, alarm = self._safe_panel_state(snapshot, now)
        self._indicator.set_label(label, "")
        status = AppIndicator3.IndicatorStatus.ATTENTION if alarm else AppIndicator3.IndicatorStatus.ACTIVE
        self._indicator.set_status(status)
        if rebuild_menu:
            self._safe_set_menu(snapshot, now)

    def _safe_panel_state(self, snapshot: state.Snapshot, now: float) -> tuple[str, bool]:
        """Рендер не должен убивать таймер опроса (находка ревью #3): падение — честное «нет данных»."""
        try:
            return bar.panel_state(snapshot, now)
        except Exception:
            print("claude-usage-indicator: ошибка рендера панели, показываю «нет данных»:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return bar.panel_state(_RENDER_FAILED_SNAPSHOT, now)

    def _safe_set_menu(self, snapshot: state.Snapshot, now: float) -> None:
        """Сборка меню — тоже вынесенный риск: одна плохая запись не должна остановить таймер."""
        try:
            menu = _build_menu(snapshot, now)
        except Exception:
            print("claude-usage-indicator: ошибка сборки меню, старое меню остаётся:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return
        self._indicator.set_menu(menu)


def _add_static_item(menu: Gtk.Menu, text: str) -> None:
    """Пункт-ярлык: заголовки окон, бар, время сброса, возраст данных ничего не делают по клику."""
    item = Gtk.MenuItem(label=text)
    item.set_sensitive(False)
    menu.append(item)


def _append_window_section(menu: Gtk.Menu, window: state.Window, now: float) -> None:
    _add_static_item(menu, window.label)
    _add_static_item(menu, f"{bar.render_bar(window.percent)} {bar.round_percent(window.percent)}%")
    _add_static_item(menu, bar.format_reset(window.resets_epoch, now))
    menu.append(Gtk.SeparatorMenuItem())


def _append_extra_usage(menu: Gtk.Menu, extra: state.ExtraUsage) -> None:
    _add_static_item(menu, f"Доп. расход                {bar.round_percent(extra.percent)}%")
    menu.append(Gtk.SeparatorMenuItem())


def _append_problem_hint(menu: Gtk.Menu, snapshot: state.Snapshot) -> None:
    """Когда окон нет, объясняет почему: «ещё не спрашивали» и «файл битый» иначе неотличимы."""
    if snapshot.windows:
        return
    text = bar.problem_text(snapshot.problem)
    if text is not None:
        _add_static_item(menu, text)
        menu.append(Gtk.SeparatorMenuItem())


def _append_age(menu: Gtk.Menu, snapshot: state.Snapshot, now: float) -> None:
    if snapshot.updated_epoch is None:
        _add_static_item(menu, "данные отсутствуют")
    else:
        _add_static_item(menu, f"данные {bar.format_age(snapshot.updated_epoch, now)}")


def _append_autostart_toggle(menu: Gtk.Menu) -> None:
    item = Gtk.CheckMenuItem(label="Запускать при входе в систему")
    item.set_active(autostart_enabled())
    item.connect("toggled", lambda checkbox: set_autostart(checkbox.get_active()))
    menu.append(item)


def _append_action_item(menu: Gtk.Menu, text: str, on_activate) -> None:
    item = Gtk.MenuItem(label=text)
    item.connect("activate", lambda _item: on_activate())
    menu.append(item)


def _build_menu(snapshot: state.Snapshot, now: float) -> Gtk.Menu:
    """Меню собирается заново на каждое изменение снимка: набор окон (model:*) не фиксирован."""
    menu = Gtk.Menu()
    for key in snapshot.order:
        _append_window_section(menu, snapshot.windows[key], now)
    _append_problem_hint(menu, snapshot)
    if snapshot.extra_usage is not None:
        _append_extra_usage(menu, snapshot.extra_usage)
    _append_age(menu, snapshot, now)
    _append_autostart_toggle(menu)
    _append_action_item(menu, "Подробнее…", on_details)
    menu.append(Gtk.SeparatorMenuItem())
    _append_action_item(menu, "Выход", Gtk.main_quit)
    menu.show_all()
    return menu


def main() -> None:
    """Точка входа демона: держит GTK-цикл, пока пользователь не выберет «Выход»."""
    Indicator()
    Gtk.main()
