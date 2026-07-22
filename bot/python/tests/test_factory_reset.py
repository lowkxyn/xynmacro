import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

import xynmacro_core as core


class FactoryResetTests(unittest.TestCase):
    def test_factory_reset_applies_shipped_calibration_immediately(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            json_dir = data_dir / "json"
            json_dir.mkdir()
            (json_dir / "button_calibration.json").write_text(
                json.dumps({"Health": [1, 2]}), encoding="utf-8"
            )
            (json_dir / "region_calibration.json").write_text(
                json.dumps({"health_box": {"top": 1, "left": 2, "width": 3, "height": 4}}),
                encoding="utf-8",
            )
            (data_dir / "save_dir.txt").write_text("C:/custom", encoding="utf-8")

            with patch.multiple(
                core,
                DATA_DIR=str(data_dir),
                JSON_DIR=str(json_dir),
                MACRO_CONFIG_FILE=str(json_dir / "macro_config.json"),
                SAVE_DIR="C:/custom",
                BUTTONS=dict(core.DEFAULT_BUTTONS),
                HEALTH_BOX=dict(core.DEFAULT_HEALTH_BOX),
                AGILITY_BOX=dict(core.DEFAULT_AGILITY_BOX),
                USER_BUTTON_OVERRIDES={"Health": [1, 2]},
                USER_REGION_OVERRIDES={
                    "health_box": {"top": 1, "left": 2, "width": 3, "height": 4}
                },
            ):
                core.factory_reset_configuration()

                expected_buttons = json.loads(
                    (PYTHON_DIR / "defaults" / "button_calibration.json").read_text(
                        encoding="utf-8"
                    )
                )
                expected_regions = json.loads(
                    (PYTHON_DIR / "defaults" / "region_calibration.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(core.BUTTONS, {k: tuple(v) for k, v in expected_buttons.items()})
                self.assertEqual(core.HEALTH_BOX, expected_regions["health_box"])
                self.assertEqual(core.AGILITY_BOX, expected_regions["agility_box"])
                self.assertEqual(core.SAVE_DIR, str(data_dir / "saves"))
                self.assertFalse((data_dir / "save_dir.txt").exists())
                self.assertTrue((json_dir / "button_calibration.json").exists())
                self.assertTrue((json_dir / "region_calibration.json").exists())


if __name__ == "__main__":
    unittest.main()
