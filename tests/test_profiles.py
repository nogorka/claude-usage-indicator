import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from claude_usage_indicator import profiles


class ProfileIdTests(unittest.TestCase):
    def test_unset_config_dir_is_default_profile(self):
        self.assertEqual(profiles.profile_id_from_config_dir(None), "default")

    def test_empty_string_is_default_profile(self):
        self.assertEqual(profiles.profile_id_from_config_dir(""), "default")

    def test_home_claude_dir_is_default_profile(self):
        self.assertEqual(profiles.profile_id_from_config_dir(Path.home() / ".claude"), "default")

    def test_claude_prefix_is_stripped(self):
        self.assertEqual(
            profiles.profile_id_from_config_dir(Path.home() / ".claude-personal"), "personal"
        )

    def test_trailing_slash_does_not_change_id(self):
        self.assertEqual(
            profiles.profile_id_from_config_dir(f"{Path.home()}/.claude-personal/"), "personal"
        )

    def test_unsafe_characters_are_replaced(self):
        self.assertEqual(profiles.profile_id_from_config_dir("/tmp/.claude-Work Acct!"), "work-acct")

    def test_name_that_sanitizes_to_nothing_falls_back_to_default(self):
        self.assertEqual(profiles.profile_id_from_config_dir("/tmp/.claude-!!!"), "default")

    def test_sort_key_puts_default_first(self):
        self.assertEqual(
            sorted(["personal", "default", "alpha"], key=profiles.profile_sort_key),
            ["default", "alpha", "personal"],
        )


class DiscoverProfilesTests(unittest.TestCase):
    def test_directory_without_credentials_is_not_a_profile(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude-empty").mkdir()
            self.assertEqual(profiles.discover_profiles(home), [])

    def test_directory_with_credentials_is_a_profile(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            for name in (".claude", ".claude-personal"):
                (home / name).mkdir()
                (home / name / ".credentials.json").write_text("{}", encoding="utf-8")
            found = profiles.discover_profiles(home)
            self.assertEqual([p.id for p in found], ["default", "personal"])
            self.assertEqual(found[1].config_dir, home / ".claude-personal")

    def test_credentials_file_is_never_opened(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            secret = home / ".claude" / ".credentials.json"
            secret.write_text("{}", encoding="utf-8")
            os.chmod(secret, 0o000)
            try:
                self.assertEqual([p.id for p in profiles.discover_profiles(home)], ["default"])
            finally:
                os.chmod(secret, 0o600)
