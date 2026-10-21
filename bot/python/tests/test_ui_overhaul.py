"""October 2026 UI overhaul: the GC TAB prompt became a two-line panel."""
import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

import xynmacro_core as core
import gc_recovery


PYTHON_DIR = Path(gc_recovery.__file__).parent
FIXTURES = Path(__file__).parent / 'fixtures'
LEGACY = cv2.imread(str(PYTHON_DIR / 'tpl_gc_enter.png'), cv2.IMREAD_GRAYSCALE)
# Areas of the normalized capture, located by eye on the fixture.
PANEL = (slice(266, 311), slice(20, 404))
TITLE_LINE = (slice(268, 289), slice(36, 270))
PRESS_LINE = (slice(289, 308), slice(36, 270))
TEXT_RIGHT_HALF = (slice(268, 308), slice(150, 270))


@pytest.fixture(autouse=True)
def fresh_v2_state():
    gc_recovery._v2_template.cache_clear()
    gc_recovery._v2_scale = None
    yield
    gc_recovery._v2_template.cache_clear()
    gc_recovery._v2_scale = None


def new_idle():
    return cv2.imread(str(FIXTURES / 'ui_overhaul' / 'gc_idle_1920x1080.png'))


def legacy_frames():
    return sorted((FIXTURES / 'gc_recovery').glob('*.png'))


def without(frame, *areas):
    """Paint text areas with the panel's own empty background."""
    frame = frame.copy()
    fill = frame[290, 385].copy()
    for area in areas:
        frame[area] = fill
    return frame


def test_new_prompt_in_the_real_gc_room_is_recognized():
    frame = new_idle()
    score, scale = gc_recovery.v2_prompt_match(frame)
    assert score >= gc_recovery.V2_THRESHOLD, (score, scale)
    assert gc_recovery.room_visible(frame)
    assert gc_recovery.enter_prompt_visible(frame, LEGACY)
    # The room is confirmed. The sanitizing mask merges into the support
    # column, so only the safety edge is asserted here.
    landmarks = gc_recovery.room_landmarks(frame, LEGACY)
    assert landmarks is not None
    assert landmarks[1] is not None and 600 < landmarks[1] < 800, landmarks


def test_four_channel_capture_is_accepted():
    frame = cv2.cvtColor(new_idle(), cv2.COLOR_BGR2BGRA)
    assert gc_recovery.enter_prompt_visible(frame, LEGACY)


def test_old_gc_fixtures_keep_their_legacy_answer():
    for path in legacy_frames():
        frame = cv2.imread(str(path))
        bgr = gc_recovery._canonical_bgr(frame)
        legacy = bgr is not None and gc_recovery._legacy_prompt_visible(bgr, LEGACY)
        assert gc_recovery.enter_prompt_visible(frame, LEGACY) == legacy, path.name
        score, _ = gc_recovery.v2_prompt_match(frame)
        assert score < gc_recovery.V2_THRESHOLD - .1, (path.name, score)
    assert gc_recovery.enter_prompt_visible(
        cv2.imread(str(FIXTURES / 'gc_recovery' / 'respawn_02.png')), LEGACY)


@pytest.mark.parametrize('areas', [
    (TITLE_LINE,), (PRESS_LINE,), (TEXT_RIGHT_HALF,), (PANEL,),
], ids=['title-hidden', 'press-line-hidden', 'right-half-clipped', 'panel-absent'])
def test_partial_or_absent_prompt_is_rejected(areas):
    frame = without(new_idle(), *areas)
    score, _ = gc_recovery.v2_prompt_match(frame)
    assert score < gc_recovery.V2_THRESHOLD, score
    assert not gc_recovery.enter_prompt_visible(frame, LEGACY)
    assert gc_recovery.room_landmarks(frame, LEGACY) is None


def test_a_confirmed_scale_does_not_carry_over_to_the_next_frame():
    assert gc_recovery.enter_prompt_visible(new_idle(), LEGACY)
    assert gc_recovery._v2_scale is not None
    assert not gc_recovery.enter_prompt_visible(without(new_idle(), PRESS_LINE), LEGACY)


