"""Тесты чистых функций рендера (bar.py): ни GTK, ни файловой системы, ни time.time()."""
from __future__ import annotations

import os
import time
import unittest

from claude_usage_indicator import bar, state
from claude_usage_indicator.bar import (
    _plural_en,
    format_age,
    format_reset,
    is_alarm,
    is_stale,
    panel_key,
    panel_label,
    panel_state,
    problem_text,
    render_bar,
)
from claude_usage_indicator.state import _MAX_RESETS_EPOCH, Snapshot, Window


def _snapshot(windows: dict, order: tuple, updated_epoch=1_000_000, problem=None) -> Snapshot:
    """Собирает Snapshot напрямую, минуя чтение файла — bar.py не знает про state.py-парсинг."""
    return Snapshot(
        updated_epoch=updated_epoch,
        windows=windows,
        order=order,
        extra_usage=None,
        problem=problem,
    )


class RenderBarTests(unittest.TestCase):
    """filled = floor(percent/100*cells + 0.5), зажатый в 0..cells."""

    def test_zero_percent_is_fully_empty(self) -> None:
        self.assertEqual(render_bar(0), "░" * 8)

    def test_just_below_half_cell_threshold(self) -> None:
        # 6.2% -> 6.2/100*8+0.5 = 0.996 -> floor 0
        self.assertEqual(render_bar(6.2), "░" * 8)

    def test_just_above_half_cell_threshold(self) -> None:
        # 6.3% -> 6.3/100*8+0.5 = 1.004 -> floor 1
        self.assertEqual(render_bar(6.3), "▓" + "░" * 7)

    def test_eighty_percent(self) -> None:
        # 80/100*8+0.5 = 6.9 -> floor 6
        self.assertEqual(render_bar(80), "▓" * 6 + "░" * 2)

    def test_ninety_nine_point_five_rounds_up_to_full(self) -> None:
        # 99.5/100*8+0.5 = 8.46 -> floor 8, зажато в потолок cells=8
        self.assertEqual(render_bar(99.5), "▓" * 8)

    def test_hundred_percent_is_fully_filled(self) -> None:
        self.assertEqual(render_bar(100), "▓" * 8)

    def test_out_of_range_percent_is_clamped_not_crashed(self) -> None:
        self.assertEqual(render_bar(150), "▓" * 8)
        self.assertEqual(render_bar(-10), "░" * 8)


class PanelKeyTests(unittest.TestCase):
    def test_five_hour_short_key(self) -> None:
        window = Window(percent=1.0, resets_epoch=None, label="5 hours")
        self.assertEqual(panel_key("five_hour", window), "5h")

    def test_seven_day_short_key(self) -> None:
        window = Window(percent=1.0, resets_epoch=None, label="7 days")
        self.assertEqual(panel_key("seven_day", window), "7d")

    def test_model_scoped_key_uses_window_label(self) -> None:
        window = Window(percent=1.0, resets_epoch=None, label="Fable")
        self.assertEqual(panel_key("model:fable", window), "Fable")


