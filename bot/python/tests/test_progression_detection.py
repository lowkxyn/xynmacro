import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xmacro_core as core


WHITE = (230, 230, 230, 255)


def progression_frame(right_matches=True, include_word=True):
    frame = np.zeros((500, 700, 4), dtype=np.uint8)

    if include_word:
        for index in range(8):
            x = 35 + index * 24
            cv2.rectangle(frame, (x, 70), (x + 4, 93), WHITE, -1)

    cv2.rectangle(frame, (260, 60), (267, 94), WHITE, -1)
    cv2.rectangle(frame, (350, 60), (357, 94), WHITE, -1)
    cv2.rectangle(frame, (310, 68), (314, 91), WHITE, -1)

    # Left numeral: an L-shaped connected glyph with a 10x24 bounding box.
    cv2.rectangle(frame, (280, 68), (283, 91), WHITE, -1)
    cv2.rectangle(frame, (280, 88), (289, 91), WHITE, -1)

    if right_matches:
        cv2.rectangle(frame, (330, 68), (333, 91), WHITE, -1)
        cv2.rectangle(frame, (330, 88), (339, 91), WHITE, -1)
    else:
        # Mirrored L keeps the same dimensions but has a clearly different mask.
        cv2.rectangle(frame, (336, 68), (339, 91), WHITE, -1)
        cv2.rectangle(frame, (330, 88), (339, 91), WHITE, -1)

    return frame


class ProgressionDetectionTests(unittest.TestCase):
    def test_matching_progression_numbers_are_complete(self):
        self.assertIs(
            core._progression_completion_from_frame(progression_frame()),
            True,
        )

    def test_different_progression_numbers_are_incomplete(self):
        self.assertIs(
            core._progression_completion_from_frame(
                progression_frame(right_matches=False)
            ),
            False,
        )

    def test_unrelated_matching_parenthesized_numbers_are_ignored(self):
        self.assertIsNone(
            core._progression_completion_from_frame(
                progression_frame(include_word=False)
            )
        )

    def test_blank_frame_is_unknown(self):
        blank = np.zeros((500, 700, 4), dtype=np.uint8)
        self.assertIsNone(core._progression_completion_from_frame(blank))

    def test_tracking_messages_match_enabled_fallback_mode(self):
        with patch.object(core, "NO_YELLOW_FALLBACK_ENABLED", True):
            self.assertIn(
                "fallback is no longer needed",
                core._progression_tracking_message("Health"),
            )
            self.assertIn("fallback timeout available", core._first_yellow_message())

    def test_tracking_messages_match_disabled_fallback_mode(self):
        with patch.object(core, "NO_YELLOW_FALLBACK_ENABLED", False):
            self.assertIn("fallback remains disabled", core._progression_tracking_message("Health"))
            self.assertIn("fallback timeout is disabled", core._first_yellow_message())

    def test_whole_and_decimal_countdowns_keep_their_natural_format(self):
        self.assertEqual(core._format_seconds(5), "5s")
        self.assertEqual(core._format_seconds(2.5), "2.5s")

    def test_manual_skips_cannot_report_a_completed_training_order(self):
        self.assertEqual(
            core._training_order_result([]),
            ("completed", "Training order completed"),
        )
        self.assertEqual(
            core._training_order_result(["Ki Control", "Ki Damage"]),
            (
                "incomplete",
                "Training order ended with skipped stats: Ki Control, Ki Damage",
            ),
        )


if __name__ == "__main__":
    unittest.main()
