import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.face_tracking import interpolate_center, smooth_centers


class SmoothCentersTests(unittest.TestCase):
    def test_empty_input_returns_empty_output(self):
        self.assertEqual(smooth_centers([]), [])

    def test_single_sample_returns_itself(self):
        result = smooth_centers([(0.0, 0.5, 0.5)])
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0][1], 0.5)
        self.assertAlmostEqual(result[0][2], 0.5)

    def test_sorts_out_of_order_samples_by_time(self):
        result = smooth_centers([(1.0, 0.8, 0.8), (0.0, 0.2, 0.2)])
        self.assertEqual([t for t, _, _ in result], [0.0, 1.0])

    def test_smoothing_pulls_new_sample_toward_previous_average(self):
        samples = [(0.0, 0.2, 0.2), (1.0, 0.9, 0.9)]
        result = smooth_centers(samples, smoothing=0.9)

        # Heavy smoothing means the second point should land much closer to
        # the first point's position than to the raw new sample (0.9, 0.9).
        _, cx1, cy1 = result[1]
        self.assertLess(cx1, 0.5)
        self.assertLess(cy1, 0.5)

    def test_low_smoothing_tracks_raw_samples_closely(self):
        samples = [(0.0, 0.2, 0.2), (1.0, 0.9, 0.9)]
        result = smooth_centers(samples, smoothing=0.0)

        _, cx1, cy1 = result[1]
        self.assertAlmostEqual(cx1, 0.9)
        self.assertAlmostEqual(cy1, 0.9)

    def test_gap_between_detections_still_produces_one_entry_per_sample(self):
        samples = [(0.0, 0.5, 0.5), (5.0, 0.6, 0.4)]
        result = smooth_centers(samples)
        self.assertEqual(len(result), 2)


class InterpolateCenterTests(unittest.TestCase):
    def test_empty_timeline_returns_default(self):
        self.assertEqual(interpolate_center([], 3.0), (0.5, 0.5))

    def test_empty_timeline_returns_custom_default(self):
        self.assertEqual(interpolate_center([], 3.0, default=(0.1, 0.9)), (0.1, 0.9))

    def test_time_before_range_holds_first_sample(self):
        timeline = [(1.0, 0.3, 0.4), (2.0, 0.7, 0.6)]
        self.assertEqual(interpolate_center(timeline, 0.0), (0.3, 0.4))

    def test_time_after_range_holds_last_sample(self):
        timeline = [(1.0, 0.3, 0.4), (2.0, 0.7, 0.6)]
        self.assertEqual(interpolate_center(timeline, 10.0), (0.7, 0.6))

    def test_time_exactly_on_sample_returns_that_sample(self):
        timeline = [(1.0, 0.3, 0.4), (2.0, 0.7, 0.6)]
        self.assertEqual(interpolate_center(timeline, 1.0), (0.3, 0.4))
        self.assertEqual(interpolate_center(timeline, 2.0), (0.7, 0.6))

    def test_time_between_samples_interpolates_linearly(self):
        timeline = [(0.0, 0.0, 0.0), (2.0, 1.0, 1.0)]
        cx, cy = interpolate_center(timeline, 1.0)
        self.assertAlmostEqual(cx, 0.5)
        self.assertAlmostEqual(cy, 0.5)

    def test_time_between_samples_interpolates_at_quarter_point(self):
        timeline = [(0.0, 0.0, 0.0), (4.0, 1.0, 1.0)]
        cx, cy = interpolate_center(timeline, 1.0)
        self.assertAlmostEqual(cx, 0.25)
        self.assertAlmostEqual(cy, 0.25)


if __name__ == "__main__":
    unittest.main()