class PanelLabelTests(unittest.TestCase):
    def test_both_fixed_windows(self) -> None:
        windows = {
            "five_hour": Window(percent=42.3, resets_epoch=None, label="5 hours"),
            "seven_day": Window(percent=55.0, resets_epoch=None, label="7 days"),
        }
        snapshot = _snapshot(windows, ("five_hour", "seven_day"))
        label = panel_label(snapshot, now_epoch=1_000_000)
        self.assertIn("5h ", label)
        self.assertIn("7d ", label)
        self.assertIn(" · ", label)  # разделитель ·
        self.assertTrue(label.startswith("5h"))

    def test_three_windows_including_model_scoped(self) -> None:
        windows = {
            "five_hour": Window(percent=42.3, resets_epoch=None, label="5 hours"),
            "seven_day": Window(percent=55.0, resets_epoch=None, label="7 days"),
            "model:fable": Window(percent=21.0, resets_epoch=None, label="Fable"),
        }
        snapshot = _snapshot(windows, ("five_hour", "seven_day", "model:fable"))
        label = panel_label(snapshot, now_epoch=1_000_000)
        self.assertEqual(
            label,
            "5h ▓▓▓░░░░░ 42% · "
            "7d ▓▓▓▓░░░░ 55% · "
            "Fable ▓▓░░░░░░ 21%",
        )

    def test_only_one_window_no_separator(self) -> None:
        windows = {"five_hour": Window(percent=10.0, resets_epoch=None, label="5 hours")}
        snapshot = _snapshot(windows, ("five_hour",))
        label = panel_label(snapshot, now_epoch=1_000_000)
        self.assertNotIn("·", label)
        self.assertTrue(label.startswith("5h "))

    def test_no_windows_at_all_reports_no_data(self) -> None:
        snapshot = _snapshot({}, ())
        self.assertEqual(panel_label(snapshot, now_epoch=1_000_000), "Claude: no data")

    def test_problem_snapshot_reports_no_data(self) -> None:
        snapshot = _snapshot({}, (), updated_epoch=None, problem="no_file")
        self.assertEqual(panel_label(snapshot, now_epoch=1_000_000), "Claude: no data")


class IsAlarmTests(unittest.TestCase):
    def test_exactly_at_threshold_triggers_alarm(self) -> None:
        windows = {"five_hour": Window(percent=80.0, resets_epoch=None, label="5 hours")}
        snapshot = _snapshot(windows, ("five_hour",))
        self.assertTrue(is_alarm(snapshot, now_epoch=1_000_000))

    def test_just_below_threshold_does_not_trigger(self) -> None:
        windows = {"five_hour": Window(percent=79.9, resets_epoch=None, label="5 hours")}
        snapshot = _snapshot(windows, ("five_hour",))
        self.assertFalse(is_alarm(snapshot, now_epoch=1_000_000))

    def test_model_scoped_window_can_trigger_alarm(self) -> None:
        """Fable, упёршийся в потолок, это ровно тот случай, ради которого индикатор делается."""
        windows = {
            "five_hour": Window(percent=10.0, resets_epoch=None, label="5 hours"),
            "model:fable": Window(percent=95.0, resets_epoch=None, label="Fable"),
        }
        snapshot = _snapshot(windows, ("five_hour", "model:fable"))
        self.assertTrue(is_alarm(snapshot, now_epoch=1_000_000))

    def test_no_windows_never_alarms(self) -> None:
        self.assertFalse(is_alarm(_snapshot({}, ()), now_epoch=1_000_000))


class IsStaleTests(unittest.TestCase):
    def test_exactly_at_boundary_is_not_stale(self) -> None:
        snapshot = _snapshot({}, (), updated_epoch=1000)
        self.assertFalse(is_stale(snapshot, now_epoch=1000 + 3600, max_age_s=3600))

    def test_one_second_past_boundary_is_stale(self) -> None:
        snapshot = _snapshot({}, (), updated_epoch=1000)
        self.assertTrue(is_stale(snapshot, now_epoch=1000 + 3601, max_age_s=3600))

    def test_missing_updated_epoch_is_always_stale(self) -> None:
        snapshot = _snapshot({}, (), updated_epoch=None)
        self.assertTrue(is_stale(snapshot, now_epoch=1_000_000))


