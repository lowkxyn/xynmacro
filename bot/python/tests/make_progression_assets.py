"""Reproduce progression assets from the checked-in label-only fixtures.

Run from bot/python: python tests/make_progression_assets.py
No capture, OCR engine, panel-font substitutions or generated digit identities.
"""
from pathlib import Path
import sys

import cv2
import numpy as np

PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))
import training_ui as ui

FIXTURES = PYTHON_DIR / "tests/fixtures/progression_2026_10"
STANDARD_DIGITS = {
    0: "d3full_PhysicalDamage_0010", 1: "d3full_PhysicalDamage_0080",
    2: "d3full_PhysicalDamage_0160", 3: "d3full_PhysicalDamage_0240",
    4: "d2chain_Health_0420", 5: "d3full_PhysicalDamage_0410",
    6: "d2chain_Health_0010", 7: "d2chain_Health_0070",
    8: "d2chain_Health_0630", 9: "d2chain_Health_0180",
}
PREFIXES = {
    "standard": ("d3full_PhysicalDamage_0010", (172, 6, 104, 20)),
    "ki": ("d2k1_KiControl_0010", (357, 6, 89, 17)),
    "ki_large": ("d2kid1_KiDamage_0030", (357, 4, 94, 18)),
}


def fixture(name):
    image = cv2.imread(str(FIXTURES / f"{name}.png"))
    if image is None or image.shape[:2] not in ((32, 360), (26, 520)):
        raise ValueError(f"Missing or non-label fixture: {name}")
    return image


def generate():
    sheet = np.zeros((72, 200), np.uint8)
    for digit, name in STANDARD_DIGITS.items():
        numerator = ui.progression_numbers(fixture(name))[0]
        # 14 and 18 supply the previously missing 4 and 8.
        piece = ui._digit_pieces(numerator > 100)[-1]
        sheet[:24, digit * 20:(digit + 1) * 20] = ui._digit_mask(piece) * 255
    ki = cv2.imread(str(PYTHON_DIR / "tests/fixtures/ui_2026_10/progression_ki_0of20.png"))
    for digit, piece in zip((2, 0), ui._digit_pieces(ui.progression_numbers(ki)[1] > 100)):
        sheet[24:48, digit * 20:(digit + 1) * 20] = ui._digit_mask(piece) * 255
    sheet[24:48, 20:40] = ui._digit_mask(
        ui.progression_numbers(fixture("d2k1_KiControl_final"))[0] > 100) * 255
    large = ui._progression_text(fixture("d2kid1_KiDamage_0030"))
    for digit, start, end in ((0, 457, 466), (2, 474, 482)):
        sheet[48:72, digit * 20:(digit + 1) * 20] = ui._digit_mask(large[7:18, start:end]) * 255
    cv2.imwrite(str(PYTHON_DIR / "tpl_progression_digits_v2.png"), sheet)
    for layout, (name, box) in PREFIXES.items():
        x, y, w, h = box
        prefix = ui._progression_text(fixture(name))[y:y + h, x:x + w]
        cv2.imwrite(str(PYTHON_DIR / f"tpl_progression_prefix_{layout}_v2.png"),
                    prefix.astype(np.uint8) * 255)


if __name__ == "__main__":
    generate()
