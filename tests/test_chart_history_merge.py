import unittest

from trend_analyzer.chart import MultiAxisChart
from trend_analyzer.ui import MainWindow


class ChartHistoryMergeTests(unittest.TestCase):
    def test_merge_series_keeps_history_and_live_sorted(self) -> None:
        history = ([10.0, 20.0, 30.0], [1.0, 2.0, 3.0])
        live = ([40.0, 50.0], [4.0, 5.0])

        xs, ys = MultiAxisChart._merge_series(history, live)

        self.assertEqual(xs, [10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(ys, [1.0, 2.0, 3.0, 4.0, 5.0])

    def test_merge_series_prefers_live_value_on_same_timestamp(self) -> None:
        history = ([10.0, 20.0, 30.0], [1.0, 2.0, 3.0])
        live = ([30.0, 40.0], [33.0, 4.0])

        xs, ys = MultiAxisChart._merge_series(history, live)

        self.assertEqual(xs, [10.0, 20.0, 30.0, 40.0])
        self.assertEqual(ys, [1.0, 2.0, 33.0, 4.0])


class HistoryPayloadTests(unittest.TestCase):
    def test_samples_payload_has_points_detects_non_empty_rows(self) -> None:
        self.assertTrue(MainWindow._samples_payload_has_points({"a": [[1.0, 2.0]], "b": []}))

    def test_samples_payload_has_points_rejects_empty_payload(self) -> None:
        self.assertFalse(MainWindow._samples_payload_has_points({"a": [], "b": []}))
        self.assertFalse(MainWindow._samples_payload_has_points({}))


if __name__ == "__main__":
    unittest.main()
