import ast
import re
from pathlib import Path
import shutil

import cv2
import numpy as np
import pytest

import majin_vision
import xynmacro_core as core


PYTHON_DIR = Path(core.__file__).parent
BOT_DIR = PYTHON_DIR.parent


def _spec_assets():
    spec = ast.parse((PYTHON_DIR / "XynMacro-core.spec").read_text(encoding="utf-8"))
    analysis = next(node for node in ast.walk(spec)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Analysis")
    return ast.literal_eval(next(arg.value for arg in analysis.keywords if arg.arg == "datas"))


def test_release_uses_spec_as_its_only_asset_inventory():
    script = (BOT_DIR / "scripts/build_sidecar.ps1").read_text(encoding="utf-8")
    workflow = (BOT_DIR.parent / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "build_sidecar.ps1" in workflow
    assert "-m PyInstaller --noconfirm --clean XynMacro-core.spec" in script
    assert "--add-data" not in script
    assert "--hidden-import" not in script
    assets = dict(_spec_assets())
    for name in ("tpl_menu_digits.png", "tpl_majin_transformations.png",
                 "tpl_majin_enter_training.png"):
        assert assets[name] == "."
    assert len(assets) == len(_spec_assets())
    assert all((PYTHON_DIR / name).exists() for name in assets)


def test_authoritative_spec_is_exempt_from_generated_artifact_ignores():
    # The repository ignores generated *.spec files. The maintained build input
    # must remain visible to the owner's eventual source commit.
    rules = (PYTHON_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "!XynMacro-core.spec" in rules


def test_spec_staged_bundle_loads_and_recognizes_new_templates(tmp_path, monkeypatch):
    for name, destination in _spec_assets():
        source = PYTHON_DIR / name
        target = tmp_path / destination
        target.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copyfile(source, target / source.name)
    monkeypatch.setattr(core, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(core, "_MENU_DIGIT_GLYPHS", None)
    monkeypatch.setattr(majin_vision, "__file__", str(tmp_path / "majin_vision.py"))
    majin_vision._template.cache_clear()
    try:
        fixtures = PYTHON_DIR / "tests/fixtures"
        panel = cv2.imread(str(fixtures / "training_points_panel.png"))
        assert core._read_training_points(panel, "Health") == (0, 20, 130)
        panel = cv2.imread(str(fixtures / "majin/115005_0_panel.png"))
        prompt = cv2.imread(str(fixtures / "majin/115005_0_enter.png"))
        assert majin_vision.slot_state(panel, 3) == "ready"
        assert majin_vision.enter_prompt_visible(prompt)
    finally:
        majin_vision._template.cache_clear()


@pytest.mark.parametrize("sheet", [None, np.zeros((1, 1), dtype=np.uint8)])
def test_missing_or_damaged_menu_digits_disable_optional_reader(monkeypatch, sheet):
    reads = []
    monkeypatch.setattr(core, "_MENU_DIGIT_GLYPHS", None)
    monkeypatch.setattr(core.cv2, "imread", lambda *args: reads.append(args) or sheet)
    labels = np.ones((22, 16), dtype=np.uint8)
    assert core._classify_number(labels, [1]) is None
    assert core._classify_number(labels, [1]) is None
    assert len(reads) == 1


def test_missing_majin_templates_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(majin_vision, "__file__", str(tmp_path / "majin_vision.py"))
    majin_vision._template.cache_clear()
    try:
        panel = np.zeros((332, 430, 3), dtype=np.uint8)
        assert majin_vision.slot_state(panel, 3) == "unknown"
        assert not majin_vision.enter_prompt_visible(np.zeros((55, 395, 3), dtype=np.uint8))
    finally:
        majin_vision._template.cache_clear()


def test_every_template_the_code_loads_is_packed():
    # Build 6 shipped without the gold Training Mode heading: the code loaded it, the
    # spec didn't pack it, so cv2.imread returned None in the installed app.
    packed = {name for name, _ in _spec_assets()}
    used = set()
    for source in PYTHON_DIR.glob("*.py"):
        used |= set(re.findall(r"tpl_\w+\.png", source.read_text(encoding="utf-8")))
    assert sorted(used - packed) == []
