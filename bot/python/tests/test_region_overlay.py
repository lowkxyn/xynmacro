"""The debug HUD draws the scan regions over Roblox. Its value depends entirely on
drawing the SAME regions the scanner reads, so the region list is shared with the
diagnostics preview and published to the HUD through /state."""
from unittest.mock import patch

import xynmacro_core as core


class TestRegionList:
    def test_labels_every_region_the_scanner_reads(self):
        labels = [name for name, _box, _colour in core._diagnostic_regions()]
        assert labels == [
            "Training menu", "Health", "WASD", "HP", "Gravity", "Progression",
        ]

    def test_boxes_are_the_live_globals_not_copies(self):
        # Calibration reassigns these globals; a snapshot taken at import time would
        # leave the HUD drawing the old box after a recalibration.
        regions = dict((name, box) for name, box, _colour in core._diagnostic_regions())
        assert regions["Health"] is core.HEALTH_BOX
        assert regions["WASD"] is core.AGILITY_BOX

    def test_picks_up_a_recalibrated_region(self):
        moved = {"left": 10, "top": 20, "width": 30, "height": 40}
        with patch.object(core, "HEALTH_BOX", moved):
            regions = dict((n, b) for n, b, _c in core._diagnostic_regions())
            assert regions["Health"] == moved

    def test_every_box_has_the_four_keys_the_drawing_code_indexes(self):
        for name, box, _colour in core._diagnostic_regions():
            assert set(box) >= {"left", "top", "width", "height"}, name

    def test_colours_are_three_channel_bytes(self):
        for name, _box, colour in core._diagnostic_regions():
            assert len(colour) == 3, name
            assert all(0 <= channel <= 255 for channel in colour), name


class TestOverlayColour:
    def test_converts_bgr_to_css_hex(self):
        # OpenCV hands out BGR; CSS wants #rrggbb. Swapping these silently recolours
        # every box, which is exactly the kind of thing nobody notices by eye.
        assert core._overlay_colour((50, 180, 255)) == "#ffb432"

    def test_pads_single_digit_channels(self):
        assert core._overlay_colour((1, 2, 3)) == "#030201"


class TestScanRegionPayload:
    def test_publishes_one_entry_per_region(self):
        payload = core._scan_region_payload()
        assert len(payload) == len(core._diagnostic_regions())

    def test_entries_carry_everything_the_hud_draws_with(self):
        for entry in core._scan_region_payload():
            assert set(entry) == {"name", "left", "top", "width", "height", "colour"}

    def test_colours_are_css_hex(self):
        for entry in core._scan_region_payload():
            assert entry["colour"].startswith("#"), entry
            assert len(entry["colour"]) == 7, entry
            int(entry["colour"][1:], 16)  # raises if it is not real hex

    def test_coordinates_stay_canonical_rather_than_screen_pixels(self):
        # The HUD applies the same width/1920, height/1080 scaling the scanner does.
        # Pre-scaling here would hide exactly the skew the HUD exists to reveal.
        with patch.object(core, "GAME_OFFSET_X", 500), \
             patch.object(core, "GAME_OFFSET_Y", 300), \
             patch.object(core, "GAME_WIDTH", 1280), \
             patch.object(core, "GAME_HEIGHT", 720):
            entry = core._scan_region_payload()[1]
        assert entry["name"] == "Health"
        assert entry["left"] == core.HEALTH_BOX["left"]
        assert entry["top"] == core.HEALTH_BOX["top"]

    def test_is_json_serializable(self):
        import json
        json.dumps(core._scan_region_payload())

    def test_reaches_the_ui_through_the_state_snapshot(self):
        with patch.object(core, "update_game_window", return_value=False):
            state = core._ui_state_snapshot()
        assert state["scan_regions"] == core._scan_region_payload()
