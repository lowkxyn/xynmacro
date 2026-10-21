"""Bug report assembly and live capture diagnostics."""

import hashlib

# Recent previews, oldest first, so a report open in the dialog can still be sent.
_reviewed_reports = {}
_REVIEWED_REPORT_LIMIT = 8

# --- Bug report ---------------------------------------------------------------------
# Everything a bug report needs, assembled here rather than in the WebView so the
# exact text the user approves is the exact text that gets sent. Unlike
# _diagnostic_report() this never hard-fails: people file bugs precisely when the
# game is closed or something is broken.


def _capture_brightness_profile():
    """Measure the levels in the macro's own screen capture.

    Detection compares pixels against fixed brightness values, so a capture that
    arrives brighter than the detector expects breaks colour checks while the
    game looks completely normal on screen — the failure is invisible to the
    person reporting it. Black floor is the tell: a real SDR capture of the game
    puts it near zero, and anything lifting the pipeline (HDR, a display mode
    that alters gamma, in-game brightness) raises it.

    Returns None rather than raising: a bug report must still send.
    """
    import xynmacro_core as core

    try:
        core.update_game_window()
        if core.GAME_HWND is not None and core.GAME_WIDTH > 0 and core.GAME_HEIGHT > 0:
            region = {"left": core.GAME_OFFSET_X, "top": core.GAME_OFFSET_Y,
                      "width": core.GAME_WIDTH, "height": core.GAME_HEIGHT}
            source = "Roblox client"
        else:
            region = None
            source = "primary screen (Roblox not found)"
        with core.mss.mss() as sct:
            frame = core.np.array(sct.grab(region or sct.monitors[1]))
        # Same measure the detectors use: max channel, not luminance.
        max_channel = frame[:, :, :3].max(axis=2)
        floor, median, ceiling = core.np.percentile(max_channel, (1, 50, 99))
        return {
            "source": source,
            "black_floor": int(floor),
            "median": int(median),
            "white_point": int(ceiling),
        }
    except Exception:
        return None


