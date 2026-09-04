"""AyatanaAppIndicator3-обвязка: метка в панели, меню, таймер опроса раз в 10 секунд.

GTK-код тестами не покрыт — нужен живой X11/Wayland-сеанс с шиной indicator,
проверяется живым прогоном в Task 5. Здесь сознательно тонкий слой поверх
bar.py/state.py: вся логика, которую можно протестировать без GTK, живёт там.
"""
from __future__ import annotations

import time

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
_ATTENTION_PREFIX = "⚠ "
_STALE_SUFFIX = " (устарело)"


def autostart_enabled() -> bool:
    """Заглушка: состояние автозапуска подключит Task 3."""
    return False


def set_autostart(enabled: bool) -> None:
    """Заглушка: запись состояния автозапуска на диск сделает Task 3."""
    return None


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
        """Перечитывает файл состояния; перерисовывает панель только при реальном изменении."""
        snapshot = state.read_state()
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot
        self._apply(snapshot)

    def _apply(self, snapshot: state.Snapshot) -> None:
        now = time.time()
        alarm = bar.is_alarm(snapshot)
        label = bar.panel_label(snapshot)
        if alarm:
            label = _ATTENTION_PREFIX + label
        if bar.is_stale(snapshot, now):
            label += _STALE_SUFFIX
        self._indicator.set_label(label, "")
        status = AppIndicator3.IndicatorStatus.ATTENTION if alarm else AppIndicator3.IndicatorStatus.ACTIVE
        self._indicator.set_status(status)
        self._indicator.set_menu(_build_menu(snapshot, now))


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