@pytest.mark.parametrize('background', [0, 200], ids=['black', 'light'])
def test_prompt_outside_the_gc_room_is_not_enough(background):
    y0, y1, x0, x1 = gc_recovery.V2_PROMPT_BOX
    frame = np.full((1080, 1920, 3), background, np.uint8)
    frame[y0:y1, x0:x1] = new_idle()[y0:y1, x0:x1]
    score, _ = gc_recovery.v2_prompt_match(frame)
    assert score >= gc_recovery.V2_THRESHOLD  # the text itself is there...
    assert not gc_recovery.room_visible(frame)
    assert not gc_recovery.enter_prompt_visible(frame, LEGACY)  # ...but not the room


def rescaled_prompt(fx, fy):
    """Simulate another window shape by rescaling the HUD corner in place."""
    y0, y1, x0, x1 = gc_recovery.V2_PROMPT_BOX
    frame = new_idle()
    corner = cv2.resize(frame[y0:y1, x0:x1], None, fx=fx, fy=fy, interpolation=cv2.INTER_AREA)
    h, w = min(corner.shape[0], y1 - y0), min(corner.shape[1], x1 - x0)
    frame[y0:y0 + h, x0:x0 + w] = corner[:h, :w]
    return frame


@pytest.mark.parametrize('fx, fy', [(.9, 1.0), (1.0, .92), (.85, 1.1)])
def test_bounded_scale_search_tolerates_other_window_shapes(fx, fy):
    frame = rescaled_prompt(fx, fy)
    score, scale = gc_recovery.v2_prompt_match(frame)
    assert score >= gc_recovery.V2_THRESHOLD, (score, scale)
    assert gc_recovery.enter_prompt_visible(frame, LEGACY)


def test_scale_search_is_bounded():
    frame = rescaled_prompt(.5, .5)
    score, scale = gc_recovery.v2_prompt_match(frame)
    assert score < gc_recovery.V2_THRESHOLD, (score, scale)
    assert not gc_recovery.enter_prompt_visible(frame, LEGACY)


@pytest.mark.parametrize('frame', [
    None,
    [],
    np.zeros((0, 0, 3), np.uint8),
    np.zeros((10, 10, 3), np.uint8),
    np.zeros((1080, 1920), np.uint8),
    np.zeros((1080, 1920, 3), np.uint8),
    np.full((1080, 1920, 3), 255, np.uint8),
    np.zeros((1080, 1920, 3), np.float32),
    np.zeros((1080, 1920, 1), np.uint8),
], ids=['none', 'list', 'empty', 'tiny', 'gray', 'black', 'white', 'float', 'one-channel'])
def test_invalid_or_blank_images_never_throw_or_match(frame):
    assert not gc_recovery.enter_prompt_visible(frame, LEGACY)
    assert not gc_recovery.room_visible(frame)
    assert gc_recovery.room_landmarks(frame, LEGACY) is None
    assert gc_recovery.v2_prompt_match(frame) == (0.0, None)


def test_uncanonical_capture_is_rejected():
    raw = cv2.imread(str(FIXTURES / 'ui_overhaul' / 'gc_idle_1410x1001.png'))
    assert raw.shape[:2] == (1001, 1410)
    assert not gc_recovery.enter_prompt_visible(raw, LEGACY)


@pytest.mark.parametrize('legacy', [None, np.zeros((5, 5, 3), np.uint8),
                                    np.zeros((200, 900), np.uint8)],
                         ids=['none', 'color', 'oversized'])
def test_missing_or_damaged_legacy_template_is_safe(legacy):
    assert not gc_recovery.enter_prompt_visible(
        cv2.imread(str(FIXTURES / 'gc_recovery' / 'respawn_02.png')), legacy)
    # The new prompt carries its own template.
    assert gc_recovery.enter_prompt_visible(new_idle(), legacy)


@pytest.mark.parametrize('legacy', [
    np.zeros((0, 0), np.uint8),
    np.zeros((0, 100), np.uint8),
    np.zeros((20, 0), np.uint8),
    np.zeros((1, 1), np.uint8),
    np.zeros((1, 100), np.uint8),
    np.zeros((20, 1), np.uint8),
    np.zeros((20, 100), np.uint8),
    np.full((20, 100), 255, np.uint8),
    np.tile(np.arange(100, dtype=np.uint8), (1, 1)),
], ids=['empty', 'zero-height', 'zero-width', 'one-pixel', 'one-row', 'one-column',
        'black', 'white', 'one-row-gradient'])
