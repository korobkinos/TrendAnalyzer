from __future__ import annotations

import unittest
from unittest import mock

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

    def test_api_modbus_write_uses_shared_local_client(self) -> None:
        service = RecorderService()
        profile = ProfileConfig(
            ip="192.168.4.218",
            port=502,
            signals=[SignalConfig(id="local-a", name="Local A", source_id="local")],
        )
        service._set_runtime_profile(profile)
        fake_client = mock.Mock()
        fake_client.connected = True
        service._poll_clients["local"] = fake_client

        with mock.patch("trend_analyzer.recorder_service.api_modbus_write", return_value=(True, {"address": 36})) as api_write:
            ok, result = service.api_modbus_write(
                {
                    "address": 36,
                    "register_type": "holding",
                    "data_type": "bool",
                    "bit_index": 1,
                    "value": 1.0,
                }
            )

        self.assertTrue(ok)
        self.assertEqual(result, {"address": 36})
        api_write.assert_called_once()
        self.assertIs(api_write.call_args.kwargs.get("client"), fake_client)

    def test_api_modbus_read_many_uses_shared_modbus_source_client(self) -> None:
        service = RecorderService()
        profile = ProfileConfig(
            recorder_sources=[
                RecorderSourceConfig(
                    id="src-modbus",
                    name="PLC A",
                    source_kind="modbus_tcp",
                    host="10.1.1.5",
                    port=502,
                    unit_id=3,
                    enabled=True,
                )
            ],
            signals=[SignalConfig(id="sig-a", name="Sig A", source_id="src-modbus")],
        )
        service._set_runtime_profile(profile)
        fake_client = mock.Mock()
        fake_client.connected = True
        service._poll_clients["src-modbus"] = fake_client

        with mock.patch(
            "trend_analyzer.recorder_service.api_modbus_read_many",
            return_value=(True, {"values": [{"id": "row:0", "value": 1.0}], "errors": []}),
        ) as api_read_many:
            ok, result = service.api_modbus_read_many(
                {
                    "host": "10.1.1.5",
                    "port": 502,
                    "items": [{"id": "row:0", "address": 36, "register_type": "holding", "data_type": "bool"}],
                }
            )

        self.assertTrue(ok)
        self.assertEqual(result["errors"], [])
        api_read_many.assert_called_once()
        self.assertIs(api_read_many.call_args.kwargs.get("client"), fake_client)


if __name__ == "__main__":
    unittest.main()
