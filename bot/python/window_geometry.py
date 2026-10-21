"""Window process and monitor metadata helpers."""

import os
import win32api
import win32con


def _window_process_executable(hwnd):
    """Return the executable basename that owns hwnd, or None."""
    if os.name != "nt" or not hwnd:
        return None
    import ctypes

    process_id = ctypes.c_ulong(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not process_id.value:
        return None
    process_handle = ctypes.windll.kernel32.OpenProcess(
        0x1000, False, process_id.value  # PROCESS_QUERY_LIMITED_INFORMATION
    )
    if not process_handle:
        return None
    try:
        path_buffer = ctypes.create_unicode_buffer(32768)
        path_size = ctypes.c_ulong(len(path_buffer))
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            process_handle, 0, path_buffer, ctypes.byref(path_size)
        ):
            return None
        return os.path.basename(path_buffer.value).lower()
    finally:
        ctypes.windll.kernel32.CloseHandle(process_handle)


def _monitor_info_for_window(hwnd):
    """Return normalized monitor metadata for hwnd, or None.

    MonitorFromWindow chooses the display containing the largest part of the
    window, which is the behavior users expect when dragging Roblox between
    displays. A device name is required so display-mode changes never silently
    fall back to the primary monitor.
    """
    if os.name != "nt" or not hwnd:
        return None
    try:
        monitor_handle = win32api.MonitorFromWindow(
            int(hwnd), win32con.MONITOR_DEFAULTTONEAREST
        )
        raw = win32api.GetMonitorInfo(monitor_handle)
        left, top, right, bottom = [int(value) for value in raw["Monitor"]]
        device = str(raw.get("Device") or "").strip()
        if not device or right <= left or bottom <= top:
            return None
        # The work area excludes the taskbar. Windowed Mode centres inside it so the
        # game never sits under the taskbar, where the macro's clicks would hit it.
        work = raw.get("Work") or raw["Monitor"]
        work_left, work_top, work_right, work_bottom = [int(value) for value in work]
        if work_right <= work_left or work_bottom <= work_top:
            work_left, work_top, work_right, work_bottom = left, top, right, bottom
        return {
            "device": device,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
            "work_left": work_left,
            "work_top": work_top,
            "work_width": work_right - work_left,
            "work_height": work_bottom - work_top,
            "primary": bool(int(raw.get("Flags", 0)) & 1),
        }
    except Exception as e:
        print(f"[window] monitor lookup failed: {e}")
        return None
