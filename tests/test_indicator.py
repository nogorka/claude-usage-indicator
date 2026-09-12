"""Тесты indicator.py: сборка меню трея и устойчивость Indicator — без живого GTK.

gi/Gtk/AyatanaAppIndicator3 подменены фейками из _fake_gtk.py (см. его докстринг).
indicator.py импортируется заново в каждом тесте, чтобы имя Gtk внутри него всегда
указывало на текущий фейк, а не на тот, что остался от предыдущего теста —
поэтому шаблон setUp/tearDown, а не импорт один раз на модуль.

_build_menu и _build_content уже принимают reading/профили параметрами, а не читают
глобали, — производственный код не потребовал ни одного шва ради тестируемости.
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from unittest.mock import Mock, patch

from claude_usage_indicator import bar, profiles, state

# Каталог тестов лежит в sys.path только при запуске через `unittest discover -s tests`;
# без этой вставки отдельный модуль (`python3 -m unittest tests.test_window`) падает на импорте.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import _fake_gtk

_TARGET_MODULES = ("claude_usage_indicator.indicator", "claude_usage_indicator.window")


def _snapshot(
    five: float, seven: float, updated: int | None = 1_000, extra_usage: state.ExtraUsage | None = None
) -> state.Snapshot:
    return state.Snapshot(
        updated_epoch=updated,
        windows={
            "five_hour": state.Window(percent=five, resets_epoch=9_000, label="5 hours"),
            "seven_day": state.Window(percent=seven, resets_epoch=9_000, label="7 days"),
        },
        order=("five_hour", "seven_day"),
        extra_usage=extra_usage,
    )


def _entry(
    profile_id: str,
    label: str,
    five: float = 10.0,
    seven: float = 20.0,
    extra_usage: state.ExtraUsage | None = None,
) -> state.ProfileSnapshot:
    return state.ProfileSnapshot(profile_id=profile_id, label=label, snapshot=_snapshot(five, seven, extra_usage=extra_usage))


def _reading(*entries: state.ProfileSnapshot, unreadable: tuple[str, ...] = ()) -> state.Reading:
    return state.Reading(profiles={e.profile_id: e for e in entries}, unreadable=unreadable)


def _find(menu: object, label: str, item_type: type | None = None) -> object:
    for item in menu.items:  # type: ignore[attr-defined]
        if item_type is not None and not isinstance(item, item_type):
            continue
        if getattr(item, "label", None) == label:
            return item
    raise AssertionError(f"item {label!r} not found among {[getattr(i, 'label', i) for i in menu.items]!r}")


class IndicatorTestCase(unittest.TestCase):
    """Общая обвязка: фейковый GTK на время теста, indicator.py импортирован заново."""

    def setUp(self) -> None:
        saved = _fake_gtk.install()
        self.addCleanup(_fake_gtk.uninstall, saved)
        for name in _TARGET_MODULES:
            sys.modules.pop(name, None)
        self.addCleanup(lambda: [sys.modules.pop(n, None) for n in _TARGET_MODULES])
        self.indicator = importlib.import_module("claude_usage_indicator.indicator")


class MenuCompositionTests(IndicatorTestCase):
    """Порядок и содержимое _build_menu — то, что видит пользователь при клике на трей."""

    def _build(self, reading, discovered=(), autostart=False, on_toggled=None):
        # discover_profiles патчится, а не сам ProfileCache: кэш — тонкая обёртка
        # вокруг discover_profiles, и свежий инстанс на каждый _build не тянет TTL
        # между тестами (expires_at начинается в прошлом — первый get всегда сканирует).
        with patch.object(profiles, "discover_profiles", return_value=list(discovered)):
            return self.indicator._build_menu(
                reading, now=8_100, autostart_state=autostart,
                on_autostart_toggled=on_toggled or (lambda _v: None),
                profile_cache=profiles.ProfileCache(),
            )

    def test_profile_sections_appear_in_profile_sort_key_order(self):
        # id "aaa" идёт раньше "default" алфавитно — наивная сортировка по id ошиблась бы
        # именно здесь, а не на паре id, где алфавит и profile_sort_key случайно совпадают.
        reading = _reading(_entry("aaa", "own"), _entry("default", "work"))
        menu = self._build(reading)
        labels = [getattr(item, "label", None) for item in menu.items]
        self.assertLess(labels.index("work"), labels.index("own"))

    def test_section_text_matches_bar_menu_section_lines_verbatim(self):
        entry = _entry("personal", "own")
        reading = _reading(entry)
        menu = self._build(reading)
        expected_lines = bar.menu_section_lines(entry, now_epoch=8_100)
        actual_labels = [item.label for item in menu.items if hasattr(item, "label")]
        for line in expected_lines:
            self.assertIn(line, actual_labels)

    def test_extra_usage_line_appears_between_windows_and_the_age_line(self):
        # Регрессия против критерия приёмки №3 docs/PLAN.md: переезд на мультипрофильность
        # выкинул эту строку из меню, хотя окно «Подробнее» её сохранило.
        extra = state.ExtraUsage(percent=31.0, used_credits=12.4, monthly_limit=40.0, currency="USD")
        entry = _entry("default", "work", extra_usage=extra)
        menu = self._build(_reading(entry))
        labels = [getattr(item, "label", None) for item in menu.items]
        section_lines = bar.menu_section_lines(entry, now_epoch=8_100)
        self.assertIn(bar.extra_usage_line(extra), labels)
        self.assertLess(labels.index(bar.extra_usage_line(extra)), labels.index(section_lines[-1]))

    def test_no_extra_usage_line_for_a_profile_without_extra_usage(self):
        entry = _entry("default", "work")
        menu = self._build(_reading(entry))
        labels = [getattr(item, "label", None) for item in menu.items]
        self.assertFalse(any(isinstance(label, str) and label.startswith("Extra usage") for label in labels))

    def test_static_section_lines_are_not_clickable(self):
        reading = _reading(_entry("default", "work"))
        menu = self._build(reading)
        first_line = bar.menu_section_lines(reading.profiles["default"], now_epoch=8_100)[0]
        item = _find(menu, first_line)
        self.assertFalse(item.sensitive)

    def test_separator_follows_every_profile_section(self):
        reading = _reading(_entry("default", "work"), _entry("personal", "own"))
        menu = self._build(reading)
        work_lines = bar.menu_section_lines(reading.profiles["default"], now_epoch=8_100)
        last_item_index = [i.label for i in menu.items if hasattr(i, "label")].index(work_lines[-1])
        # индекс в полном списке items (не только среди подписанных) — сепаратор идёт сразу за ним
        flat = menu.items
        pos = flat.index(_find(menu, work_lines[-1]))
        self.assertIsInstance(flat[pos + 1], _fake_gtk.SeparatorMenuItem)

    def test_unreadable_line_placed_after_profile_sections(self):
        reading = _reading(_entry("default", "work"), unreadable=("broken.json",))
        menu = self._build(reading)
        labels = [getattr(item, "label", None) for item in menu.items]
        self.assertLess(labels.index("work"), labels.index(bar.unreadable_line(("broken.json",))))

    def test_no_unreadable_item_when_reading_has_none(self):
        reading = _reading(_entry("default", "work"))
        menu = self._build(reading)
        labels = [getattr(item, "label", None) for item in menu.items]
        self.assertFalse(any(isinstance(label, str) and label.startswith("couldn't read") for label in labels))

    def test_one_launch_item_per_discovered_profile_after_unreadable_section(self):
        profile = profiles.Profile(id="work", label="Work", config_dir=__import__("pathlib").Path("/tmp/.claude-work"))
        reading = _reading(_entry("default", "work"), unreadable=("broken.json",))
        menu = self._build(reading, discovered=[profile])
        labels = [getattr(item, "label", None) for item in menu.items]
        unreadable_index = labels.index(bar.unreadable_line(("broken.json",)))
        launch_index = labels.index("Open Claude — Work")
        self.assertGreater(launch_index, unreadable_index)

    def test_launch_item_labeled_open_claude_dash_profile_label(self):
        profile = profiles.Profile(id="personal", label="Личный", config_dir=__import__("pathlib").Path("/tmp/x"))
        menu = self._build(_reading(), discovered=[profile])
        self.assertTrue(_find(menu, "Open Claude — Личный").sensitive)

    def test_autostart_checkbox_reflects_passed_state(self):
        menu_on = self._build(_reading(), autostart=True)
        menu_off = self._build(_reading(), autostart=False)
        self.assertTrue(_find(menu_on, "Start at login", _fake_gtk.CheckMenuItem).active)
        self.assertFalse(_find(menu_off, "Start at login", _fake_gtk.CheckMenuItem).active)

    def test_toggling_autostart_checkbox_calls_the_callback_with_new_state(self):
        on_toggled = Mock()
        menu = self._build(_reading(), autostart=False, on_toggled=on_toggled)
        checkbox = _find(menu, "Start at login", _fake_gtk.CheckMenuItem)
        checkbox.toggle(True)
        on_toggled.assert_called_once_with(True)

    def test_details_item_activation_calls_on_details(self):
        with patch.object(self.indicator, "on_details", Mock()) as mock_on_details:
            menu = self._build(_reading())
            _find(menu, "Details…").activate()
            mock_on_details.assert_called_once_with()

    def test_quit_item_activation_calls_gtk_main_quit(self):
        menu = self._build(_reading())
        _find(menu, "Quit").activate()
        self.indicator.Gtk.main_quit.assert_called_once_with()

    def test_quit_item_is_last_and_preceded_by_a_separator(self):
        menu = self._build(_reading())
        self.assertIsInstance(menu.items[-1], _fake_gtk.MenuItem)
        self.assertEqual(menu.items[-1].label, "Quit")
        self.assertIsInstance(menu.items[-2], _fake_gtk.SeparatorMenuItem)

    def test_menu_is_shown_after_being_built(self):
        menu = self._build(_reading())
        self.assertTrue(menu.shown)


class EmptyReadingMenuTests(IndicatorTestCase):
    """Ни одного профиля на диске — меню не падает и не показывает пустых секций."""

    def _build(self, reading=None):
        with patch.object(profiles, "discover_profiles", return_value=[]):
            return self.indicator._build_menu(
                reading if reading is not None else _reading(), now=1, autostart_state=False,
                on_autostart_toggled=lambda _v: None, profile_cache=profiles.ProfileCache(),
            )

    def test_no_profile_sections_and_no_unreadable_item(self):
        menu = self._build()
        labels = [getattr(item, "label", None) for item in menu.items]
        self.assertNotIn(None, [label for label in labels if isinstance(label, str) and "%" in label])
        self.assertFalse(any(isinstance(label, str) and label.startswith("couldn't read") for label in labels))

    def test_still_has_the_fixed_tail_items(self):
        menu = self._build()
        labels = [getattr(item, "label", None) for item in menu.items]
        self.assertIn("Start at login", labels)
        self.assertIn("Details…", labels)
        self.assertIn("Quit", labels)

    def test_empty_reading_panel_state_is_no_data(self):
        reading = _reading()
        label, alarm = bar.panel_state_for(reading, now_epoch=1)
        self.assertEqual(label, bar.panel_label_no_data())
        self.assertFalse(alarm)


class LaunchClickTests(IndicatorTestCase):
    """Клик по пункту запуска дёргает launcher.launch; ошибка не глотается молча."""

    def _profile(self):
        from pathlib import Path

        return profiles.Profile(id="work", label="Work", config_dir=Path("/tmp/.claude-work"))

    def _menu_with_launch_item(self):
        with patch.object(profiles, "discover_profiles", return_value=[self._profile()]):
            menu = self.indicator._build_menu(
                _reading(), now=1, autostart_state=False, on_autostart_toggled=lambda _v: None,
                profile_cache=profiles.ProfileCache(),
            )
        return _find(menu, "Open Claude — Work")

    def test_activating_launch_item_calls_launcher_launch_with_the_profile(self):
        item = self._menu_with_launch_item()
        with patch.object(self.indicator.launcher, "launch", return_value=None) as mock_launch:
            item.activate()
        mock_launch.assert_called_once_with(self._profile())

    def test_on_launch_shows_dialog_with_error_text_and_runs_it(self):
        profile = self._profile()
        with patch.object(self.indicator.launcher, "launch", return_value="boom: no terminal"), \
             patch.object(self.indicator, "_show_launch_error", Mock()) as mock_show:
            self.indicator._on_launch(profile)
        mock_show.assert_called_once_with("boom: no terminal")

    def test_on_launch_shows_nothing_when_launch_succeeds(self):
        profile = self._profile()
        with patch.object(self.indicator.launcher, "launch", return_value=None), \
             patch.object(self.indicator, "_show_launch_error", Mock()) as mock_show:
            self.indicator._on_launch(profile)
        mock_show.assert_not_called()

    def test_show_launch_error_dialog_is_run_and_destroyed(self):
        created = []
        real_dialog_cls = self.indicator.Gtk.MessageDialog

        class RecordingDialog(real_dialog_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)

        with patch.object(self.indicator.Gtk, "MessageDialog", RecordingDialog):
            self.indicator._show_launch_error("could not start")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].text, "could not start")
        self.assertTrue(created[0].ran)
        self.assertTrue(created[0].destroyed)


class IndicatorResilienceTests(IndicatorTestCase):
    """Indicator: таймер опроса переживает падение рендера и падение сборки меню."""

    def _make_indicator(self, reading):
        self.enterContext(patch.object(self.indicator, "autostart_enabled", return_value=False))
        self.enterContext(patch.object(state, "read_all_states", return_value=reading))
        return self.indicator.Indicator()

    def test_poll_timer_registered_with_ten_second_interval(self):
        indicator = self._make_indicator(_reading())
        self.assertEqual(self.indicator.GLib.timers, [(10, indicator._on_timeout)])

    def test_empty_reading_sets_no_data_label_on_the_panel(self):
        indicator = self._make_indicator(_reading())
        self.assertEqual(indicator._indicator.label, bar.panel_label_no_data())

    def test_render_failure_for_one_profile_falls_back_to_no_data_without_raising(self):
        entry = _entry("default", "work")
        reading = _reading(entry)
        indicator = self._make_indicator(reading)
        real_panel_state_for = bar.panel_state_for

        def flaky(reading_arg, now_arg):
            if reading_arg is reading:
                raise ValueError("malformed snapshot")
            return real_panel_state_for(reading_arg, now_arg)

        with patch.object(bar, "panel_state_for", side_effect=flaky):
            indicator.refresh()  # не должно бросить исключение
        self.assertEqual(indicator._indicator.label, bar.panel_label_no_data())

    def test_menu_build_failure_keeps_the_previous_menu_and_does_not_raise(self):
        indicator = self._make_indicator(_reading())
        menu_before = indicator._indicator.menu
        self.assertIsNotNone(menu_before)
        with patch.object(profiles, "discover_profiles", side_effect=OSError("disk gone")):
            # Кэш ProfileCache уже прогрет предыдущим успешным refresh — обнуляем его,
            # иначе TTL спрячет патч на discover_profiles за кэшированным результатом.
            indicator._profile_cache = profiles.ProfileCache()
            indicator.refresh()  # не должно бросить исключение
        self.assertIs(indicator._indicator.menu, menu_before)

    def test_on_timeout_keeps_returning_true_after_a_menu_build_failure(self):
        indicator = self._make_indicator(_reading())
        with patch.object(profiles, "discover_profiles", side_effect=OSError("disk gone")):
            indicator._profile_cache = profiles.ProfileCache()  # см. тест выше про TTL
            self.assertTrue(indicator._on_timeout())

    def test_autostart_toggle_callback_persists_the_new_state_and_calls_systemctl(self):
        indicator = self._make_indicator(_reading())
        with patch.object(self.indicator, "set_autostart", Mock()) as mock_set:
            indicator._on_autostart_toggled(True)
        mock_set.assert_called_once_with(True)
        self.assertTrue(indicator._autostart_cached)


if __name__ == "__main__":
    unittest.main()
