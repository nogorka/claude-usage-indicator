"""AyatanaAppIndicator3-обвязка: метка в панели, меню, таймер опроса раз в 10 секунд.

GTK-код тестами не покрыт — нужен живой X11/Wayland-сеанс с шиной indicator,
проверяется только живым запуском демона. Здесь сознательно тонкий слой поверх
bar.py/state.py: вся логика, которую можно протестировать без GTK, живёт там.
"""
from __future__ import annotations

import subprocess
import sys
import time
import traceback
from types import MappingProxyType
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import GLib, Gtk
from gi.repository import AyatanaAppIndicator3 as AppIndicator3

from . import bar, launcher, profiles, state, window

_APP_ID = "claude-usage-indicator"
_ICON_NORMAL = "utilities-system-monitor"
_ICON_ALARM = "dialog-warning"
_POLL_INTERVAL_S = 10
_UNIT_NAME = "claude-usage-indicator.service"
# Пустой каталог для честного «нет данных», когда рендер реального каталога упал:
# panel_state_for на пустом Reading гарантированно не бросает — сам по себе fallback безопасен.
_RENDER_FAILED_READING = state.Reading(profiles=MappingProxyType({}), unreadable=())


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
        print("claude-usage-indicator: couldn't check autostart status (systemctl):", file=sys.stderr)
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
        result = subprocess.run(["systemctl", "--user", action, "--quiet", _UNIT_NAME], timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        print(f"claude-usage-indicator: couldn't {action} autostart (systemctl):", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return
    if result.returncode != 0:
        print(
            f"claude-usage-indicator: systemctl {action} {_UNIT_NAME} exited with code {result.returncode}",
            file=sys.stderr,
        )


def on_details() -> None:
    """Открывает окно "Details…" с барами по каждому лимиту."""
    window.show_details_window()


class Indicator:
    """Держит AppIndicator3, последний прочитанный каталог состояния и таймер опроса."""

    def __init__(self) -> None:
        self._indicator = AppIndicator3.Indicator.new(
            _APP_ID, _ICON_NORMAL, AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        # Обе иконки задаются один раз: AppIndicator сам показывает attention-иконку,
        # когда статус переключается в ATTENTION — не нужно менять иконку на каждый refresh.
        self._indicator.set_icon_full(_ICON_NORMAL, "limits normal")
        self._indicator.set_attention_icon_full(_ICON_ALARM, "limit almost exhausted")
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        # Кэш, а не запрос к systemctl на каждый тик: с пересборкой меню
        # каждые 10 секунд (см. refresh) опрос systemd на каждый тик означал
        # бы форк процесса раз в 10 секунд навсегда, включая простой демона.
        # Обновляется по факту переключения пользователем — см. _on_autostart_toggled.
        self._autostart_cached = autostart_enabled()
        self.refresh()
        GLib.timeout_add_seconds(_POLL_INTERVAL_S, self._on_timeout)

    def _on_timeout(self) -> bool:
        self.refresh()
        return True  # GLib держит таймер, пока колбэк возвращает True

    def _on_autostart_toggled(self, enabled: bool) -> None:
        set_autostart(enabled)
        self._autostart_cached = enabled

    def refresh(self) -> None:
        """Перечитывает каталог состояния и пересобирает панель с меню на каждый тик.

        Меню раньше пересобиралось только при смене снимка — но текст его
        пунктов (format_reset/format_age) зависит от текущего времени, а не
        только от снимка, и застывал между сменами данных.
        """
        reading = state.read_all_states()
        self._apply(reading)

    def _apply(self, reading: state.Reading) -> None:
        now = time.time()
        label, alarm = self._safe_panel_state(reading, now)
        self._indicator.set_label(label, "")
        status = AppIndicator3.IndicatorStatus.ATTENTION if alarm else AppIndicator3.IndicatorStatus.ACTIVE
        self._indicator.set_status(status)
        self._safe_set_menu(reading, now)

    def _safe_panel_state(self, reading: state.Reading, now: float) -> tuple[str, bool]:
        """Рендер не должен убивать таймер опроса: падение — честное «нет данных»."""
        try:
            return bar.panel_state_for(reading, now)
        except Exception:
            print('claude-usage-indicator: panel render failed, showing "no data":', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return bar.panel_state_for(_RENDER_FAILED_READING, now)

    def _safe_set_menu(self, reading: state.Reading, now: float) -> None:
        """Сборка меню — тоже вынесенный риск: одна плохая запись не должна остановить таймер."""
        try:
            menu = _build_menu(reading, now, self._autostart_cached, self._on_autostart_toggled)
        except Exception:
            print("claude-usage-indicator: menu build failed, keeping the old menu:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return
        self._indicator.set_menu(menu)


def _add_static_item(menu: Gtk.Menu, text: str) -> None:
    """Пункт-ярлык: заголовки окон, бар, время сброса, возраст данных ничего не делают по клику."""
    item = Gtk.MenuItem(label=text)
    item.set_sensitive(False)
    menu.append(item)


def _append_profile_section(menu: Gtk.Menu, entry: state.ProfileSnapshot, now: float) -> None:
    """Секция одного профиля: окна и возраст — из bar.menu_section_lines, доп. расход —
    отдельной строкой между ними (см. bar.extra_usage_line про то, почему не внутри
    menu_section_lines). menu_section_lines всегда кладёт возраст последней строкой —
    единственной, добавленной после цикла по окнам, — поэтому срез безопасен."""
    lines = bar.menu_section_lines(entry, now)
    for line in lines[:-1]:
        _add_static_item(menu, line)
    extra_line = bar.extra_usage_line(entry.snapshot.extra_usage)
    if extra_line is not None:
        _add_static_item(menu, extra_line)
    _add_static_item(menu, lines[-1])
    menu.append(Gtk.SeparatorMenuItem())


def _append_unreadable(menu: Gtk.Menu, reading: state.Reading) -> None:
    text = bar.unreadable_line(reading.unreadable)
    if text is not None:
        _add_static_item(menu, text)
        menu.append(Gtk.SeparatorMenuItem())


def _append_autostart_toggle(menu: Gtk.Menu, autostart_state: bool, on_toggled: Callable[[bool], None]) -> None:
    item = Gtk.CheckMenuItem(label="Start at login")
    item.set_active(autostart_state)
    item.connect("toggled", lambda checkbox: on_toggled(checkbox.get_active()))
    menu.append(item)


def _append_action_item(menu: Gtk.Menu, text: str, on_activate: Callable[[], None]) -> None:
    item = Gtk.MenuItem(label=text)
    item.connect("activate", lambda _item: on_activate())
    menu.append(item)


def _on_launch(profile: profiles.Profile) -> None:
    """Клик по пункту запуска. Ошибку логирует сам launcher.launch — здесь только диалог
    для человека: молчаливый провал кнопки неотличим от «ничего не произошло»."""
    error = launcher.launch(profile)
    if error is not None:
        _show_launch_error(error)


def _show_launch_error(text: str) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=None,
        flags=0,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=text,
    )
    dialog.run()
    dialog.destroy()


def _append_launch_items(menu: Gtk.Menu, discovered: list[profiles.Profile]) -> None:
    """Пункты запуска строятся по профилям с диска, а не по файлам состояния: у только что
    заведённого профиля файла состояния ещё нет, а кнопка нужна сразу."""
    for profile in discovered:
        _append_action_item(menu, f"Open Claude — {profile.label}", lambda profile=profile: _on_launch(profile))


def _build_menu(
    reading: state.Reading, now: float, autostart_state: bool, on_autostart_toggled: Callable[[bool], None]
) -> Gtk.Menu:
    """Меню собирается заново на каждый тик: набор профилей и окон (model:*) не
    фиксирован, и текст части пунктов зависит от текущего времени (см. Indicator.refresh)."""
    menu = Gtk.Menu()
    for profile_id in sorted(reading.profiles, key=profiles.profile_sort_key):
        _append_profile_section(menu, reading.profiles[profile_id], now)
    _append_unreadable(menu, reading)
    _append_launch_items(menu, profiles.discover_profiles())
    _append_autostart_toggle(menu, autostart_state, on_autostart_toggled)
    _append_action_item(menu, "Details…", on_details)
    menu.append(Gtk.SeparatorMenuItem())
    _append_action_item(menu, "Quit", Gtk.main_quit)
    menu.show_all()
    return menu


def main() -> None:
    """Точка входа демона: держит GTK-цикл, пока пользователь не выберет "Quit"."""
    Indicator()
    Gtk.main()
