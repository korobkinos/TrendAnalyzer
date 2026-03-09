from __future__ import annotations

import unittest

from trend_analyzer.models import ProfileConfig


class ProfileModelTests(unittest.TestCase):
    def test_render_interval_serialization(self) -> None:
        profile = ProfileConfig(render_interval_ms=150)
        payload = profile.to_dict()
        restored = ProfileConfig.from_dict(payload)
        self.assertEqual(restored.render_interval_ms, 150)

    def test_render_interval_default_and_bounds(self) -> None:
        restored = ProfileConfig.from_dict({})
        self.assertGreaterEqual(restored.render_interval_ms, 50)
        self.assertEqual(restored.render_interval_ms, 200)

        restored_low = ProfileConfig.from_dict({"render_interval_ms": 1})
        self.assertEqual(restored_low.render_interval_ms, 50)

    def test_archive_change_only_fields_serialization(self) -> None:
        profile = ProfileConfig(
            archive_on_change_only=True,
            archive_deadband=0.25,
            archive_keepalive_s=90,
        )
        payload = profile.to_dict()
        restored = ProfileConfig.from_dict(payload)
        self.assertTrue(restored.archive_on_change_only)
        self.assertAlmostEqual(restored.archive_deadband, 0.25, places=6)
        self.assertEqual(restored.archive_keepalive_s, 90)

    def test_archive_change_only_fields_bounds(self) -> None:
        restored = ProfileConfig.from_dict(
            {
                "archive_on_change_only": True,
                "archive_deadband": -1.0,
                "archive_keepalive_s": -10,
            }
        )
        self.assertTrue(restored.archive_on_change_only)
        self.assertEqual(restored.archive_deadband, 0.0)
        self.assertEqual(restored.archive_keepalive_s, 0)


if __name__ == "__main__":
    unittest.main()
