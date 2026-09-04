"""Тесты чистых функций рендера (bar.py): ни GTK, ни файловой системы, ни time.time()."""
from __future__ import annotations

import os
import time
import unittest

from claude_usage_indicator.bar import (
    _plural_ru,
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
        window = Window(percent=1.0, resets_epoch=None, label="5 часов")
        self.assertEqual(panel_key("five_hour", window), "5h")

    def test_seven_day_short_key(self) -> None:
        window = Window(percent=1.0, resets_epoch=None, label="7 дней")
        self.assertEqual(panel_key("seven_day", window), "7d")

    def test_model_scoped_key_uses_window_label(self) -> None:
        window = Window(percent=1.0, resets_epoch=None, label="Fable")
        self.assertEqual(panel_key("model:fable", window), "Fable")


class PanelLabelTests(unittest.TestCase):
    def test_both_fixed_windows(self) -> None:
        windows = {
            "five_hour": Window(percent=42.3, resets_epoch=None, label="5 часов"),
            "seven_day": Window(percent=55.0, resets_epoch=None, label="7 дней"),
        }
        snapshot = _snapshot(windows, ("five_hour", "seven_day"))
        label = panel_label(snapshot)
        self.assertIn("5h ", label)
        self.assertIn("7d ", label)
        self.assertIn(" · ", label)  # разделитель ·
        self.assertTrue(label.startswith("5h"))

    def test_three_windows_including_model_scoped(self) -> None:
        windows = {
            "five_hour": Window(percent=42.3, resets_epoch=None, label="5 часов"),
            "seven_day": Window(percent=55.0, resets_epoch=None, label="7 дней"),
            "model:fable": Window(percent=21.0, resets_epoch=None, label="Fable"),
        }
        snapshot = _snapshot(windows, ("five_hour", "seven_day", "model:fable"))
        label = panel_label(snapshot)
        self.assertEqual(
            label,
            "5h ▓▓▓░░░░░ 42% · "
            "7d ▓▓▓▓░░░░ 55% · "
            "Fable ▓▓░░░░░░ 21%",
        )

    def test_only_one_window_no_separator(self) -> None:
        windows = {"five_hour": Window(percent=10.0, resets_epoch=None, label="5 часов")}
        snapshot = _snapshot(windows, ("five_hour",))
        label = panel_label(snapshot)
        self.assertNotIn("·", label)
        self.assertTrue(label.startswith("5h "))

    def test_no_windows_at_all_reports_no_data(self) -> None:
        snapshot = _snapshot({}, ())
        self.assertEqual(panel_label(snapshot), "Claude: нет данных")

    def test_problem_snapshot_reports_no_data(self) -> None:
        snapshot = _snapshot({}, (), updated_epoch=None, problem="no_file")
        self.assertEqual(panel_label(snapshot), "Claude: нет данных")


class IsAlarmTests(unittest.TestCase):
    def test_exactly_at_threshold_triggers_alarm(self) -> None:
        windows = {"five_hour": Window(percent=80.0, resets_epoch=None, label="5 часов")}
        snapshot = _snapshot(windows, ("five_hour",))
        self.assertTrue(is_alarm(snapshot))

    def test_just_below_threshold_does_not_trigger(self) -> None:
        windows = {"five_hour": Window(percent=79.9, resets_epoch=None, label="5 часов")}
        snapshot = _snapshot(windows, ("five_hour",))
        self.assertFalse(is_alarm(snapshot))

    def test_model_scoped_window_can_trigger_alarm(self) -> None:
        """Fable, упёршийся в потолок, это ровно тот случай, ради которого индикатор делается."""
        windows = {
            "five_hour": Window(percent=10.0, resets_epoch=None, label="5 часов"),
            "model:fable": Window(percent=95.0, resets_epoch=None, label="Fable"),
        }
        snapshot = _snapshot(windows, ("five_hour", "model:fable"))
        self.assertTrue(is_alarm(snapshot))

    def test_no_windows_never_alarms(self) -> None:
        self.assertFalse(is_alarm(_snapshot({}, ())))


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
        windows = {"five_hour": Window(percent=10.0, resets_epoch=None, label="5 часов")}
        snapshot = _snapshot(windows, ("five_hour",), updated_epoch=1000)
        label_fresh, alarm_fresh = panel_state(snapshot, now_epoch=1000)
        label_stale, alarm_stale = panel_state(snapshot, now_epoch=1000 + 3601)
        self.assertNotIn("устарело", label_fresh)
        self.assertIn("устарело", label_stale)
        self.assertFalse(alarm_fresh)
        self.assertFalse(alarm_stale)

    def test_alarm_adds_attention_prefix(self) -> None:
        windows = {"five_hour": Window(percent=95.0, resets_epoch=None, label="5 часов")}
        snapshot = _snapshot(windows, ("five_hour",), updated_epoch=1000)
        label, alarm = panel_state(snapshot, now_epoch=1000)
        self.assertTrue(alarm)
        self.assertTrue(label.startswith("⚠ "))

    def test_no_data_snapshot_reports_no_data_label(self) -> None:
        snapshot = _snapshot({}, (), updated_epoch=None)
        label, alarm = panel_state(snapshot, now_epoch=1_000_000)
        self.assertEqual(label, "Claude: нет данных (устарело)")
        self.assertFalse(alarm)


class PluralRuTests(unittest.TestCase):
    """Согласование русских числительных: 1/2/5 — три разные формы."""

    def test_one_form(self) -> None:
        self.assertEqual(_plural_ru(1, "минуту", "минуты", "минут"), "минуту")
        self.assertEqual(_plural_ru(21, "минуту", "минуты", "минут"), "минуту")

    def test_few_form(self) -> None:
        self.assertEqual(_plural_ru(2, "минуту", "минуты", "минут"), "минуты")
        self.assertEqual(_plural_ru(4, "минуту", "минуты", "минут"), "минуты")

    def test_many_form(self) -> None:
        self.assertEqual(_plural_ru(5, "минуту", "минуты", "минут"), "минут")
        self.assertEqual(_plural_ru(0, "минуту", "минуты", "минут"), "минут")

    def test_teen_exception_uses_many_form(self) -> None:
        """11-14 не согласуются по последней цифре: не «11 минуту», а «11 минут»."""
        self.assertEqual(_plural_ru(11, "минуту", "минуты", "минут"), "минут")
        self.assertEqual(_plural_ru(12, "минуту", "минуты", "минут"), "минут")


class FormatAgeTests(unittest.TestCase):
    def test_just_now(self) -> None:
        self.assertEqual(format_age(1000, now_epoch=1005), "только что")

    def test_one_minute_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=60), "1 минуту назад")

    def test_two_minutes_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=120), "2 минуты назад")

    def test_five_minutes_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=300), "5 минут назад")

    def test_hours_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=2 * 3600), "2 часа назад")

    def test_days_ago(self) -> None:
        self.assertEqual(format_age(0, now_epoch=5 * 86400), "5 дней назад")

    def test_half_boundary_rounds_up_not_to_even(self) -> None:
        """2.5 минуты: builtin round() банковски округлил бы вниз к 2 (чётное) — тут нужен round-half-up."""
        self.assertEqual(format_age(0, now_epoch=150), "3 минуты назад")


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
        self.assertEqual(format_reset(None, now_epoch=0), "время сброса неизвестно")

    def test_future_reset_shows_countdown(self) -> None:
        # 23:10 UTC в тот же день; now на 1ч23м раньше.
        reset_epoch = 23 * 3600 + 10 * 60
        now_epoch = reset_epoch - (1 * 3600 + 23 * 60)
        self.assertEqual(format_reset(reset_epoch, now_epoch), "сброс в 23:10, через 1 ч 23 м")

    def test_past_reset_has_no_countdown(self) -> None:
        reset_epoch = 10 * 3600
        now_epoch = reset_epoch + 60
        self.assertEqual(format_reset(reset_epoch, now_epoch), "сброс в 10:00")


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
        self.assertEqual(problem_text("no_limits"), "цифры появятся после первого запроса в Claude Code")

    def test_no_file_message_matches_brief_wording(self) -> None:
        self.assertEqual(
            problem_text("no_file"),
            "Claude Code ещё ни разу не запускался с установленным хуком",
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


if __name__ == "__main__":
    unittest.main()
