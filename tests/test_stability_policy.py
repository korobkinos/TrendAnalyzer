from __future__ import annotations

import unittest

from trend_analyzer.stability_policy import (
    should_force_auto_x_on_start,
    should_preload_history_on_profile_load,
)


class StabilityPolicyTests(unittest.TestCase):
    def test_online_profile_load_does_not_preload_history(self) -> None:
        self.assertFalse(
            should_preload_history_on_profile_load(
                work_mode="online",
                render_chart_enabled=True,
                live_running=False,
            )
        )

    def test_offline_profile_load_preloads_history(self) -> None:
        self.assertTrue(
            should_preload_history_on_profile_load(
                work_mode="offline",
                render_chart_enabled=True,
                live_running=False,
            )
        )

    def test_no_preload_when_render_disabled(self) -> None:
        self.assertFalse(
            should_preload_history_on_profile_load(
                work_mode="offline",
                render_chart_enabled=False,
                live_running=False,
            )
        )

    def test_auto_x_is_not_forced_on_start(self) -> None:
        self.assertFalse(should_force_auto_x_on_start(True))
        self.assertFalse(should_force_auto_x_on_start(False))


if __name__ == "__main__":
    unittest.main()

