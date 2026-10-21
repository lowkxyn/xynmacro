# October 2026 progression label evidence

All 191 supplied crops were inspected by eye, in enlarged contact sheets and
individual enlarged images where glyph animation needed closer inspection.
`ground_truth.json` records the displayed numerator (null when no progression
label is visible), the source SHA-256, whether the image is included here, and
the original image-comparison completion result. No values are interpolated
from run order or training history. The 54 copied fixtures are unmodified
360x32 standard or 520x26 Ki label-box crops, with no HUD.

Source: live captures of the in-minigame Level label.

| Run | Correct labels before | Correct labels after | Non-labels rejected after |
| --- | ---: | ---: | ---: |
| d2a2 | 3/8 | 8/8 | 1/1 |
| d2chain | 0/74 | 74/74 | 3/3 |
| d2k1 | 5/6 | 6/6 | 1/1 |
| d2kid1 | 1/5 | 4/5 | 2/2 |
| d2s1 | 6/20 | 20/20 | 1/1 |
| d3full | 15/53 | 53/53 | 6/6 |
| d3h0g | 10/10 | 10/10 | 1/1 |

Total: 175/176 visible labels correctly read, 15/15 non-labels rejected, zero
incorrect values. `d2kid1_KiDamage_0020.png` visibly says 0/20, but its enlarged
word extends below the crop, so its label anchor is incomplete and the numeric
reader deliberately returns None. The completion result on every source crop
is unchanged. The baseline used the original source and original template
bytes from before this change, not the replacement assets.

The d2chain images show 6 through 19, then 1/20 in frame 0750 and the final
crop. There is no visible 20/20 in this supplied corpus. Tests cover 20/20
separately using the actual captured numeral strokes, including the existing
standard and Ki completion fixtures. The 4 template comes from frame 0420
(14/20); the 8 comes from frame 0630 (18/20). Panel-font templates are unchanged;
label-font 4/8 are never substituted into the panel reader.

From `bot/python`, regenerate label assets with
`python tests/make_progression_assets.py`. Re-evaluate the full corpus with
`python tests/evaluate_progression_levels.py PATH_TO_LEVEL_FRAMES`.