def test_empty_or_constant_legacy_template_cannot_authorize_a_blank_frame(legacy):
    for value in (0, 255):
        blank = np.full((1080, 1920, 3), value, np.uint8)
        assert not gc_recovery._legacy_prompt_visible(blank, legacy)
        assert not gc_recovery.enter_prompt_visible(blank, legacy)
        assert gc_recovery.room_landmarks(blank, legacy) is None
    assert not gc_recovery.enter_prompt_visible(
        cv2.imread(str(FIXTURES / 'gc_recovery' / 'respawn_02.png')), legacy)
    # The new prompt carries its own template.
    assert gc_recovery.enter_prompt_visible(new_idle(), legacy)


def test_missing_new_asset_leaves_legacy_support_working(tmp_path, monkeypatch):
    monkeypatch.setattr(gc_recovery, '__file__', str(tmp_path / 'gc_recovery.py'))
    gc_recovery._v2_template.cache_clear()
    assert gc_recovery._v2_template() is None
    assert not gc_recovery.enter_prompt_visible(new_idle(), LEGACY)
    assert gc_recovery.enter_prompt_visible(
        cv2.imread(str(FIXTURES / 'gc_recovery' / 'respawn_02.png')), LEGACY)


def test_blank_new_asset_is_treated_as_missing(tmp_path, monkeypatch):
    cv2.imwrite(str(tmp_path / 'tpl_gc_enter_v2.png'), np.full((32, 196), 40, np.uint8))
    monkeypatch.setattr(gc_recovery, '__file__', str(tmp_path / 'gc_recovery.py'))
    gc_recovery._v2_template.cache_clear()
    assert gc_recovery._v2_template() is None
    assert not gc_recovery.enter_prompt_visible(new_idle(), LEGACY)


def test_new_asset_ships_in_the_maintained_spec():
    spec = ast.parse((PYTHON_DIR / 'XynMacro-core.spec').read_text(encoding='utf-8'))
    analysis = next(node for node in ast.walk(spec)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'Analysis')
    datas = ast.literal_eval(next(arg.value for arg in analysis.keywords if arg.arg == 'datas'))
    assert ('tpl_gc_enter_v2.png', '.') in datas
    assert ('tpl_gc_enter.png', '.') in datas
    template = cv2.imread(str(PYTHON_DIR / 'tpl_gc_enter_v2.png'), cv2.IMREAD_GRAYSCALE)
    assert template.shape == (32, 196)


def test_stale_new_prompt_before_the_loading_screen_sends_no_movement():
    # Same respawn safety as the legacy prompt: a prompt that does not stay
    # visible through the settle frames never starts movement.
    context = MagicMock()
    context.__enter__.return_value = object()
    loading = np.zeros((1080, 1920, 3), np.uint8)
    stable = core.GC_RESPAWN_STABLE_FRAMES
    frames = [new_idle()] * (stable - 1) + [loading] * (core.GC_RESPAWN_LOADING_FRAMES - stable + 1)
    with patch.object(core, '_USER_STOP_LATCHED', False), \
         patch.object(core, 'AUTO_RETRY_WALK_OUT', True), \
         patch.object(core, 'PAUSE_TOGGLE_REQUESTED', False), \
         patch.object(core, 'CONTROLLER_PAUSED', False), \
         patch.object(core, '_auto_retry_wait', return_value=True), \
         patch.object(core, 'focus_game_window', return_value=True), \
         patch.object(core, '_gc_recovery_ready', return_value=True), \
         patch.object(core, '_confirmed_game_capture_rect', return_value={}), \
         patch.object(core.mss, 'MSS', return_value=context), \
         patch.object(core, '_gc_death_dialog_visible', return_value=False), \
         patch.object(core, '_grab_reference_box', side_effect=frames), \
         patch.object(core, '_gc_recovery_mouse') as mouse, \
         patch.object(core, '_gc_recovery_hold') as hold:
        assert not core._auto_retry_walk_forward()
    mouse.assert_not_called()
    hold.assert_not_called()
