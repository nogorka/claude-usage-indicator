"""Тесты window.py: содержимое окна «Подробнее» и жизненный цикл синглтона — без живого GTK.

См. докстринг _fake_gtk.py про причину и приём подмены. window.py импортируется заново
в каждом тесте — иначе модульный синглтон `_window` тёк бы между тестами, а привязка
имени Gtk внутри модуля держалась бы за фейк из предыдущего теста.

_build_content уже принимает reading/now параметрами — шва в продакшн-коде не потребовалось.
"""
from __future__ import annotations

import importlib
import sys
import unittest
from unittest.mock import Mock, patch

from claude_usage_indicator import bar, state

import _fake_gtk

_TARGET_MODULES = ("claude_usage_indicator.window", "claude_usage_indicator.indicator")


def _snapshot(five: float = 10.0, seven: float = 20.0, updated: int | None = 1_000, extra_usage=None) -> state.Snapshot:
    return state.Snapshot(
        updated_epoch=updated,
        windows={
            "five_hour": state.Window(percent=five, resets_epoch=9_000, label="5 hours"),
            "seven_day": state.Window(percent=seven, resets_epoch=9_000, label="7 days"),
        },
        order=("five_hour", "seven_day"),
        extra_usage=extra_usage,
    )


def _entry(profile_id: str, label: str, **snapshot_kwargs) -> state.ProfileSnapshot:
    return state.ProfileSnapshot(profile_id=profile_id, label=label, snapshot=_snapshot(**snapshot_kwargs))


def _reading(*entries: state.ProfileSnapshot, unreadable: tuple[str, ...] = ()) -> state.Reading:
    return state.Reading(profiles={e.profile_id: e for e in entries}, unreadable=unreadable)


class WindowTestCase(unittest.TestCase):
    """Общая обвязка: фейковый GTK на время теста, window.py импортирован заново."""

    def setUp(self) -> None:
        saved = _fake_gtk.install()
        self.addCleanup(_fake_gtk.uninstall, saved)
        for name in _TARGET_MODULES:
            sys.modules.pop(name, None)
        self.addCleanup(lambda: [sys.modules.pop(n, None) for n in _TARGET_MODULES])
        self.window = importlib.import_module("claude_usage_indicator.window")


