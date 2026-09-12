"""Поддельные gi/Gtk/GLib/AyatanaAppIndicator3 для тестов indicator.py и window.py.

Оба модуля на импорте зовут `gi.require_version` и тянут реальный GTK — живого
X11/Wayland-сеанса и AppIndicator-шины в общем прогоне тестов обычно нет. Подкладываем
в sys.modules объекты с ровно той поверхностью API, которую indicator.py/window.py
реально используют, и они записывают, что у них запросили, — тесты утверждают про
состав меню/окна через эти записи, а не через настоящий GTK.

Имя без префикса test* — unittest discover его не подхватывает как тестовый модуль.
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import Mock

_MODULE_KEYS = (
    "gi",
    "gi.repository",
    "gi.repository.Gtk",
    "gi.repository.GLib",
    "gi.repository.AyatanaAppIndicator3",
)


class _Enum:
    """Метка с уникальной идентичностью: код только присваивает и сравнивает её,
    не заглядывает внутрь — своего значения ей не нужно."""


class Widget:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class _Signalled(Widget):
    """Общий примитив connect/emit для виджетов с сигналами (MenuItem, Window)."""

    def __init__(self) -> None:
        super().__init__()
        self._handlers: dict[str, list[Any]] = {}

    def connect(self, signal: str, callback: Any) -> None:
        self._handlers.setdefault(signal, []).append(callback)

    def _emit(self, signal: str) -> None:
        for callback in self._handlers.get(signal, []):
            callback(self)


class MenuItem(_Signalled):
    def __init__(self, label: str = "") -> None:
        super().__init__()
        self.label = label
        self.sensitive = True

    def set_sensitive(self, value: bool) -> None:
        self.sensitive = value

    def get_label(self) -> str:
        return self.label

    def activate(self) -> None:
        self._emit("activate")


class CheckMenuItem(MenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label)
        self.active = False

    def set_active(self, value: bool) -> None:
        self.active = value

    def get_active(self) -> bool:
        return self.active

    def toggle(self, value: bool) -> None:
        """Симулирует клик пользователя: меняет состояние и эмитит "toggled"."""
        self.active = value
        self._emit("toggled")


class SeparatorMenuItem(Widget):
    pass


class Menu(Widget):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[Any] = []
        self.shown = False

    def append(self, item: Any) -> None:
        self.items.append(item)

    def show_all(self) -> None:
        self.shown = True


class Box(Widget):
    def __init__(self, orientation: Any = None, spacing: int = 0) -> None:
        super().__init__()
        self.orientation = orientation
        self.spacing = spacing
        self.children: list[Any] = []
        self.shown = False

    def pack_start(self, widget: Any, expand: bool, fill: bool, padding: int) -> None:
        self.children.append(widget)

    def show_all(self) -> None:
        self.shown = True


class Label(Widget):
    def __init__(self, label: str = "", xalign: float = 0.0) -> None:
        super().__init__()
        self.text = label
        self.xalign = xalign


class LevelBar(Widget):
    def __init__(self) -> None:
        super().__init__()
        self.min_value: float | None = None
        self.max_value: float | None = None
        self.value: float | None = None

    def set_min_value(self, value: float) -> None:
        self.min_value = value

    def set_max_value(self, value: float) -> None:
        self.max_value = value

    def set_value(self, value: float) -> None:
        self.value = value


class Separator(Widget):
    def __init__(self, orientation: Any = None) -> None:
        super().__init__()
        self.orientation = orientation


class Window(_Signalled):
    def __init__(self, title: str = "") -> None:
        super().__init__()
        self.title = title
        self.default_size: tuple[int, int] | None = None
        self.border_width: int | None = None
        self.shown = False
        self.present_count = 0
        self._child: Any = None

    def set_default_size(self, width: int, height: int) -> None:
        self.default_size = (width, height)

    def set_border_width(self, width: int) -> None:
        self.border_width = width

    def get_child(self) -> Any:
        return self._child

    def add(self, widget: Any) -> None:
        self._child = widget

    def remove(self, widget: Any) -> None:
        self._child = None

    def show_all(self) -> None:
        self.shown = True

    def present(self) -> None:
        self.present_count += 1

    def destroy(self) -> None:
        super().destroy()
        self._emit("destroy")


class MessageDialog(Widget):
    def __init__(
        self,
        transient_for: Any = None,
        flags: int = 0,
        message_type: Any = None,
        buttons: Any = None,
        text: str = "",
    ) -> None:
        super().__init__()
        self.transient_for = transient_for
        self.flags = flags
        self.message_type = message_type
        self.buttons = buttons
        self.text = text
        self.ran = False

    def run(self) -> None:
        self.ran = True


class Orientation:
    VERTICAL = _Enum()
    HORIZONTAL = _Enum()


class MessageType:
    ERROR = _Enum()


class ButtonsType:
    OK = _Enum()


def _make_gtk_module() -> types.ModuleType:
    module = types.ModuleType("gi.repository.Gtk")
    module.Menu = Menu
    module.MenuItem = MenuItem
    module.CheckMenuItem = CheckMenuItem
    module.SeparatorMenuItem = SeparatorMenuItem
    module.Box = Box
    module.Label = Label
    module.LevelBar = LevelBar
    module.Separator = Separator
    module.Window = Window
    module.MessageDialog = MessageDialog
    module.Orientation = Orientation
    module.MessageType = MessageType
    module.ButtonsType = ButtonsType
    module.main_quit = Mock(name="Gtk.main_quit")
    return module


class _GLibStub:
    """Таймеры не тикают сами — тест вызывает записанный колбэк вручную."""

    def __init__(self) -> None:
        self.timers: list[tuple[int, Any]] = []

    def timeout_add_seconds(self, interval_s: int, callback: Any) -> int:
        self.timers.append((interval_s, callback))
        return len(self.timers)


def _make_glib_module() -> types.ModuleType:
    module = types.ModuleType("gi.repository.GLib")
    stub = _GLibStub()
    module.timeout_add_seconds = stub.timeout_add_seconds
    module.timers = stub.timers
    return module


class Indicator:
    def __init__(self, app_id: str, icon: str, category: Any) -> None:
        self.app_id = app_id
        self.icon = icon
        self.category = category
        self.icon_full: tuple[str, str] | None = None
        self.attention_icon_full: tuple[str, str] | None = None
        self.status: Any = None
        self.label: str | None = None
        self.menu: Any = None

    def set_icon_full(self, icon: str, description: str) -> None:
        self.icon_full = (icon, description)

    def set_attention_icon_full(self, icon: str, description: str) -> None:
        self.attention_icon_full = (icon, description)

    def set_status(self, status: Any) -> None:
        self.status = status

    def set_label(self, label: str, guide: str) -> None:
        self.label = label

    def set_menu(self, menu: Any) -> None:
        self.menu = menu


class _IndicatorFactory:
    """Реальный AppIndicator3.Indicator.new — фабричный staticmethod, не конструктор."""

    new = staticmethod(lambda app_id, icon, category: Indicator(app_id, icon, category))


class IndicatorCategory:
    APPLICATION_STATUS = _Enum()


class IndicatorStatus:
    ACTIVE = _Enum()
    ATTENTION = _Enum()


def _make_appindicator_module() -> types.ModuleType:
    module = types.ModuleType("gi.repository.AyatanaAppIndicator3")
    module.Indicator = _IndicatorFactory
    module.IndicatorCategory = IndicatorCategory
    module.IndicatorStatus = IndicatorStatus
    return module


def install() -> dict[str, Any]:
    """Кладёт поддельные модули в sys.modules и отдаёт снимок для симметричного uninstall."""
    saved = {key: sys.modules.get(key) for key in _MODULE_KEYS}

    gi_module = types.ModuleType("gi")
    gi_module.require_version = lambda *_args, **_kwargs: None

    repository_module = types.ModuleType("gi.repository")
    gtk_module = _make_gtk_module()
    glib_module = _make_glib_module()
    appindicator_module = _make_appindicator_module()
    repository_module.Gtk = gtk_module
    repository_module.GLib = glib_module
    repository_module.AyatanaAppIndicator3 = appindicator_module
    gi_module.repository = repository_module

    sys.modules["gi"] = gi_module
    sys.modules["gi.repository"] = repository_module
    sys.modules["gi.repository.Gtk"] = gtk_module
    sys.modules["gi.repository.GLib"] = glib_module
    sys.modules["gi.repository.AyatanaAppIndicator3"] = appindicator_module
    return saved


def uninstall(saved: dict[str, Any]) -> None:
    """Возвращает sys.modules в точности к состоянию до install(), а не просто чистит ключи."""
    for key, value in saved.items():
        if value is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = value
