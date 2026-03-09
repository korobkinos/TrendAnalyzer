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


if __name__ == "__main__":
    unittest.main()