def _bug_report_sections(selected, webview=None, ui_errors=None):
    """Build {section: [(label, value), ...]} for the chosen sections only."""
    import xynmacro_core as core

    selected = [s for s in (selected or []) if s in core.BUG_REPORT_SECTIONS]
    out = {}

    if "system" in selected:
        profile = core._system_profile()
        out["System"] = [
            ("OS", profile.get("os")),
            ("CPU", profile.get("cpu")),
            ("GPU", profile.get("gpu")),
            ("RAM", f"{profile['ram_gb']} GB" if profile.get("ram_gb") else None),
            ("Threads", profile.get("cpu_threads")),
            # The single most useful field: a WebView2 update once broke every
            # button in the app, and this is what identifies it.
            ("WebView2", webview),
            ("Packaged build", "yes" if profile.get("frozen") else "no (from source)"),
        ]

    if "display" in selected:
        screen = core._get_screen_info() or {}
        monitor = core._current_game_monitor_info() or {}

        def _size(source, width_key="width", height_key="height"):
            """Format WxH, or None when it is unknown — a stray '0x0' in a public
            issue reads as a real measurement rather than a missing one."""
            width, height = source.get(width_key), source.get(height_key)
            return f"{width}x{height}" if width and height else None

        out["Display"] = [
            ("Virtual screen", _size(screen)),
            ("Game monitor", _size(monitor)),
            ("Work area", _size(monitor, "work_width", "work_height")),
        ]

    if "game" in selected:
        core.update_game_window()
        if core.GAME_HWND is None:
            out["Roblox"] = [
                ("Window", "minimized" if core.GAME_WINDOW_MINIMIZED else "not found"),
            ]
        else:
            aspect = core.GAME_WIDTH / max(1, core.GAME_HEIGHT)
            out["Roblox"] = [
                ("Client size", f"{core.GAME_WIDTH}x{core.GAME_HEIGHT}"),
                ("Client origin", f"{core.GAME_OFFSET_X},{core.GAME_OFFSET_Y}"),
                ("Aspect", f"{aspect:.3f}" + ("" if abs(aspect - 16 / 9) < 0.02 else " (not 16:9)")),
                ("Scale X/Y",
                 f"{core.GAME_WIDTH / core.GAME_REFERENCE_WIDTH:.3f} / "
                 f"{core.GAME_HEIGHT / core.GAME_REFERENCE_HEIGHT:.3f}"),
                ("Fullscreen", core.game_window_is_fullscreen()),
            ]

    if "settings" in selected:
        config = core._ui_config_snapshot()
        # The settings that actually change detection or timing. The full config is
        # long and mostly noise in a bug report.
        keys = [
            "agility_mode", "health_mode", "ki_v8_mode", "training_order",
            "start_delay_sec", "restore_fullscreen_on_start", "windowed_mode_on_start",
            "senzu_enabled", "auto_retry_on_failure", "diagnostic_mode",
            "ki_adaptive_brightness", "trait_click_approach",
        ]
        out["Settings"] = [(key, config.get(key)) for key in keys]

    if "capture" in selected:
        rows = []
        profile = core._capture_brightness_profile()
        if profile is None:
            rows.append(("Levels", "could not capture"))
        else:
            rows.append(("Captured from", profile["source"]))
            rows.append((
                "Levels",
                f"black floor {profile['black_floor']}, "
                f"median {profile['median']}, "
                f"white point {profile['white_point']} (0-255)",
            ))
            # Only meaningful against the game: the app's own window has no
            # true black to measure, so a high floor there says nothing.
            if profile["black_floor"] > 25 and profile["source"] == "Roblox client":
                rows.append((
                    "Note",
                    "Black is not arriving as black. Something between the game "
                    "and the capture is lifting brightness, which is what breaks "
                    "colour-based detection.",
                ))

        rejection = core._ki_v8_last_digit_rejection
        if rejection:
            age = max(0, int(core.time.time() - rejection["at"]))
            rows.append(("Last Ki dot rejected", f"{age}s ago, dot radius {rejection['dot_r']}"))
            rows.append((
                "Ki digit check",
                f"{rejection['columns']} matching columns, "
                f"using threshold {rejection['threshold']}, "
                f"needed {rejection['needed']}, "
                f"dot brightness {rejection['dot_brightness']}"
                + (", adaptive on" if rejection["adaptive"] else ", adaptive off"),
            ))
        else:
            rows.append(("Last Ki dot rejected", "none this session"))

        out["Capture"] = rows

    if "logs" in selected:
        recent = list(core._ui_log_ring)[-25:]
        rows = [
            ("Last error", core.MACRO_LAST_ERROR or "none"),
            ("Errors this session", core.MACRO_ERROR_COUNT),
        ]
        # Frontend errors live only in the WebView, so the UI hands them over. Without
        # them a report can read as perfectly healthy while the real failure was on
        # that side — which is exactly how the CSP breakage looked.
        if ui_errors:
            rows.append(
                ("Interface errors", "\n".join(str(line) for line in ui_errors[-10:]))
            )
        rows.append(("Tail", core._shareable_log(recent) if recent else "empty"))
        out["Recent log"] = rows

    # The log of the run that died is in a different file from the current one, and
    # it is the only place the cause could be. Offered only when there was a crash.
    if "crash" in selected and core.PREVIOUS_SESSION_CRASH:
        started = core.PREVIOUS_SESSION_CRASH.get("started_at")
        out["Previous session (ended unexpectedly)"] = [
            ("Version", core.PREVIOUS_SESSION_CRASH.get("version") or "unknown"),
            ("Started", core.time.strftime("%Y-%m-%d %H:%M:%S", core.time.localtime(started))
             if started else None),
            ("Tail", core.PREVIOUS_SESSION_CRASH.get("log_tail") or "the log file is gone"),
        ]

    return out


def build_bug_report(selected=None, webview=None, description=None, ui_errors=None):
    """Return the Markdown a user is about to post, already scrubbed."""
    import xynmacro_core as core

    # APP_VERSION is handed over by the launcher; running the sidecar directly
    # leaves it unset, and "XynMacro None" in a public issue looks like a bug.
    lines = [
        f"**XynMacro** {core.APP_VERSION or 'unknown version'}",
        "",
    ]
    text = (description or "").strip()
    lines += ["### What happened", text or "_(not described)_", ""]

    for title, rows in core._bug_report_sections(
            selected, webview=webview, ui_errors=ui_errors).items():
        lines.append(f"### {title}")
        for label, value in rows:
            if value is None or value == "":
                continue
            if isinstance(value, str) and "\n" in value:
                lines += [f"**{label}**", "```", value.strip(), "```"]
            else:
                lines.append(f"- **{label}:** {value}")
        lines.append("")

    return core._scrub_user_paths("\n".join(lines).strip())


def bug_report_payload(selected=None, webview=None, description=None, ui_errors=None):
    import xynmacro_core as core

    body = core.build_bug_report(selected, webview=webview, description=description,
                            ui_errors=ui_errors)
    query = core.urlencode({"title": f"[{core.APP_VERSION}] ", "body": body})
    url = f"{core.ISSUE_URL}?{query}"
    too_long = len(url) > core.ISSUE_URL_LIMIT
    report_id = None
    if not too_long:
        # Logs keep moving after the preview, so Open sends back this id and gets
        # exactly the previewed link instead of a fresh build.
        report_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
        _reviewed_reports.pop(report_id, None)
        _reviewed_reports[report_id] = url
        while len(_reviewed_reports) > _REVIEWED_REPORT_LIMIT:
            _reviewed_reports.pop(next(iter(_reviewed_reports)))
    return {
        "ok": True,
        "markdown": body,
        "url": None if too_long else url,
        "too_long": too_long,
        "report_id": report_id,
    }


