from __future__ import annotations

import unittest

from trend_analyzer.instance_lock import SingleInstanceLock


class SingleInstanceLockTests(unittest.TestCase):
    def test_second_lock_is_blocked_until_release(self) -> None:
        lock_a = SingleInstanceLock("test_lock_single_instance")
        lock_b = SingleInstanceLock("test_lock_single_instance")

        self.assertTrue(lock_a.acquire())
        self.assertFalse(lock_b.acquire())

        lock_a.release()
        self.assertTrue(lock_b.acquire())
        lock_b.release()


if __name__ == "__main__":
    unittest.main()
