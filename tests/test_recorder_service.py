from __future__ import annotations

import unittest

from trend_analyzer.models import ProfileConfig, SignalConfig

try:
    from trend_analyzer.recorder_service import RecorderService
except Exception:  # pragma: no cover - optional dependency (pymodbus)
    RecorderService = None


@unittest.skipUnless(RecorderService is not None, "RecorderService dependencies are unavailable")
class RecorderServiceSignalFilterTests(unittest.TestCase):
    def test_local_signals_filter_skips_remote_sources(self) -> None:
        local_a = SignalConfig(id="local-a", name="Local A", source_id="local")
        remote = SignalConfig(id="remote-a", name="Remote A", source_id="remote-src-1")
        local_default = SignalConfig(id="local-b", name="Local B")
        profile = ProfileConfig(signals=[local_a, remote, local_default])

        local_only = RecorderService._local_signals(profile)
        local_ids = [str(signal.id) for signal in local_only]

        self.assertEqual(local_ids, ["local-a", "local-b"])


if __name__ == "__main__":
    unittest.main()
