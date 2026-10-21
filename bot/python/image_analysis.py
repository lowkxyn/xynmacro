"""Frame classification and image analysis helpers."""

import cv2
import numpy as np


def _hp_fill_is_critical(raw):
    """Classify a small, always-filled HP patch as critical red."""
    hsv = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 140) & (hsv[:, :, 2] > 140)
    vivid_count = int(np.count_nonzero(vivid))
    if vivid_count < int(raw.shape[0] * raw.shape[1] * 0.45):
        return False
    hue = hsv[:, :, 0]
    red = int(np.count_nonzero(((hue <= 5) | (hue >= 175)) & vivid))
    return red >= int(vivid_count * 0.82)


def _hp_fill_is_green(raw):
    """Classify the same opaque HP patch as restored green."""
    hsv = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 140) & (hsv[:, :, 2] > 140)
    vivid_count = int(np.count_nonzero(vivid))
    if vivid_count < int(raw.shape[0] * raw.shape[1] * 0.45):
        return False
    hue = hsv[:, :, 0]
    green = int(np.count_nonzero((hue >= 35) & (hue <= 75) & vivid))
    return green >= int(vivid_count * 0.82)


def _read_inventory_count(list_frame, row_top, digit_sheet):
    """Read the green stock number before selecting the Senzu row."""
    import xynmacro_core as core

    if digit_sheet is None:
        return None
    center_y = int(row_top - 395 + 18)
    crop = list_frame[max(0, center_y - 15):center_y + 15, 480:555]
    if crop.shape[0] < 15:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Green = normal stock display. Red = the row is selected for assignment
    # (GC keeps that state across menu opens — live 22:31 failure read None on
    # a still-selected row and aborted the whole refill).
    vivid = (hsv[:, :, 1] > 130) & (hsv[:, :, 2] > 100)
    green_digits = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85)
    red_digits = (hsv[:, :, 0] <= 8) | (hsv[:, :, 0] >= 175)
    mask = ((green_digits | red_digits) & vivid).astype(np.uint8)
    # Classify each digit on its own, scaled to a common size. Whole-template
    # matching needed pixel-exact glyphs: on one test client the digits render 12 px tall
    # against the sheet's 11, so "97" read as 9 and "20"/"50" as 0 (a false
    # empty stock). An unsure digit returns None, which never means empty.
    glyphs = core._inventory_digit_glyphs(digit_sheet)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    digits = []
    for index in sorted(range(1, count), key=lambda i: stats[i][0]):
        _x, _y, w, h, area = stats[index]
        if h < 8 or area < 10:
            continue  # anti-aliasing specks
        if not (h <= 18 and w <= 14):
            return None  # touching digits or not a number
        shape = core._normalized_glyph(labels == index)
        scores = sorted(
            ((core._mask_iou(shape, glyph), digit) for digit, glyph in enumerate(glyphs)),
            reverse=True,
        )
        best, second = scores[0], scores[1]
        if best[0] < core.INVENTORY_DIGIT_MIN_IOU or best[0] - second[0] < core.INVENTORY_DIGIT_MIN_MARGIN:
            return None
        digits.append(str(best[1]))
    if not digits or len(digits) > 7:
        return None
    return int("".join(digits))


def _glyph_iou(mask, first, second):
    def normalized(component):
        x, y, w, h, _area = component
        glyph = mask[y:y + h, x:x + w]
        return cv2.resize(glyph, (32, 32), interpolation=cv2.INTER_NEAREST) > 0

    a = normalized(first)
    b = normalized(second)
    union = np.count_nonzero(a | b)
    return (np.count_nonzero(a & b) / union) if union else 0.0


def _progression_completion_from_frame(frame):
    """Return True for X/X, False for a detected incomplete label, else None."""
    import xynmacro_core as core

    label = core._progression_label_glyphs(frame)
    if label is None:
        return None
    mask, left, right = label
    return len(left) == len(right) and all(
        core._glyph_iou(mask, a, b) >= 0.82 for a, b in zip(left, right)
    )


def _gravity_mask_from_frame(frame):
    bgr = frame[:, :, :3]
    channel_min = bgr.min(axis=2)
    channel_spread = bgr.max(axis=2) - channel_min
    return ((channel_min > 190) & (channel_spread < 55)).astype(np.uint8) * 255


def _gravity_control_glyph_count(mask):
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    glyphs = []
    for x, y, w, h, area in stats[1:count]:
        # Up to 34 px wide: some machines render the 4's crossbar one pixel
        # wider, so "40" arrives as one touching pair (23 px on one test client).
        if 2 <= w <= 34 and 10 <= h <= 24 and 20 <= area <= 320:
            glyphs.append((int(x), int(y), int(w), int(h), int(area)))
    if not 2 <= len(glyphs) <= 4:
        return 0
    baselines = [y + h for _x, y, _w, h, _area in glyphs]
    return len(glyphs) if max(baselines) - min(baselines) <= 4 else 0


def preprocess_solid(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 11, 2)
    kernel = np.ones((2, 2), np.uint8)
    dilated = cv2.dilate(thresh, kernel, iterations=1)
    proc = cv2.GaussianBlur(dilated, (3, 3), 0)
    return proc


