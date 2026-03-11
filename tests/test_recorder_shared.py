from __future__ import annotations

import unittest
from unittest import mock

from trend_analyzer import recorder_shared


class RecorderSharedTests(unittest.TestCase):
    def test_resolve_recorder_pid_recovers_from_status(self) -> None:
        with mock.patch.object(recorder_shared, "read_recorder_pid", return_value=None), mock.patch.object(
            recorder_shared,
            "read_recorder_status",
            return_value={"pid": 43210, "state": "running"},
        ), mock.patch.object(
            recorder_shared,
            "is_recorder_pid_running",
            side_effect=lambda pid: int(pid or 0) == 43210,
        ), mock.patch.object(
            recorder_shared, "write_recorder_pid"
        ) as write_pid:
            pid = recorder_shared.resolve_recorder_pid(heal_pid_file=True)
            self.assertEqual(pid, 43210)
            write_pid.assert_called_once_with(43210)

    def test_resolve_recorder_pid_uses_pid_file_when_valid(self) -> None:
        with mock.patch.object(recorder_shared, "read_recorder_pid", return_value=12345), mock.patch.object(
            recorder_shared, "is_recorder_pid_running", return_value=True
        ), mock.patch.object(recorder_shared, "read_recorder_status") as read_status:
            pid = recorder_shared.resolve_recorder_pid(heal_pid_file=True)
            self.assertEqual(pid, 12345)
            read_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
