import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))
import xynmacro_core as core

SHEET = cv2.imread(str(PYTHON_DIR / "tpl_inventory_digits.png"), cv2.IMREAD_GRAYSCALE)


def _row_with_number(number, colour=(0, 200, 0)):
    """A list frame whose quantity column shows `number` drawn from the digit sheet."""
    frame = np.zeros((60, 600, 3), dtype=np.uint8)
    x = 500
    for char in str(number):
        digit = SHEET[:, int(char) * 12:(int(char) + 1) * 12] > 0
        frame[3:3 + digit.shape[0], x:x + 12][digit] = colour
        x += 12
    return frame


class InventoryCountTests(unittest.TestCase):
    def test_live_counts_read_exactly(self):
        # Lossless live Items list (row tops relative to y=395). This client renders the
        # digits a pixel taller than the sheet, which the old matcher misread
        # as 9 for 97 and 0 for 20 and 50.
        frame = cv2.imread(str(PYTHON_DIR / "tests" / "fixtures" / "inventory_items_live.png"))
        for centre_y, expected in ((424, 20), (472, 50), (520, 97), (568, 50)):
            with self.subTest(expected=expected):
                self.assertEqual(core._read_inventory_count(frame, centre_y - 18, SHEET), expected)

    def test_sheet_digits_still_read_on_the_original_rendering(self):
        for number in (0, 1, 7, 12, 20, 48, 99, 105, 360):
            with self.subTest(number=number):
                frame = _row_with_number(number)
                self.assertEqual(core._read_inventory_count(frame, 395, SHEET), number)

    def test_selected_red_quantity_reads_too(self):
        frame = _row_with_number(36, colour=(0, 0, 200))
        self.assertEqual(core._read_inventory_count(frame, 395, SHEET), 36)

    def test_blank_or_blob_is_unknown_not_zero(self):
        blank = np.zeros((60, 600, 3), dtype=np.uint8)
        self.assertIsNone(core._read_inventory_count(blank, 395, SHEET))
        blob = blank.copy()
        blob[5:25, 500:540] = (0, 200, 0)
        self.assertIsNone(core._read_inventory_count(blob, 395, SHEET))


if __name__ == "__main__":
    unittest.main()