class PanelStateTests(unittest.TestCase):
    """panel_state пересчитывает метку и тревогу от now_epoch — чинит вечно-свежий статус.

    _apply() в indicator.py перерисовывает метку каждый тик таймера, а не только
    когда файл состояния реально изменился: один и тот же снимок обязан
    становиться «(устарело)» по мере течения времени, без нового чтения файла.
    """

    def test_same_snapshot_turns_stale_as_time_passes_without_new_read(self) -> None:
        windows = {"five_hour": Window(percent=10.0, resets_epoch=None, label="5 hours")}
        snapshot = _snapshot(windows, ("five_hour",), updated_epoch=1000)
        label_fresh, alarm_fresh = panel_state(snapshot, now_epoch=1000)
        label_stale, alarm_stale = panel_state(snapshot, now_epoch=1000 + 3601)
        self.assertNotIn("stale", label_fresh)
        self.assertIn("stale", label_stale)
        self.assertFalse(alarm_fresh)
        self.assertFalse(alarm_stale)

    def test_alarm_adds_attention_prefix(self) -> None:
        windows = {"five_hour": Window(percent=95.0, resets_epoch=None, label="5 hours")}
        snapshot = _snapshot(windows, ("five_hour",), updated_epoch=1000)
        label, alarm = panel_state(snapshot, now_epoch=1000)
        self.assertTrue(alarm)
        self.assertTrue(label.startswith("⚠ "))

    def test_no_data_snapshot_reports_no_data_label(self) -> None:
        snapshot = _snapshot({}, (), updated_epoch=None)
        label, alarm = panel_state(snapshot, now_epoch=1_000_000)
        self.assertEqual(label, "Claude: no data (stale)")
        self.assertFalse(alarm)


class PluralEnTests(unittest.TestCase):
    """Согласование английских числительных: 1 — singular, всё остальное — plural."""

    def test_one_form(self) -> None:
        self.assertEqual(_plural_en(1, "minute"), "minute")

    def test_plural_form(self) -> None:
        for n in (0, 2, 5, 11, 21):
            with self.subTest(n=n):
                self.assertEqual(_plural_en(n, "minute"), "minutes")


class FormatAgeTests(unittest.TestCase):
    def test_just_now(self) -> None:
        self.assertEqual(format_age(1000, now_epoch=1005), "just now")

    def test_one_minute_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=60), "1 minute ago")

    def test_two_minutes_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=120), "2 minutes ago")

    def test_five_minutes_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=300), "5 minutes ago")

    def test_hours_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=2 * 3600), "2 hours ago")

    def test_days_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=5 * 86400), "5 days ago")

    def test_half_boundary_rounds_up_not_to_even(self) -> None:
        """2.5 минуты: builtin round() банковски округлил бы вниз к 2 (чётное) — тут нужен round-half-up."""
        self.assertEqual(format_age(0, now_epoch=150), "3 minutes ago")


class _FixedTzMixin:
    """Фиксирует TZ=UTC на время теста, чтобы format_reset не зависел от машины исполнителя."""

    def setUp(self) -> None:
        super().setUp()
        self._old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        self.addCleanup(self._restore_tz)

    def _restore_tz(self) -> None:
        if self._old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old_tz
        time.tzset()


class FormatResetTests(_FixedTzMixin, unittest.TestCase):
    def test_none_reports_unknown(self) -> None:
        self.assertEqual(format_reset(None, now_epoch=0), "reset time unknown")

    def test_future_reset_shows_countdown(self) -> None:
        # 23:10 UTC в тот же день; now на 1ч23м раньше.
        reset_epoch = 23 * 3600 + 10 * 60
        now_epoch = reset_epoch - (1 * 3600 + 23 * 60)
        self.assertEqual(format_reset(reset_epoch, now_epoch), "resets at 23:10, in 1h 23m")

    def test_past_reset_has_no_countdown(self) -> None:
        reset_epoch = 10 * 3600
        now_epoch = reset_epoch + 60
        self.assertEqual(
            format_reset(reset_epoch, now_epoch),
            "window reset at 10:00 on 01.01; next window starts with the first session",
        )


class FormatResetExtremeTzTests(unittest.TestCase):
    """Проверяет запас _MAX_RESETS_EPOCH в самом восточном часовом поясе (UTC+14)."""

    def setUp(self) -> None:
        self._old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Pacific/Kiritimati"
        time.tzset()
        self.addCleanup(self._restore_tz)

    def _restore_tz(self) -> None:
        if self._old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old_tz
        time.tzset()

    def test_upper_bound_does_not_overflow_past_datetime_max(self) -> None:
        # astimezone() на UTC+14 у конца 9999 года без запаса роняет OverflowError.
        format_reset(_MAX_RESETS_EPOCH, now_epoch=0)


