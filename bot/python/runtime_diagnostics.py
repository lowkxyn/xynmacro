"""Log, host profile, and process status helpers."""

import os
import re
import sys


def _read_log_tail(path, lines=60):
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            position = fh.tell()
            chunks = []
            newlines = 0
            while position > 0 and newlines <= lines:
                size = min(8192, position)
                position -= size
                fh.seek(position)
                chunk = fh.read(size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
            return b"\n".join(b"".join(reversed(chunks)).splitlines()[-lines:]).decode(
                "utf-8", errors="replace"
            ).strip()
    except Exception:
        return ""


# The folder after Users\ (or /home/) is usually the owner's name. It may contain
# spaces, so it runs to the next separator or quote rather than the next word.
_PROFILE_FOLDER = re.compile(
    r"((?:\b[a-z]:[\\/]+|(?<![\w.])/)(?:users|documents and settings|home)[\\/]+)"
    r"[^\\/:*?\"<>|\r\n,;)\]]+",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.IGNORECASE)
_SECRET_PATTERNS = (
    (re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\S+",
                re.IGNORECASE), "<webhook removed>"),
    # user:password@ inside a URL.
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
     r"\1<credentials removed>@"),
    (re.compile(r"([?&](?:access_token|token|key|api_key|apikey|secret|password|sig|signature|code)=)[^&\s]+",
                re.IGNORECASE), r"\1<removed>"),
    (re.compile(r"\b(bearer|basic)\s+[a-z0-9._~+/=-]{12,}", re.IGNORECASE), r"\1 <removed>"),
    (re.compile(r"\b(?:gh[pousr]_[a-z0-9]{30,}|github_pat_\w{40,}|xox[abprs]-[\w-]{10,}"
                r"|AKIA[0-9A-Z]{16}|sk-[\w-]{20,}"
                r"|eyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,})", re.IGNORECASE), "<token removed>"),
    # key=value, key: value and quoted JSON/dict pairs ("api_key": "..."), including
    # prefixed names such as access_token or client-secret. The quotes stay, so the
    # surrounding structure still reads as JSON.
    (re.compile(r"\b((?:[\w-]*[_-])?(?:password|passwd|pwd|secret|token|api[_-]?key|apikey"
                r"|private[_-]?key|credentials?))"
                r"([\"']?\s*[=:]\s*)"
                r"(\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*'|[^\s,;\"'}\]]+)",
                re.IGNORECASE), lambda m: m.group(1) + m.group(2) + _removed(m.group(3))),
)


def _removed(value):
    quote = value[0] if value[0] in "\"'" else ""
    return f"{quote}<removed>{quote}"


def _scrub_user_paths(text):
    """Redact text before it leaves the machine (Copy, Save Log, bug reports).

    Removes profile folders, the current account name, email addresses, and
    credential-shaped URLs and tokens, keeping the rest of each line intact so it
    still diagnoses something. Names typed as free text cannot be found reliably,
    so the user still reviews what they share.
    """
    if not text:
        return text
    text = str(text)
    profile = os.environ.get("USERPROFILE") or ""
    if profile:
        text = re.sub(re.escape(profile), "%USERPROFILE%", text, flags=re.IGNORECASE)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = _PROFILE_FOLDER.sub(r"\1<user>", text)
    user = os.environ.get("USERNAME") or ""
    if user and len(user) > 2:
        text = re.sub(re.escape(user), "%USER%", text, flags=re.IGNORECASE)
    return _EMAIL.sub("<email>", text)


def _log_entry_line(entry):
    """One session-log ring entry as the viewer shows it: 'HH:MM:SS  message'."""
    import time
    return f"{time.strftime('%H:%M:%S', time.localtime(entry['t']))}  {entry['msg']}"


def _shareable_log(entries):
    """Ring entries as redacted text, the one form Copy and bug reports share."""
    return _scrub_user_paths("\n".join(_log_entry_line(entry) for entry in entries))


def _registry_value(root, path, name):
    try:
        import winreg
        with winreg.OpenKey(root, path) as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except Exception:
        return None


def _system_profile():
    """CPU / RAM / GPU / OS, read cheaply. Never raises — missing is fine."""
    import xynmacro_core as core

    import platform
    profile = {
        "os": platform.platform(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "cpu_threads": os.cpu_count(),
    }
    if os.name == "nt":
        import winreg
        profile["cpu"] = core._registry_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            "ProcessorNameString",
        )
        # First display adapter. WMI would be more thorough but costs ~1s.
        profile["gpu"] = core._registry_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Class"
            r"\{4d36e968-e325-11ce-bfc1-08002be10318}\0000",
            "DriverDesc",
        )
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                profile["ram_gb"] = round(status.ullTotalPhys / (1024 ** 3), 1)
        except Exception:
            pass
    return profile


def _pid_alive(pid):
    """True if a process with this PID is still running (Windows: not a zombie)."""
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        # Also check exit code — handle may be valid for a zombie process
        exit_code = ctypes.c_ulong(0)
        STILL_ACTIVE = 259
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return not (ok and exit_code.value != STILL_ACTIVE)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
