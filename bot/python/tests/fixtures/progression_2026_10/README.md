# Progression label fixtures (October 2026 UI)

Live captures of the in-minigame Level label, used to test the progression
reader. The images are unmodified 360x32 standard or 520x26 Ki label-box crops
with no HUD.

`ground_truth.json` lists the number each label shows (null when no label is
visible), the source SHA-256, whether the image is included here, and the
completion result from the older image-comparison check.

`d2kid1_KiDamage_0020.png` shows 0/20, but the word runs below the crop, so the
reader returns None for it on purpose.

There's no 20/20 frame in this set. Tests cover 20/20 separately using captured
numeral strokes and the existing completion fixtures. The label-font 4 template
comes from frame 0420 (14/20) and the 8 from frame 0630 (18/20); the panel-font
templates are separate and never mixed with these.

From `bot/python`, regenerate label assets with
`python tests/make_progression_assets.py`. Check a folder of frames with
`python tests/evaluate_progression_levels.py PATH_TO_LEVEL_FRAMES`.
