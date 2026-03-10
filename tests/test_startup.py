from __future__ import annotations

import unittest

from trend_analyzer.startup import startup_command


class StartupCommandTests(unittest.TestCase):
    def test_startup_command_default_points_to_main(self) -> None:
        command = startup_command()
        self.assertIn("main.py", command)

    def test_startup_command_accepts_extra_args(self) -> None:
        command = startup_command(extra_args=["--recorder-tray"])
        self.assertIn("--recorder-tray", command)


if __name__ == "__main__":
    unittest.main()