class ContentCompositionTests(WindowTestCase):
    """_build_content(reading, now) — что окажется в окне «Подробнее»."""

    def _labels(self, box):
        return [child.text for child in box.children if isinstance(child, _fake_gtk.Label)]

    def test_profile_sections_appear_in_profile_sort_key_order(self):
        # id "aaa" идёт раньше "default" алфавитно — наивная сортировка по id ошиблась бы
        # именно здесь, а не на паре id, где алфавит и profile_sort_key случайно совпадают.
        reading = _reading(_entry("aaa", "own", five=1.0, seven=1.0), _entry("default", "work", five=1.0, seven=1.0))
        box = self.window._build_content(reading, now=8_100)
        labels = self._labels(box)
        self.assertLess(labels.index("work"), labels.index("own"))

    def test_header_and_age_lines_come_from_bar_menu_section_lines(self):
        entry = _entry("default", "work", five=61.0, seven=11.0)
        reading = _reading(entry)
        box = self.window._build_content(reading, now=8_100)
        section_lines = bar.menu_section_lines(entry, now_epoch=8_100)
        labels = self._labels(box)
        self.assertIn(section_lines[0], labels)
        self.assertIn(section_lines[-1], labels)

    def test_no_ascii_bar_characters_appear_anywhere_in_the_details_window(self):
        entry = _entry("default", "work", five=61.0, seven=11.0)
        reading = _reading(entry)
        box = self.window._build_content(reading, now=8_100)
        for text in self._labels(box):
            self.assertNotIn("▓", text)
            self.assertNotIn("░", text)

    def test_each_limit_window_gets_its_own_level_bar_matching_the_effective_percent(self):
        entry = _entry("default", "work", five=61.0, seven=11.0)
        box = self.window._build_content(_reading(entry), now=8_100)
        level_bars = [child for child in box.children if isinstance(child, _fake_gtk.LevelBar)]
        self.assertEqual([lb.value for lb in level_bars], [0.61, 0.11])

    def test_window_label_and_reset_text_are_shown_for_each_limit_window(self):
        entry = _entry("default", "work", five=61.0, seven=11.0)
        box = self.window._build_content(_reading(entry), now=8_100)
        labels = self._labels(box)
        for wl in bar.window_lines(entry.snapshot, now_epoch=8_100):
            self.assertIn(wl.label, labels)
            self.assertTrue(any(wl.reset_text in text for text in labels))

    def test_model_scoped_window_gets_a_level_bar_with_its_own_label(self):
        snapshot = state.Snapshot(
            updated_epoch=1_000,
            windows={"model:fable": state.Window(percent=21.0, resets_epoch=9_000, label="Fable")},
            order=("model:fable",),
            extra_usage=None,
        )
        entry = state.ProfileSnapshot(profile_id="default", label="work", snapshot=snapshot)
        box = self.window._build_content(_reading(entry), now=8_100)
        level_bars = [child for child in box.children if isinstance(child, _fake_gtk.LevelBar)]
        self.assertEqual(len(level_bars), 1)
        self.assertAlmostEqual(level_bars[0].value, 0.21)
        self.assertIn("Fable", self._labels(box))

    def test_age_line_is_the_last_label_before_the_separator(self):
        entry = _entry("default", "work")
        reading = _reading(entry)
        box = self.window._build_content(reading, now=8_100)
        expected_age_line = bar.menu_section_lines(entry, now_epoch=8_100)[-1]
        age_index = box.children.index(_next_label_with_text(box, expected_age_line))
        self.assertIsInstance(box.children[age_index + 1], _fake_gtk.Separator)

    def test_extra_usage_inserted_between_windows_and_the_age_line(self):
        extra = state.ExtraUsage(percent=31.0, used_credits=12.4, monthly_limit=40.0, currency="USD")
        entry = _entry("default", "work", extra_usage=extra)
        reading = _reading(entry)
        box = self.window._build_content(reading, now=8_100)
        labels = self._labels(box)
        age_line = bar.menu_section_lines(entry, now_epoch=8_100)[-1]
        self.assertLess(labels.index("Extra usage"), labels.index(age_line))

    def test_extra_usage_level_bar_gets_the_clamped_fraction_and_a_rounded_percent_label(self):
        # extra_usage — не окно лимита, его LevelBar последний: после двух баров окон
        # five_hour/seven_day, которые теперь тоже рисуются виджетами.
        extra = state.ExtraUsage(percent=31.0, used_credits=None, monthly_limit=None, currency=None)
        entry = _entry("default", "work", extra_usage=extra)
        box = self.window._build_content(_reading(entry), now=8_100)
        level_bars = [child for child in box.children if isinstance(child, _fake_gtk.LevelBar)]
        self.assertAlmostEqual(level_bars[-1].value, 0.31)
        self.assertIn(f"{bar.round_percent(31.0)}%", self._labels(box))

    def test_no_extra_usage_produces_no_extra_usage_label_and_no_extra_level_bar(self):
        # Без extra_usage бары остаются — по одному на окно лимита (five_hour, seven_day);
        # отсутствовать должен именно лишний, extra_usage'ный.
        entry = _entry("default", "work", extra_usage=None)
        box = self.window._build_content(_reading(entry), now=8_100)
        self.assertNotIn("Extra usage", self._labels(box))
        level_bars = [child for child in box.children if isinstance(child, _fake_gtk.LevelBar)]
        self.assertEqual(len(level_bars), len(entry.snapshot.windows))

    def test_extra_usage_is_not_shown_twice(self):
        # Меню (indicator.py) вставляет свою текстовую строку "Extra usage NN%" через
        # bar.extra_usage_line, отдельно от bar.menu_section_lines. Окно строит содержимое
        # из тех же menu_section_lines плюс собственные виджеты extra_usage — если бы строку
        # когда-нибудь занесли внутрь menu_section_lines, эта секция получила бы и текстовую
        # строку из цикла по lines, и виджеты _append_extra_usage поверх неё.
        extra = state.ExtraUsage(percent=31.0, used_credits=None, monthly_limit=None, currency=None)
        entry = _entry("default", "work", extra_usage=extra)
        lines = bar.menu_section_lines(entry, now_epoch=8_100)
        self.assertFalse(any("Extra usage" in line for line in lines))
        box = self.window._build_content(_reading(entry), now=8_100)
        extra_mentions = [text for text in self._labels(box) if "Extra usage" in text]
        self.assertEqual(extra_mentions, ["Extra usage"])

    def test_separator_follows_every_profile_section(self):
        reading = _reading(_entry("default", "work"), _entry("personal", "own"))
        box = self.window._build_content(reading, now=8_100)
        separator_count = sum(isinstance(child, _fake_gtk.Separator) for child in box.children)
        self.assertEqual(separator_count, 2)

    def test_unreadable_line_is_the_last_child_when_profiles_are_present(self):
        reading = _reading(_entry("default", "work"), unreadable=("broken.json",))
        box = self.window._build_content(reading, now=8_100)
        self.assertIsInstance(box.children[-1], _fake_gtk.Label)
        self.assertEqual(box.children[-1].text, bar.unreadable_line(("broken.json",)))

    def test_no_profiles_at_all_shows_the_first_run_message(self):
        box = self.window._build_content(_reading(), now=8_100)
        self.assertEqual(self._labels(box), [bar.no_profiles_line(_reading())])

    def test_no_profiles_but_unreadable_files_present_shows_only_the_unreadable_line(self):
        reading = _reading(unreadable=("junk.json",))
        box = self.window._build_content(reading, now=8_100)
        self.assertEqual(self._labels(box), [bar.unreadable_line(("junk.json",))])


