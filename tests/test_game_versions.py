import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import game_versions
from game_versions import DEFAULT_VERSION, SUPPORTED_VERSIONS, get_profile


class GameVersionTests(unittest.TestCase):
    def test_default_and_supported_set(self):
        self.assertEqual(DEFAULT_VERSION, "26.1.2")
        self.assertEqual(SUPPORTED_VERSIONS, ("26.1.2", "1.21.1"))

    def test_default_profile_is_java_25_official(self):
        profile = get_profile()
        self.assertEqual(profile.version, "26.1.2")
        self.assertEqual(profile.java_version, 25)
        self.assertEqual(profile.runtime_namespace, "official")
        self.assertFalse(profile.requires_remapping)

    def test_1_21_1_is_java_21_and_needs_remapping(self):
        profile = get_profile("1.21.1")
        self.assertEqual(profile.version, "1.21.1")
        self.assertEqual(profile.java_version, 21)
        self.assertEqual(profile.runtime_namespace, "official")
        self.assertTrue(profile.requires_remapping)

    def test_every_supported_version_resolves_to_itself(self):
        for version in SUPPORTED_VERSIONS:
            self.assertEqual(get_profile(version).version, version)

    def test_unsupported_versions_raise_value_error(self):
        for version in ("1.20.1", "26.1", "26.1.20", "1.21", "", " 1.21.1", "latest"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    get_profile(version)

    def test_non_text_version_raises_value_error(self):
        for version in (None, 1211, 26.12, ["26.1.2"], {"version": "26.1.2"}):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    get_profile(version)

    def test_profiles_are_immutable_and_stable(self):
        profile = get_profile("1.21.1")
        with self.assertRaises(FrozenInstanceError):
            profile.java_version = 17
        self.assertIs(get_profile("1.21.1"), profile)
        self.assertIsNot(get_profile("1.21.1"), get_profile("26.1.2"))

    def test_no_unsupported_version_leaks_into_the_table(self):
        for version in game_versions._PROFILES:
            self.assertIn(version, SUPPORTED_VERSIONS)


if __name__ == "__main__":
    unittest.main()