def _ki_v8_local_contrast_profile(img_bgr, cx, cy, radii, n_samples=32, contrast_delta=8):
    """Count locally brighter ring samples for every candidate radius.

    The shrinking Ki ring is compared with pixels six places inward and outward
    at the same angle. Lab L keeps the comparison about lightness, so a global
    red, blue, or yellow cast does not need its own environment threshold.
    This runs beside the neutral-bright profile and becomes the tracked radius
    when its qualified peak is stronger.
    """
    if not radii:
        return []
    h, w = img_bgr.shape[:2]
    lab_l = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.int16)
    angles = np.linspace(0.0, 2.0 * np.pi, n_samples, endpoint=False)
    cos_a = np.cos(angles)
    sin_a = np.sin(angles)
    counts = []

    for radius in radii:
        ring_l = np.full(n_samples, -999, dtype=np.int16)
        ring_valid = np.zeros(n_samples, dtype=bool)
        for d_r in (-1, 0, 1):
            r = radius + d_r
            xs = (cx + r * cos_a).astype(np.int32)
            ys = (cy + r * sin_a).astype(np.int32)
            valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
            values = lab_l[np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)]
            ring_l = np.maximum(ring_l, np.where(valid, values, -999))
            ring_valid |= valid

        background_l = np.full(n_samples, -999, dtype=np.int16)
        background_valid = np.ones(n_samples, dtype=bool)
        for d_r in (-6, 6):
            r = radius + d_r
            xs = (cx + r * cos_a).astype(np.int32)
            ys = (cy + r * sin_a).astype(np.int32)
            valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
            values = lab_l[np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)]
            background_l = np.maximum(background_l, np.where(valid, values, -999))
            background_valid &= valid

        hits = ring_valid & background_valid & ((ring_l - background_l) >= int(contrast_delta))
        counts.append(int(hits.sum()))
    return counts


def _ki_v8_clamped_grab(sct, center_x_abs, center_y_abs, half):
    """Grab a square ROI around an absolute screen point, clamped to the virtual
    screen. Returns (img_bgr, local_cx, local_cy). When the dot sits near a screen
    edge the rectangle shrinks instead of shifting, so local_cx/local_cy stay the
    dot's true position inside the returned image (the old code pinned the origin
    at 0 but kept assuming the dot was at the center)."""
    screen = sct.monitors[0]
    left = max(screen["left"], center_x_abs - half)
    top = max(screen["top"], center_y_abs - half)
    right = min(screen["left"] + screen["width"], center_x_abs + half)
    bottom = min(screen["top"] + screen["height"], center_y_abs + half)
    grab = {"left": left, "top": top,
            "width": max(1, right - left), "height": max(1, bottom - top)}
    img = np.array(sct.grab(grab))[:, :, :3]
    return img, center_x_abs - left, center_y_abs - top


def _ki_v8_shrink_velocity_px_s(track_history):
    """Median shrink speed (px/s, positive = shrinking) from the last few
    detected radii of ONE method's history [(t_ms, ring_r, count), ...].
    None until 3 recent samples exist — prediction stays off while evidence
    is thin, so a lone noisy pair can't produce a huge phantom velocity."""
    recent = [(t_ms, r) for t_ms, r, _ in track_history[-8:] if r is not None][-5:]
    if len(recent) < 3:
        return None
    speeds = []
    for (t0, r0), (t1, r1) in zip(recent, recent[1:]):
        dt_s = (t1 - t0) / 1000.0
        if dt_s > 0:
            speeds.append((r0 - r1) / dt_s)
    if not speeds:
        return None
    speeds.sort()
    return speeds[len(speeds) // 2]


def is_area_green(img_bgr, center_x, center_y, box_size=18):
    # Was box_size=5 (11x11 patch). Bumped to 18 (37x37) because letter centroids
    # sometimes land on the dark stroke of the letter itself (D has a thick vertical
    # bar at its centroid). With 11x11, the patch could miss the green background
    # entirely; 37x37 always covers the slot's green halo.
    y_min = max(0, center_y - box_size)
    y_max = min(img_bgr.shape[0], center_y + box_size)
    x_min = max(0, center_x - box_size)
    x_max = min(img_bgr.shape[1], center_x + box_size)
    patch = img_bgr[y_min:y_max, x_min:x_max]
    if patch.size == 0: return False
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    green_mask = (g > 100) & (g.astype(np.int16) > (r.astype(np.int16) + 30)) & (g.astype(np.int16) > (b.astype(np.int16) + 30))
    return np.any(green_mask)


def is_area_red(img_bgr, center_x, center_y, box_size=18):
    """Mirror of is_area_green: returns True if the patch around (cx,cy) has any dominantly red pixels."""
    y_min = max(0, center_y - box_size)
    y_max = min(img_bgr.shape[0], center_y + box_size)
    x_min = max(0, center_x - box_size)
    x_max = min(img_bgr.shape[1], center_x + box_size)
    patch = img_bgr[y_min:y_max, x_min:x_max]
    if patch.size == 0: return False
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    red_mask = (r > 120) & (r.astype(np.int16) > (g.astype(np.int16) + 40)) & (r.astype(np.int16) > (b.astype(np.int16) + 40))
    return np.any(red_mask)


def _scans_match(a, b, tol_px=10):
    """Two letter-scan lists (already x-sorted) describe the same on-screen state."""
    if not a or not b: return False
    if len(a) != len(b): return False
    for (ac, ax, ay), (bc, bx, by) in zip(a, b):
        if ac != bc: return False
        if abs(ax - bx) > tol_px or abs(ay - by) > tol_px: return False
    return True
