from __future__ import annotations

import unittest

from trend_analyzer.history_restore import compute_live_history_span_s


class LiveHistorySpanTests(unittest.TestCase):
    def test_prefers_existing_visible_span_when_it_is_larger(self) -> None:
        span = compute_live_history_span_s(
            poll_interval_ms=500,
            archive_interval_ms=1000,
            archive_on_change_only=False,
            archive_keepalive_s=60,
            span_hint_s=1800.0,
            current_span_s=300.0,
        )
        self.assertEqual(span, 1800.0)

    def test_expands_window_for_sparse_archive_on_change_mode(self) -> None:
        span = compute_live_history_span_s(
            poll_interval_ms=500,
            archive_interval_ms=1000,
            archive_on_change_only=True,
            archive_keepalive_s=300,
            span_hint_s=10.0,
            current_span_s=1.0,
        )
        self.assertEqual(span, 900.0)

    def test_uses_safe_fallback_when_keepalive_is_disabled(self) -> None:
        span = compute_live_history_span_s(
            poll_interval_ms=500,
            archive_interval_ms=1000,
            archive_on_change_only=True,
            archive_keepalive_s=0,
            span_hint_s=None,
            current_span_s=None,
        )
        self.assertEqual(span, 600.0)


if __name__ == "__main__":
    unittest.main()
