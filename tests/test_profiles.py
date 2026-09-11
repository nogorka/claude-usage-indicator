import hashlib
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

    def test_dir_named_work_is_id_work(self):
        self.assertEqual(profiles.profile_id_from_config_dir(Path.home() / ".claude-work"), "work")

    def test_dir_named_work_uppercase_collides_with_lowercase(self):
        # Архитектор принял эту коллизию сознательно: приведение регистра потерей
        # информации не считается, поэтому не детектируется и не хэшируется.
        self.assertEqual(profiles.profile_id_from_config_dir(Path.home() / ".claude-Work"), "work")

    def test_unsafe_characters_produce_slug_plus_hash(self):
        result = profiles.profile_id_from_config_dir("/tmp/.claude-Work Acct!")
        self.assertRegex(result, r"^work-acct-[0-9a-f]{6}$")

    def test_name_that_sanitizes_to_nothing_gets_profile_prefix_and_hash(self):
        result = profiles.profile_id_from_config_dir("/tmp/.claude-!!!")
        self.assertRegex(result, r"^profile-[0-9a-f]{6}$")

    def test_hash_suffix_matches_sha256_of_canonical_path(self):
        config_dir = "/tmp/.claude-!!!"
        result = profiles.profile_id_from_config_dir(config_dir)
        canonical = str(Path(config_dir).resolve())
        expected_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:6]
        self.assertEqual(result, f"profile-{expected_digest}")

    def test_cyrillic_lossy_names_are_distinguishable_from_each_other_and_default(self):
        personal = profiles.profile_id_from_config_dir("/tmp/.claude-личный")
        work = profiles.profile_id_from_config_dir("/tmp/.claude-работа")
        self.assertNotEqual(personal, work)
        self.assertNotEqual(personal, "default")
        self.assertNotEqual(work, "default")
        self.assertRegex(personal, r"^profile-[0-9a-f]{6}$")
        self.assertRegex(work, r"^profile-[0-9a-f]{6}$")

    def test_diacritic_name_keeps_ascii_slug_prefix_plus_hash(self):
        result = profiles.profile_id_from_config_dir("/tmp/.claude-Café")
        self.assertRegex(result, r"^caf-[0-9a-f]{6}$")

    def test_slug_colliding_with_reserved_default_gets_hash_suffix(self):
        result = profiles.profile_id_from_config_dir("/tmp/.claude-default")
        self.assertRegex(result, r"^default-[0-9a-f]{6}$")

    def test_sort_key_puts_default_first(self):
        self.assertEqual(
            sorted(["personal", "default", "alpha"], key=profiles.profile_sort_key),
            ["default", "alpha", "personal"],
        )

    def test_arbitrary_claude_directory_is_not_default(self):
        self.assertEqual(profiles.profile_id_from_config_dir("/tmp/.claude"), "claude")


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
