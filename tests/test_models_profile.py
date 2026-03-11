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
        self.assertEqual(restored.recorder_sources[0].host, "192.168.1.10")
        self.assertEqual(restored.signals[0].source_id, "src-a")
        self.assertEqual(restored.signals[0].remote_tag_id, "tag-001")
        self.assertTrue(restored.recorder_api_enabled)
        self.assertEqual(restored.recorder_api_port, 18777)
        self.assertEqual(restored.recorder_api_token, "api-token")


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


if __name__ == "__main__":
    unittest.main()
