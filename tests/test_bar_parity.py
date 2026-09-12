"""Гвард паритета рендера бара: bash-хук (`bin/claude-statusline.sh`, jq)
и `bar.panel_label` (python) обязаны рисовать один и тот же текст панели
из одного и того же набора лимитов.

Оба рендера получают вход независимо друг от друга: bash — через stdin в
формате реального бандла Claude Code, python — читая state.py-снимок,
который этот же прогон bash-хука только что записал на диск. Общий
источник входа (rate_limits) и раздельные пути рендера — то, что делает
сравнение честным: если один из алгоритмов (округление, зажим ячеек,
порядок окон, обработка отсутствующего resets_at) изменится, тест падает.

Область не включает effective_percent/is_stale/is_alarm — это логика
демона поверх уже сохранённого снимка, у bash-хука аналога нет: он читает
rate_limits один раз в момент вызова и не умеет решать, истекло ли окно к
моменту повторного показа. Поэтому у каждой фикстуры resets_at либо в
далёком будущем, либо отсутствует вовсе — паритет проверяется там, где
обе стороны действительно совершают одну и ту же работу.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from claude_usage_indicator import bar, state

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / "bin" / "claude-statusline.sh"
_FUTURE_EPOCH = 4102444800  # 2100-01-01 — за пределами любого правдоподобного now_epoch фикстуры
_NOW_EPOCH = 1_700_000_000  # произвольный "текущий" момент, меньше _FUTURE_EPOCH

# (имя_фикстуры, rate_limits) — каждое имя описывает край, который оно покрывает.
_FIXTURES: list[tuple[str, dict]] = [
    ("zero_percent", {"five_hour": {"used_percentage": 0, "resets_at": _FUTURE_EPOCH}}),
    ("hundred_percent", {"seven_day": {"used_percentage": 100, "resets_at": _FUTURE_EPOCH}}),
    ("cell_boundary_round_down", {"five_hour": {"used_percentage": 6.2, "resets_at": _FUTURE_EPOCH}}),
    ("cell_boundary_round_up", {"seven_day": {"used_percentage": 6.3, "resets_at": _FUTURE_EPOCH}}),
    ("above_hundred_percent", {"five_hour": {"used_percentage": 150, "resets_at": _FUTURE_EPOCH}}),
    ("fractional_percent", {"seven_day": {"used_percentage": 33.7, "resets_at": _FUTURE_EPOCH}}),
    ("missing_reset_window", {"five_hour": {"used_percentage": 20}}),
    (
        "model_scoped_own_label",
        {"model_scoped": [{"display_name": "Fable", "utilization": 57.5, "resets_at": _FUTURE_EPOCH}]},
    ),
    (
        "multiple_windows_and_order",
        {
            "five_hour": {"used_percentage": 12.5, "resets_at": _FUTURE_EPOCH},
            "seven_day": {"used_percentage": 88.1, "resets_at": _FUTURE_EPOCH},
            "model_scoped": [
                {"display_name": "Zeta Model", "utilization": 5.0, "resets_at": _FUTURE_EPOCH},
                {"display_name": "Alpha Model", "utilization": 95.0, "resets_at": _FUTURE_EPOCH},
            ],
        },
    ),
    ("no_windows_at_all", {}),
]


def _run_bash_hook(rate_limits: dict, state_path: Path) -> subprocess.CompletedProcess[str]:
    """Прогоняет реальный bash-хук на входе с данным rate_limits, пишет состояние в state_path."""
    session_json = json.dumps({"session_id": "sess-parity", "rate_limits": rate_limits})
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_USAGE_PROFILE_LABEL", None)
    env["CLAUDE_USAGE_STATE"] = str(state_path)
    return subprocess.run(
        [str(_HOOK)], input=session_json, capture_output=True, text=True, env=env, timeout=10
    )


def _python_render(state_dir: Path) -> str:
    """Строит Snapshot из того же файла состояния, что записал bash, и рендерит его bar.py."""
    snapshot = state.read_all_states(state_dir).profiles["default"].snapshot
    return bar.panel_label(snapshot, _NOW_EPOCH)


class BarRenderParityTests(unittest.TestCase):
    """Один и тот же набор лимитов, две независимые реализации рендера, один и тот же текст."""

    def test_bash_and_python_render_identical_status_line(self) -> None:
        for name, rate_limits in _FIXTURES:
            with self.subTest(fixture=name):
                with tempfile.TemporaryDirectory() as tmp:
                    state_path = Path(tmp) / "state" / "claude-usage" / "latest.json"
                    result = _run_bash_hook(rate_limits, state_path)

                    self.assertEqual(0, result.returncode, f"{name}: exit code")
                    self.assertEqual("", result.stderr, f"{name}: stderr")

                    bash_line = result.stdout.rstrip("\n")
                    python_line = _python_render(state_path.parent)
                    self.assertEqual(bash_line, python_line, f"{name}: рендер разошёлся")


if __name__ == "__main__":
    unittest.main()