class ProblemTextTests(unittest.TestCase):
    """Человеческие формулировки problem для меню — «нет данных» больше не одно на всё."""

    _KNOWN_CODES = (
        "no_file",
        "read_error",
        "empty_file",
        "bad_json",
        "bad_root",
        "bad_schema",
        "bad_encoding",
        "no_limits",
    )

    def test_none_has_no_text(self) -> None:
        """problem=None — валидный файл с пустыми limits, объяснять нечего."""
        self.assertIsNone(problem_text(None))

    def test_every_known_code_has_non_empty_human_text(self) -> None:
        for code in self._KNOWN_CODES:
            with self.subTest(code=code):
                text = problem_text(code)
                self.assertIsInstance(text, str)
                self.assertTrue(text)

    def test_no_limits_message_matches_brief_wording(self) -> None:
        self.assertEqual(
            problem_text("no_limits"), "numbers will appear after the first request in Claude Code"
        )

    def test_no_file_message_matches_brief_wording(self) -> None:
        self.assertEqual(
            problem_text("no_file"),
            "Claude Code has never run with the hook installed",
        )

    def test_known_codes_have_distinct_messages(self) -> None:
        """Иначе разные проблемы снова неотличимы друг от друга в меню — та же болезнь, что чинили."""
        messages = {problem_text(code) for code in self._KNOWN_CODES}
        self.assertEqual(len(messages), len(self._KNOWN_CODES))

    def test_unknown_code_gets_generic_text_not_a_crash(self) -> None:
        text = problem_text("some_future_hook_version_code")
        self.assertIsInstance(text, str)
        self.assertTrue(text)
        self.assertNotIn(text, {problem_text(code) for code in self._KNOWN_CODES})


class ExpiredWindowTests(unittest.TestCase):
    def _window(self, percent, resets_epoch):
        return state.Window(percent=percent, resets_epoch=resets_epoch, label="5h")

    def test_window_past_its_reset_is_expired(self):
        self.assertTrue(bar.is_expired(self._window(87.0, 1000), now_epoch=2000))

    def test_window_before_its_reset_is_not_expired(self):
        self.assertFalse(bar.is_expired(self._window(87.0, 3000), now_epoch=2000))

    def test_window_without_reset_epoch_is_never_expired(self):
        self.assertFalse(bar.is_expired(self._window(87.0, None), now_epoch=2000))

    def test_expired_window_reports_zero_not_the_stale_number(self):
        self.assertEqual(bar.effective_percent(self._window(87.0, 1000), now_epoch=2000), 0.0)

    def test_live_window_reports_its_own_number(self):
        self.assertEqual(bar.effective_percent(self._window(87.0, 3000), now_epoch=2000), 87.0)

    def test_expired_window_does_not_raise_the_alarm(self):
        snapshot = state.Snapshot(
            updated_epoch=500,
            windows={"five_hour": self._window(87.0, 1000)},
            order=("five_hour",),
            extra_usage=None,
            problem=None,
        )
        self.assertFalse(bar.is_alarm(snapshot, now_epoch=2000))

    def test_expired_window_renders_zero_in_the_panel(self):
        snapshot = state.Snapshot(
            updated_epoch=500,
            windows={"five_hour": self._window(87.0, 1000)},
            order=("five_hour",),
            extra_usage=None,
            problem=None,
        )
        self.assertIn("0%", bar.panel_label(snapshot, now_epoch=2000))
        self.assertNotIn("87%", bar.panel_label(snapshot, now_epoch=2000))

    def test_expired_reset_text_names_the_moment_and_promises_nothing(self):
        text = bar.format_reset(1000, now_epoch=2000)
        self.assertIn("window reset at", text)
        self.assertIn("first session", text)
        self.assertNotIn("in 0h", text)


if __name__ == "__main__":
    unittest.main()
