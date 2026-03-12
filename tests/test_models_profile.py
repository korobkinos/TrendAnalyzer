from __future__ import annotations

import unittest

from trend_analyzer.models import ProfileConfig, RecorderSourceConfig, SignalConfig


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
            render_chart_enabled=False,
            archive_on_change_only=True,
            archive_deadband=0.25,
            archive_keepalive_s=90,
        )
        payload = profile.to_dict()
        restored = ProfileConfig.from_dict(payload)
        self.assertFalse(restored.render_chart_enabled)
        self.assertTrue(restored.archive_on_change_only)
        self.assertAlmostEqual(restored.archive_deadband, 0.25, places=6)
        self.assertEqual(restored.archive_keepalive_s, 90)

    def test_explicit_empty_signals_are_preserved(self) -> None:
        restored = ProfileConfig.from_dict({"signals": []})
        self.assertEqual(restored.signals, [])

    def test_remote_sources_and_signal_binding_serialization(self) -> None:
        profile = ProfileConfig(
            recorder_sources=[
                RecorderSourceConfig(
                    id="src-a",
                    name="Recorder A",
                    source_kind="remote_recorder",
                    host="192.168.1.10",
                    port=18777,
                    token="tok",
                    enabled=True,
                    recorder_id="rec-a",
                )
            ],
            signals=[
                SignalConfig(
                    id="sig-a",
                    name="Remote Tag",
                    source_id="src-a",
                    remote_tag_id="tag-001",
                )
            ],
            recorder_api_enabled=True,
            recorder_api_host="0.0.0.0",
            recorder_api_port=18777,
            recorder_api_token="api-token",
        )
        restored = ProfileConfig.from_dict(profile.to_dict())
        self.assertEqual(len(restored.recorder_sources), 1)
        self.assertEqual(restored.recorder_sources[0].id, "src-a")
        self.assertEqual(restored.recorder_sources[0].source_kind, "remote_recorder")
        self.assertEqual(restored.recorder_sources[0].host, "192.168.1.10")
        self.assertEqual(restored.signals[0].source_id, "src-a")
        self.assertEqual(restored.signals[0].remote_tag_id, "tag-001")
        self.assertTrue(restored.recorder_api_enabled)
        self.assertEqual(restored.recorder_api_port, 18777)
        self.assertEqual(restored.recorder_api_token, "api-token")

    def test_modbus_source_fields_roundtrip(self) -> None:
        profile = ProfileConfig(
            recorder_sources=[
                RecorderSourceConfig(
                    id="src-modbus",
                    name="PLC A",
                    source_kind="modbus_tcp",
                    host="10.1.1.5",
                    port=502,
                    unit_id=3,
                    timeout_s=0.7,
                    retries=2,
                    address_offset=1,
                    enabled=True,
                )
            ]
        )
        restored = ProfileConfig.from_dict(profile.to_dict())
        self.assertEqual(len(restored.recorder_sources), 1)
        src = restored.recorder_sources[0]
        self.assertEqual(src.source_kind, "modbus_tcp")
        self.assertEqual(src.host, "10.1.1.5")
        self.assertEqual(src.port, 502)
        self.assertEqual(src.unit_id, 3)
        self.assertAlmostEqual(src.timeout_s, 0.7, places=6)
        self.assertEqual(src.retries, 2)
        self.assertEqual(src.address_offset, 1)

    def test_legacy_source_defaults_to_remote_kind(self) -> None:
        restored = RecorderSourceConfig.from_dict(
            {
                "id": "legacy-src",
                "name": "Legacy",
                "host": "192.168.10.10",
                "port": 18777,
            }
        )
        self.assertEqual(restored.source_kind, "remote_recorder")
        self.assertEqual(restored.port, 18777)


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

    def test_plot_smoothing_fields_roundtrip_and_normalization(self) -> None:
        profile = ProfileConfig(
            plot_smoothing_enabled=True,
            plot_smoothing_window=12,
        )
        restored = ProfileConfig.from_dict(profile.to_dict())
        self.assertTrue(restored.plot_smoothing_enabled)
        self.assertEqual(restored.plot_smoothing_window, 13)

        restored_low = ProfileConfig.from_dict(
            {
                "plot_smoothing_enabled": True,
                "plot_smoothing_window": 1,
            }
        )
        self.assertTrue(restored_low.plot_smoothing_enabled)
        self.assertEqual(restored_low.plot_smoothing_window, 3)

    def test_archive_retention_size_and_theme_fields_roundtrip(self) -> None:
        profile = ProfileConfig(
            archive_retention_mode="size",
            archive_max_size_value=10,
            archive_max_size_unit="GB",
            ui_theme_preset="light",
        )
        restored = ProfileConfig.from_dict(profile.to_dict())
        self.assertEqual(restored.archive_retention_mode, "size")
        self.assertEqual(restored.archive_max_size_value, 10)
        self.assertEqual(restored.archive_max_size_unit, "GB")
        self.assertEqual(restored.ui_theme_preset, "light")

        restored_invalid = ProfileConfig.from_dict(
            {
                "archive_retention_mode": "bad",
                "archive_max_size_value": 0,
                "archive_max_size_unit": "tb",
                "ui_theme_preset": "",
            }
        )
        self.assertEqual(restored_invalid.archive_retention_mode, "days")
        self.assertEqual(restored_invalid.archive_max_size_value, 1)
        self.assertEqual(restored_invalid.archive_max_size_unit, "MB")
        self.assertEqual(restored_invalid.ui_theme_preset, "dark")


if __name__ == "__main__":
    unittest.main()
