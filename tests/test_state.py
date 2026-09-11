"""Тесты чтения и разбора файлов состояния (state.py). Только stdlib unittest."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from claude_usage_indicator import state
from claude_usage_indicator.state import ExtraUsage, Window, read_all_states, state_dir


def _read_snapshot(dir_path: Path, payload: dict) -> state.Snapshot:
    """Кладёт тело снимка в каталог состояния и возвращает разобранный снимок.

    Разбор тела — внутренняя функция, поэтому проверяется через единственную
    публичную дверь к нему: файл без блока `profile` читается как профиль default.
    """
    (dir_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    return read_all_states(dir_path).profiles["default"].snapshot


class SnapshotParsingTests(unittest.TestCase):
    """Файл соответствует схеме Ш0 полностью или частично — данные читаются."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir_path = Path(self._tmp.name)

    def test_two_fixed_windows_present(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1788547623,
            "limits": {
                "five_hour": {"percent": 42.3, "resets_epoch": 1788550200},
                "seven_day": {"percent": 55.0, "resets_epoch": 1788700000},
            },
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.updated_epoch, 1788547623)
        self.assertEqual(
            snapshot.windows,
            {
                "five_hour": Window(percent=42.3, resets_epoch=1788550200, label="5 hours"),
                "seven_day": Window(percent=55.0, resets_epoch=1788700000, label="7 days"),
            },
        )
        self.assertEqual(snapshot.order, ("five_hour", "seven_day"))

    def test_only_one_window_present(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 100,
            "limits": {"five_hour": {"percent": 10.0}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(set(snapshot.windows), {"five_hour"})
        self.assertIsNone(snapshot.windows["five_hour"].resets_epoch)

    def test_resets_epoch_explicit_null_is_none(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 100,
            "limits": {"five_hour": {"percent": 10.0, "resets_epoch": None}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertIsNone(snapshot.windows["five_hour"].resets_epoch)

    def test_model_scoped_window_uses_label_from_file(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 100,
            "limits": {"model:fable": {"percent": 21.0, "resets_epoch": 1788700000, "label": "Fable"}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(
            snapshot.windows["model:fable"],
            Window(percent=21.0, resets_epoch=1788700000, label="Fable"),
        )

    def test_three_windows_with_model_scoped(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {
                "five_hour": {"percent": 42.3, "resets_epoch": 1},
                "seven_day": {"percent": 55.0, "resets_epoch": 2},
                "model:fable": {"percent": 21.0, "resets_epoch": 3, "label": "Fable"},
            },
            "order": ["five_hour", "seven_day", "model:fable"],
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(set(snapshot.windows), {"five_hour", "seven_day", "model:fable"})
        self.assertEqual(snapshot.order, ("five_hour", "seven_day", "model:fable"))

    def test_empty_limits_yields_no_windows(self) -> None:
        """Сессия ещё не сделала ни одного запроса модели — валидное пустое состояние."""
        payload = {"schema": 1, "updated_epoch": 100, "limits": {}}
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})
        self.assertEqual(snapshot.order, ())
        self.assertEqual(snapshot.updated_epoch, 100)

    def test_updated_epoch_missing_becomes_none(self) -> None:
        """updated_epoch не входит в перечень структурных полей брифа — деградирует мягко."""
        payload = {"schema": 1, "limits": {}}
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertIsNone(snapshot.updated_epoch)

    def test_extra_usage_parsed_when_present(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {},
            "extra_usage": {
                "percent": 31.0,
                "used_credits": 12.4,
                "monthly_limit": 40.0,
                "currency": "USD",
            },
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(
            snapshot.extra_usage,
            ExtraUsage(percent=31.0, used_credits=12.4, monthly_limit=40.0, currency="USD"),
        )

    def test_extra_usage_absent_is_none(self) -> None:
        payload = {"schema": 1, "updated_epoch": 1, "limits": {}}
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertIsNone(snapshot.extra_usage)

    def test_extra_usage_garbage_percent_drops_whole_block(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {},
            "extra_usage": {"percent": "31.0"},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertIsNone(snapshot.extra_usage)

    def test_extra_usage_nan_percent_drops_whole_block(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {},
            "extra_usage": {"percent": float("nan")},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertIsNone(snapshot.extra_usage)

    def test_windows_mapping_is_read_only(self) -> None:
        """Snapshot.windows объявлен Mapping, но был обычным dict — владелец ссылки мог его портить."""
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": 10.0}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        with self.assertRaises(TypeError):
            snapshot.windows["five_hour"] = None


class OrderTests(unittest.TestCase):
    """order задаёт порядок отображения; отсутствующий/битый — детерминированный дефолт."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir_path = Path(self._tmp.name)

    def _payload(self, order: object) -> dict:
        return {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {
                "model:zzz": {"percent": 1.0, "label": "Zzz"},
                "seven_day": {"percent": 2.0},
                "five_hour": {"percent": 3.0},
                "model:aaa": {"percent": 4.0, "label": "Aaa"},
            },
            "order": order,
        }

    def test_missing_order_defaults_to_fixed_then_alphabetical(self) -> None:
        payload = self._payload(None)
        del payload["order"]
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.order, ("five_hour", "seven_day", "model:aaa", "model:zzz"))

    def test_order_not_a_list_falls_back_to_default(self) -> None:
        snapshot = _read_snapshot(self.dir_path, self._payload("five_hour"))
        self.assertEqual(snapshot.order, ("five_hour", "seven_day", "model:aaa", "model:zzz"))

    def test_order_referencing_nonexistent_key_is_filtered_out(self) -> None:
        order = ["ghost", "seven_day", "five_hour"]
        snapshot = _read_snapshot(self.dir_path, self._payload(order))
        # ключи из order, которых нет в limits, отфильтровываются; оставшиеся окна
        # (не упомянутые в order) дописываются в конец по алфавиту.
        self.assertEqual(snapshot.order, ("seven_day", "five_hour", "model:aaa", "model:zzz"))

    def test_order_is_honoured_when_valid_and_complete(self) -> None:
        order = ["model:zzz", "model:aaa", "seven_day", "five_hour"]
        snapshot = _read_snapshot(self.dir_path, self._payload(order))
        self.assertEqual(snapshot.order, tuple(order))


class WindowGarbageTests(unittest.TestCase):
    """Мусор в одном окне не должен портить остальные окна."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir_path = Path(self._tmp.name)

    def test_window_without_percent_is_dropped(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {
                "five_hour": {"resets_epoch": 1},
                "seven_day": {"percent": 55.0},
            },
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(set(snapshot.windows), {"seven_day"})

    def test_window_with_percent_as_string_is_dropped(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": "42.3"}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_window_with_percent_as_bool_is_dropped(self) -> None:
        """bool — подкласс int в Python, поэтому исключается отдельной проверкой."""
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": True}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_window_with_resets_epoch_as_string_is_dropped(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": 10.0, "resets_epoch": "soon"}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_model_window_without_label_is_dropped(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"model:fable": {"percent": 21.0}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_model_window_with_non_string_label_is_dropped(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"model:fable": {"percent": 21.0, "label": 5}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_unknown_window_key_is_ignored(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"some_future_window": {"percent": 1.0}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_limits_not_an_object_yields_no_windows(self) -> None:
        payload = {"schema": 1, "updated_epoch": 1, "limits": [1, 2, 3]}
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_percent_nan_is_dropped(self) -> None:
        """json.loads по умолчанию разбирает NaN — round_percent на нём падает ValueError ниже по потоку."""
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": float("nan")}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_percent_infinity_is_dropped(self) -> None:
        """render_bar на Infinity падает OverflowError ниже по потоку."""
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": float("inf")}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_resets_epoch_above_max_is_dropped(self) -> None:
        """format_reset на 10**18 падает OSError: Value too large ниже по потоку."""
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": 10.0, "resets_epoch": 10**18}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_resets_epoch_negative_is_dropped(self) -> None:
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": 10.0, "resets_epoch": -1}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})

    def test_resets_epoch_at_upper_bound_is_kept(self) -> None:
        """Конец 9999 года минус запас на часовой пояс — валидная граница, а не мусор."""
        upper_bound = 253402300799 - 14 * 3600
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": 10.0, "resets_epoch": upper_bound}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows["five_hour"].resets_epoch, upper_bound)

    def test_resets_epoch_past_upper_bound_is_dropped(self) -> None:
        """Значение, которое ещё в пределах конца 9999 года, но без запаса на TZ — мусор."""
        payload = {
            "schema": 1,
            "updated_epoch": 1,
            "limits": {"five_hour": {"percent": 10.0, "resets_epoch": 253402300799}},
        }
        snapshot = _read_snapshot(self.dir_path, payload)
        self.assertEqual(snapshot.windows, {})


class StateDirTests(unittest.TestCase):
    """Приоритет источников каталога: CLAUDE_USAGE_STATE > XDG_STATE_HOME > ~/.local/state."""

    def test_env_override_wins(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CLAUDE_USAGE_STATE": "/tmp/custom/latest.json", "XDG_STATE_HOME": "/tmp/xdg"},
        ):
            self.assertEqual(state_dir(), Path("/tmp/custom"))

    def test_xdg_state_home_used_when_no_override(self) -> None:
        env = dict(os.environ)
        env.pop("CLAUDE_USAGE_STATE", None)
        env["XDG_STATE_HOME"] = "/tmp/xdg"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(state_dir(), Path("/tmp/xdg/claude-usage"))

    def test_falls_back_to_home_local_state(self) -> None:
        env = dict(os.environ)
        env.pop("CLAUDE_USAGE_STATE", None)
        env.pop("XDG_STATE_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            expected = Path.home() / ".local" / "state" / "claude-usage"
            self.assertEqual(state_dir(), expected)


def _write_profile_file(directory: Path, name: str, payload: dict) -> None:
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


_LIMITS = {"five_hour": {"percent": 42.0, "resets_epoch": 1788550200}}


class ReadAllStatesTests(unittest.TestCase):
    def test_schema_1_file_without_profile_block_reads_as_default(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_profile_file(directory, "latest.json", {"schema": 1, "updated_epoch": 10, "limits": _LIMITS})
            reading = state.read_all_states(directory)
            self.assertEqual(set(reading.profiles), {"default"})
            self.assertEqual(reading.profiles["default"].label, "default")

    def test_schema_2_file_carries_its_own_id_and_label(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_profile_file(
                directory,
                "personal.json",
                {
                    "schema": 2,
                    "profile": {"id": "personal", "label": "own"},
                    "updated_epoch": 20,
                    "limits": _LIMITS,
                },
            )
            reading = state.read_all_states(directory)
            self.assertEqual(reading.profiles["personal"].label, "own")

    def test_duplicate_ids_keep_the_fresher_file(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_profile_file(directory, "latest.json", {"schema": 1, "updated_epoch": 10, "limits": _LIMITS})
            _write_profile_file(
                directory,
                "default.json",
                {
                    "schema": 2,
                    "profile": {"id": "default", "label": "work"},
                    "updated_epoch": 99,
                    "limits": _LIMITS,
                },
            )
            reading = state.read_all_states(directory)
            self.assertEqual(set(reading.profiles), {"default"})
            self.assertEqual(reading.profiles["default"].snapshot.updated_epoch, 99)

    def test_broken_file_does_not_hide_the_others(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "broken.json").write_text("{not json", encoding="utf-8")
            _write_profile_file(
                directory,
                "personal.json",
                {
                    "schema": 2,
                    "profile": {"id": "personal", "label": "own"},
                    "updated_epoch": 20,
                    "limits": _LIMITS,
                },
            )
            reading = state.read_all_states(directory)
            self.assertEqual(set(reading.profiles), {"personal"})
            self.assertEqual(list(reading.unreadable), ["broken.json"])

    def test_invalid_utf8_bytes_are_unreadable_not_an_exception(self):
        """UnicodeDecodeError — подкласс ValueError, а не OSError: без явной ловли чтение падало."""
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "latest.json").write_bytes(b"\xff\xfe")
            reading = read_all_states(directory)
            self.assertEqual(reading.profiles, {})
            self.assertEqual(list(reading.unreadable), ["latest.json"])

    def test_unknown_schema_version_is_unreadable_not_fatal(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_profile_file(directory, "future.json", {"schema": 99, "updated_epoch": 1, "limits": _LIMITS})
            reading = state.read_all_states(directory)
            self.assertEqual(reading.profiles, {})
            self.assertEqual(list(reading.unreadable), ["future.json"])

    def test_missing_directory_yields_empty_reading(self):
        with TemporaryDirectory() as tmp:
            reading = state.read_all_states(Path(tmp) / "nope")
            self.assertEqual(reading.profiles, {})
            self.assertEqual(list(reading.unreadable), [])

    def test_state_dir_follows_claude_usage_state_parent(self):
        with unittest.mock.patch.dict(
            "os.environ", {"CLAUDE_USAGE_STATE": "/tmp/x/custom.json"}, clear=False
        ):
            self.assertEqual(state.state_dir(), Path("/tmp/x"))


if __name__ == "__main__":
    unittest.main()
