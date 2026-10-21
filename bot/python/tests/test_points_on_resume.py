import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class PointsLeftOnResumeTests(unittest.TestCase):
    """A run started mid-minigame reads Points Left on the next trip through the menu."""

    def resume(self, targets):
        order = []
        with (
            patch.object(core, "CURRENT_TRAINING_STATE", "Ki Control"),
            patch.object(core, "CONTROLLER_PAUSED", False),
            patch.dict(core.PROGRESSION_TARGETS, targets, clear=True),
            patch.object(core, "_senzu_abort_requested", return_value=False),
            patch.object(core, "_training_menu_visible_for_senzu", side_effect=[(True, 1.0), (False, 0.0), (False, 0.0)]),
            patch.object(core, "_capture_points_panels", return_value=["a", "b"]),
            patch.object(core, "_record_points_target", side_effect=lambda *a: order.append("read")) as record,
            patch.object(core, "_button_screen_point", return_value=(100, 200)),
            patch.object(core, "click_at", side_effect=lambda *a: order.append("click")),
            patch.object(core, "_park_menu_cursor"),
            patch.object(core.time, "sleep"),
        ):
            self.assertTrue(core._resume_training_after_senzu(Mock(), {"training": object()}))
        return record, order

    def test_unknown_points_are_read_before_reselecting_the_stat(self):
        record, order = self.resume({})
        record.assert_called_once_with("Ki Control", ["a", "b"])
        self.assertEqual(order, ["read", "click"])

    def test_a_reached_or_full_cap_is_not_read_again(self):
        with patch.object(core, "_POINTS_CAP_REACHED", {"Ki Control"}):
            record, order = self.resume({"Ki Control": (10, 20)})
        record.assert_not_called()
        self.assertEqual(order, ["click"])
        record, order = self.resume({"Ki Control": (20, 20)})
        record.assert_not_called()

    def test_a_known_cap_not_yet_reached_is_read_again(self):
        # The panel is the only reliable count when the label sits on a pale sky.
        with patch.object(core, "_POINTS_CAP_REACHED", set()):
            record, order = self.resume({"Ki Control": (10, 20)})
        record.assert_called_once()
        self.assertEqual(order, ["read", "click"])

    def test_zero_points_left_completes_at_the_current_count(self):
        # 10/20 with 0 Points Left: done at 10/20, so the next count read ends the stat.
        with (
            patch.dict(core.PROGRESSION_TARGETS, {}, clear=True),
            patch.object(core, "_read_training_points", return_value=(10, 20, 0)),
        ):
            core._record_points_target("Ki Control", ["a", "b"])
            self.assertEqual(core.PROGRESSION_TARGETS["Ki Control"], (10, 20))


if __name__ == "__main__":
    unittest.main()
