"""Reproduce screenshot-only assets; run from the worktree, never capture a screen.

Input is the eight supplied 660x572 panel crops. No HUD/name pixels are copied.
Unobserved panel digits (4 and 8) deliberately have empty template cells.
Run make_progression_assets.py afterwards to restore the newer label assets.
"""
from pathlib import Path
import shutil
import sys

import cv2
import numpy as np

PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))
import training_ui as ui


def generate(source):
    fixtures = PYTHON_DIR / "tests/fixtures/ui_2026_10"
    for path in sorted(source.glob("panel_*.png")):
        panel = cv2.imread(str(path))
        if panel.shape != (572, 660, 3):
            raise ValueError(f"Not a panel-only crop: {path.name}")
        shutil.copyfile(path, fixtures / path.name)

    panel = cv2.imread(str(source / "panel_d2_02.png"))
    sheet = np.zeros((24, 200), np.uint8)
    observed = {}
    for y, expected in zip(ui.TRAINING_POINTS_ROW_Y.values(),
                           ("120/260", "117/260", "75/260", "137/260", "93/260")):
        mask = ui._points_text(panel[y:y + 20, 112:185])
        pieces = ui._digit_pieces(mask)
        assert len(pieces) == len(expected)
        for piece, char in zip(pieces, expected):
            if char != "/":
                observed.setdefault(int(char), ui._digit_mask(piece))
    for digit, mask in observed.items():
        sheet[:, digit * 20:(digit + 1) * 20] = mask * 255
    cv2.imwrite(str(PYTHON_DIR / "tpl_points_digits_v2.png"), sheet)
    cv2.imwrite(str(PYTHON_DIR / "tpl_points_title_v2.png"),
                ui._points_text(panel[18:50, 200:460]).astype(np.uint8) * 255)
    cv2.imwrite(str(PYTHON_DIR / "tpl_points_labels_v2.png"),
                np.hstack((ui._points_text(panel[94:114, 45:112]),
                           ui._points_text(panel[94:114, 544:579]))).astype(np.uint8) * 255)
    sheet = np.zeros((48, 200), np.uint8)
    for row, layout in enumerate(("standard", "ki")):
        label = cv2.imread(str(fixtures / f"progression_{layout}_0of20.png"))
        _, denominator = ui.progression_numbers(label)
        for digit, piece in zip((2, 0), ui._digit_pieces(denominator > 100)):
            sheet[row * 24:(row + 1) * 24, digit * 20:(digit + 1) * 20] = ui._digit_mask(piece) * 255
    cv2.imwrite(str(PYTHON_DIR / "tpl_progression_digits_v2.png"), sheet)


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
