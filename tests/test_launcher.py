import shlex
import unittest
from pathlib import Path

from claude_usage_indicator import launcher, profiles


class LaunchCommandTests(unittest.TestCase):
    def _profile(self, profile_id, label, config_dir):
        return profiles.Profile(id=profile_id, label=label, config_dir=Path(config_dir))

    def _assignments(self, command):
        """Присваивания из `env VAR=... claude`, разобранные как их прочтёт shell."""
        words = shlex.split(command[-1])
        self.assertEqual(words[0], "env")
        self.assertEqual(words[-1], "claude")
        return dict(word.split("=", 1) for word in words[1:-1])

    def test_default_profile_does_not_set_config_dir(self):
        command = launcher.launch_command(
            self._profile("default", "work", "/home/u/.claude"), home=Path("/home/u")
        )
        self.assertNotIn("CLAUDE_CONFIG_DIR", self._assignments(command))

    def test_named_profile_sets_config_dir_and_label(self):
        command = launcher.launch_command(
            self._profile("personal", "own", "/home/u/.claude-personal"), home=Path("/home/u")
        )
        assignments = self._assignments(command)
        self.assertEqual(assignments["CLAUDE_CONFIG_DIR"], "/home/u/.claude-personal")
        self.assertEqual(assignments["CLAUDE_USAGE_PROFILE_LABEL"], "own")

    def test_label_with_a_space_survives_the_shell(self):
        command = launcher.launch_command(
            self._profile("personal", "мой личный", "/home/u/.claude-personal"), home=Path("/home/u")
        )
        # Разбор в одно слово и есть доказательство, что кавычки на месте:
        # без них shlex.split вернул бы «мой» и «личный» отдельными словами.
        self.assertEqual(
            self._assignments(command)["CLAUDE_USAGE_PROFILE_LABEL"], "мой личный"
        )

    def test_config_dir_wins_over_lossy_id_when_sanitization_collapses_to_default(self):
        """profile_id_from_config_dir может выродить непустой каталог в id "default"
        (например ~/.claude-личный) — проверять надо реальный каталог, а не производный id,
        иначе сессия уйдёт без CLAUDE_CONFIG_DIR прямо в настоящий default-аккаунт."""
        command = launcher.launch_command(
            self._profile("default", "личный", "/home/u/.claude-личный"), home=Path("/home/u")
        )
        assignments = self._assignments(command)
        self.assertEqual(assignments["CLAUDE_CONFIG_DIR"], "/home/u/.claude-личный")

    def test_terminal_is_gnome_terminal(self):
        command = launcher.launch_command(
            self._profile("default", "work", "/home/u/.claude"), home=Path("/home/u")
        )
        self.assertEqual(command[0], "gnome-terminal")


class LaunchTests(unittest.TestCase):
    def test_successful_spawn_reports_no_error(self):
        calls = []
        result = launcher.launch(
            profiles.Profile(id="default", label="work", config_dir=Path("/home/u/.claude")),
            spawn=lambda *a, **k: calls.append((a, k)),
            home=Path("/home/u"),
        )
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)

    def test_failed_spawn_returns_text_for_the_human(self):
        def boom(*_args, **_kwargs):
            raise OSError("gnome-terminal: not found")

        result = launcher.launch(
            profiles.Profile(id="default", label="work", config_dir=Path("/home/u/.claude")),
            spawn=boom,
            home=Path("/home/u"),
        )
        self.assertIn("gnome-terminal", result)