def reviewed_bug_report_url(report_id):
    """The issue link for a report the user previewed, or None if it is unknown."""
    return _reviewed_reports.get(report_id) if isinstance(report_id, str) else None


def _diagnostic_report():
    """Describe the exact capture/input environment for remote support."""
    import xynmacro_core as core

    if core.GAME_HWND is None:
        core.update_game_window()
    if core.GAME_HWND is None:
        return {
            "ok": False,
            "summary": "Roblox window not found",
            "issues": ["Open DBOG before collecting diagnostics."],
        }

    geometry = core._game_geometry()
    left, top, width, height = geometry
    monitor = core._current_game_monitor_info() or {}
    monitor_width = int(monitor.get("width", 0))
    monitor_height = int(monitor.get("height", 0))
    mode = "borderless/fullscreen" if (
        left == int(monitor.get("left", left))
        and top == int(monitor.get("top", top))
        and width == monitor_width
        and height == monitor_height
    ) else "windowed"
    aspect = width / max(1, height)
    issues = []
    if core._user32.IsIconic(core.GAME_HWND):
        issues.append("Roblox is minimized; screen capture cannot see the game.")
    if abs(aspect - (16 / 9)) > 0.035:
        issues.append(
            f"Roblox client is not 16:9 ({width}x{height}); detection is scaled but may be less reliable."
        )
    if width < 1280 or height < 720:
        issues.append("Roblox client is below 1280x720; small templates may lose detail.")
    training_order = list(core._sanitize_training_order(core.TRAINING_ORDER_CUSTOM))
    if not training_order:
        issues.append("Training Order is empty.")
    invalid_buttons = [
        name for name, (x, y) in core.BUTTONS.items()
        if not (0 <= int(x) < core.GAME_REFERENCE_WIDTH and 0 <= int(y) < core.GAME_REFERENCE_HEIGHT)
    ]
    if invalid_buttons:
        issues.append(f"Trait calibration is outside the game client: {', '.join(invalid_buttons)}")
    invalid_regions = []
    for name, box in (("Health", core.HEALTH_BOX), ("Agility", core.AGILITY_BOX)):
        if (box["left"] < 0 or box["top"] < 0
                or box["left"] + box["width"] > core.GAME_REFERENCE_WIDTH
                or box["top"] + box["height"] > core.GAME_REFERENCE_HEIGHT):
            invalid_regions.append(name)
    if invalid_regions:
        issues.append(f"Scan calibration is outside the game client: {', '.join(invalid_regions)}")
    required_templates = (
        "tpl_training_mode.png", "tpl_game_menu.png", "tpl_inventory_menu.png",
        "tpl_senzu_bean.png", "tpl_slot_senzu.png",
    )
    missing_templates = [
        name for name in required_templates if not core.os.path.isfile(core.os.path.join(core.BASE_DIR, name))
    ]
    if missing_templates:
        issues.append(f"Required vision files are missing: {', '.join(missing_templates)}")

    try:
        dpi = int(core._user32.GetDpiForWindow(core.GAME_HWND))
    except Exception:
        dpi = 0
    foreground = core._user32.GetForegroundWindow() == core.GAME_HWND

    menu_scores = {"training": None, "game": None}
    try:
        assets = core._senzu_assets()
        with core.mss.MSS() as sct:
            _visible, training_score = core.detect_training_menu(
                sct, core._confirmed_game_capture_rect(), assets["training"]
            )
            _visible, game_score, _location = core._template_in_reference_box(
                sct, assets["game_menu"], (0, 340, 220, 80), threshold=0.82
            )
        menu_scores = {
            "training": round(float(training_score), 3),
            "game": round(float(game_score), 3),
        }
    except Exception as error:
        issues.append(f"Live template probe failed: {type(error).__name__}: {error}")

    return {
        "ok": True,
        "summary": f"Roblox {width}x{height} at ({left}, {top}), {mode}",
        "client": {"left": left, "top": top, "width": width, "height": height},
        "monitor": monitor,
        "window_mode": mode,
        "dpi": dpi,
        "foreground": foreground,
        "minimized": bool(core._user32.IsIconic(core.GAME_HWND)),
        "template_scores": menu_scores,
        "settings": {
            "training_order": training_order,
            "gravity_target_g": core.GC_GRAVITY_TARGET_G,
            "health_mode": core.HEALTH_MODE,
            "agility_mode": core.AGILITY_MODE,
            "ki_mode": core.KI_V8_MODE,
            "senzu_enabled": core.SENZU_ENABLED,
        },
        "calibration": {
            "trait_buttons": {name: list(point) for name, point in core.BUTTONS.items()},
            "health_box": dict(core.HEALTH_BOX),
            "agility_box": dict(core.AGILITY_BOX),
        },
        "capture_note": (
            "Coordinates use the Roblox client area, so a visible taskbar does not shift scan boxes. "
            "Minimizing or covering Roblox can still break capture."
        ),
        "issues": issues,
    }


