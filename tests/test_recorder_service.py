from __future__ import annotations

import unittest

from trend_analyzer.models import ProfileConfig, RecorderSourceConfig, SignalConfig

try:
    from trend_analyzer.recorder_service import RecorderService
except Exception:  # pragma: no cover - optional dependency (pymodbus)
    RecorderService = None


@unittest.skipUnless(RecorderService is not None, "RecorderService dependencies are unavailable")
class RecorderServiceSignalFilterTests(unittest.TestCase):
    def test_local_signals_filter_keeps_local_and_modbus_sources(self) -> None:
        local_a = SignalConfig(id="local-a", name="Local A", source_id="local")
        modbus = SignalConfig(id="modbus-a", name="Modbus A", source_id="modbus-src-1")
        remote = SignalConfig(id="remote-a", name="Remote A", source_id="remote-src-1")
        local_default = SignalConfig(id="local-b", name="Local B")
        profile = ProfileConfig(
            recorder_sources=[
                RecorderSourceConfig(
                    id="modbus-src-1",
                    name="PLC 1",
                    source_kind="modbus_tcp",
                    host="192.168.0.10",
                    port=502,
                    unit_id=1,
                    enabled=True,
                ),
                RecorderSourceConfig(
                    id="remote-src-1",
                    name="Recorder 1",
                    source_kind="remote_recorder",
                    host="192.168.0.20",
                    port=18777,
                    enabled=True,
                ),
            ],
            signals=[local_a, modbus, remote, local_default],
        )

        local_only = RecorderService._local_signals(profile)
        local_ids = [str(signal.id) for signal in local_only]

        self.assertEqual(local_ids, ["local-a", "local-b", "modbus-a"])


if __name__ == "__main__":
    unittest.main()