def _next_label_with_text(box, text):
    for child in box.children:
        if isinstance(child, _fake_gtk.Label) and child.text == text:
            return child
    raise AssertionError(f"label {text!r} not found")


class SingletonLifecycleTests(WindowTestCase):
    """show_details_window(): один Gtk.Window на все профили, таймер обновления, закрытие."""

    def _recording_window_class(self, sink):
        base = self.window.Gtk.Window

        class RecordingWindow(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                sink.append(self)

        return RecordingWindow

    def test_first_call_creates_a_titled_window_and_shows_it(self):
        with patch.object(state, "read_all_states", return_value=_reading()):
            self.window.show_details_window()
        win = self.window._window
        self.assertIsNotNone(win)
        self.assertEqual(win.title, "Claude Code — Limits")
        self.assertTrue(win.shown)

    def test_second_call_presents_the_existing_window_instead_of_creating_a_new_one(self):
        created = []
        with patch.object(self.window.Gtk, "Window", self._recording_window_class(created)), \
             patch.object(state, "read_all_states", return_value=_reading()):
            self.window.show_details_window()
            self.window.show_details_window()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].present_count, 1)

    def test_registers_a_ten_second_poll_timer_on_success(self):
        with patch.object(state, "read_all_states", return_value=_reading()):
            self.window.show_details_window()
        self.assertEqual(len(self.window.GLib.timers), 1)
        self.assertEqual(self.window.GLib.timers[0][0], 10)

    def test_failed_initial_content_destroys_the_window_and_leaves_no_singleton(self):
        created = []
        with patch.object(self.window.Gtk, "Window", self._recording_window_class(created)), \
             patch.object(state, "read_all_states", side_effect=OSError("disk gone")):
            self.window.show_details_window()
        self.assertIsNone(self.window._window)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].destroyed)
        self.assertEqual(self.window.GLib.timers, [])

    def test_timer_refresh_failure_destroys_the_window_and_resets_the_singleton(self):
        with patch.object(state, "read_all_states", return_value=_reading()):
            self.window.show_details_window()
        win = self.window._window
        timer_callback = self.window.GLib.timers[0][1]
        with patch.object(state, "read_all_states", side_effect=OSError("disk gone")):
            result = timer_callback()
        self.assertFalse(result)
        self.assertTrue(win.destroyed)
        self.assertIsNone(self.window._window)

    def test_timer_survives_and_keeps_polling_while_content_refreshes_fine(self):
        with patch.object(state, "read_all_states", return_value=_reading()):
            self.window.show_details_window()
            timer_callback = self.window.GLib.timers[0][1]
            result = timer_callback()
        self.assertTrue(result)
        self.assertIsNotNone(self.window._window)

    def test_on_timeout_short_circuits_without_touching_state_once_window_already_replaced(self):
        stale_win = self.window.Gtk.Window(title="stale")
        with patch.object(state, "read_all_states", Mock(side_effect=AssertionError("must not be called"))):
            result = self.window._on_timeout(stale_win)
        self.assertFalse(result)

    def test_closing_the_window_clears_the_singleton_so_the_next_call_creates_a_new_one(self):
        created = []
        with patch.object(self.window.Gtk, "Window", self._recording_window_class(created)), \
             patch.object(state, "read_all_states", return_value=_reading()):
            self.window.show_details_window()
            created[0].destroy()  # пользователь закрыл окно через WM
            self.assertIsNone(self.window._window)
            self.window.show_details_window()
        self.assertEqual(len(created), 2)


if __name__ == "__main__":
    unittest.main()