def _overlay_colour(bgr):
    """OpenCV BGR tuple -> the '#rrggbb' the HUD draws with."""
    blue, green, red = bgr
    return f"#{red:02x}{green:02x}{blue:02x}"


def _scan_region_payload():
    """The scan regions in canonical 1920x1080 client coordinates, for the debug HUD.

    Canonical rather than screen pixels on purpose: the HUD sits over the client and
    applies the same width/1920, height/1080 scaling the scanner does, so whatever
    skew a non-16:9 client causes is visible instead of corrected away in transit.
    """
    import xynmacro_core as core

    return [
        {
            "name": name,
            "left": box["left"],
            "top": box["top"],
            "width": box["width"],
            "height": box["height"],
            "colour": core._overlay_colour(colour),
        }
        for name, box, colour in core._diagnostic_regions()
    ]


def _diagnostic_regions():
    """The scan regions to draw, as ``(label, box, bgr_colour)``.

    Single source for both the diagnostics preview image and the on-screen overlay so
    the two can never disagree about where a region is. Read live because calibration
    reassigns these globals.
    """
    import xynmacro_core as core

    if core._new_game_ui():
        health, wasd = core.training_ui.HEALTH_BAR_BOX, core.training_ui.AGILITY_LETTER_BOX
        progression = core.training_ui.progression_label_box(core.CURRENT_TRAINING_STATE)
    else:
        health, wasd, progression = core.HEALTH_BOX, core.AGILITY_BOX, core.PROG_SEARCH_BOX
    regions = [
        ("Training menu", core.TRAINING_MENU_BOX, (255, 190, 40)),
        ("Health", health, (50, 220, 80)),
        ("WASD", wasd, (50, 180, 255)),
        ("HP", core.SENZU_HP_FILL_BOX, (50, 50, 255)),
        ("Gravity", core.GRAVITY_LABEL_BOX, (230, 80, 230)),
        ("Progression", progression, (255, 255, 120)),
    ]
    return [(name, _box_dict(box), colour) for name, box, colour in regions]


def _box_dict(box):
    """The October 2026 layout keeps its boxes as (left, top, width, height) tuples."""
    if isinstance(box, dict):
        return box
    left, top, width, height = box
    return {"left": left, "top": top, "width": width, "height": height}


IDENTITY_BOXES = (
    (0, 80, 175, 160),    # portrait
    (55, 92, 260, 36),    # player name above the bars
)


def _diagnostic_overlay_frame(sct, geometry):
    """Return a labelled, scaled view of the exact client-relative scan areas."""
    import xynmacro_core as core

    raw = core.np.array(sct.grab(geometry))[:, :, :3]
    frame = raw.copy()
    scale_x = frame.shape[1] / core.GAME_REFERENCE_WIDTH
    scale_y = frame.shape[0] / core.GAME_REFERENCE_HEIGHT
    # The October 2026 HUD shows the player's portrait and name top-left;
    # this preview can be shared, so black them out.
    for x, y, w, h in IDENTITY_BOXES:
        frame[int(y * scale_y):int((y + h) * scale_y), int(x * scale_x):int((x + w) * scale_x)] = 0

    def draw_box(name, box, color):
        x = int(box["left"] * scale_x)
        y = int(box["top"] * scale_y)
        right = int((box["left"] + box["width"]) * scale_x)
        bottom = int((box["top"] + box["height"]) * scale_y)
        core.cv2.rectangle(frame, (x, y), (right, bottom), color, 2)
        core.cv2.putText(
            frame, name, (x + 4, max(18, y + 18)), core.cv2.FONT_HERSHEY_SIMPLEX,
            0.55, color, 2, core.cv2.LINE_AA,
        )

    for name, box, color in core._diagnostic_regions():
        draw_box(name, box, color)
    core.cv2.putText(
        frame, "Ki: scans full Roblox client", (20, frame.shape[0] - 22),
        core.cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 230, 255), 2, core.cv2.LINE_AA,
    )
    max_width = 960
    if frame.shape[1] > max_width:
        target_height = max(1, int(frame.shape[0] * max_width / frame.shape[1]))
        frame = core.cv2.resize(frame, (max_width, target_height), interpolation=core.cv2.INTER_AREA)
    return frame
