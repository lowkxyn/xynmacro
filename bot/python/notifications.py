"""Optional Discord status messages. The webhook never enters config snapshots or logs."""
import http.client
import json
import math
import os
import queue
import re
import threading
import time
from pathlib import Path

import win32crypt
import pywintypes

_lock = threading.RLock()
_path = None
_settings = {"url": "", "enabled": False, "progress_minutes": 0}
_generation = 0
_messages = queue.Queue(maxsize=4)
_worker = None
_last_status = "Not configured"
_next_progress = 0.0
_backoff_until = 0.0
_WEBHOOK = re.compile(r"https://discord\.com/api/webhooks/[0-9]{5,24}/[A-Za-z0-9_-]{20,200}\Z")


def _validate(value, previous):
    if not isinstance(value, dict):
        raise ValueError("Invalid notification settings.")
    settings = dict(previous)
    if "url" in value:
        url = value["url"]
        if not isinstance(url, str) or (url.strip() and not _WEBHOOK.fullmatch(url.strip())):
            raise ValueError("Use a Discord webhook URL from discord.com.")
        settings["url"] = url.strip()
    if "enabled" in value:
        if type(value["enabled"]) is not bool:
            raise ValueError("Notifications must be enabled or disabled.")
        settings["enabled"] = value["enabled"]
    if "progress_minutes" in value:
        minutes = value["progress_minutes"]
        if type(minutes) is not int or minutes not in (0, 5, 10, 15, 30, 60):
            raise ValueError("Choose one of the listed progress intervals.")
        settings["progress_minutes"] = minutes
    if settings["enabled"] and not settings["url"]:
        raise ValueError("Save a webhook before enabling notifications.")
    return settings


def load(path):
    global _path, _settings, _last_status
    with _lock:
        _path = Path(path)
        try:
            if _path.stat().st_size > 8192:
                raise ValueError("Invalid notification file")
            decrypted = win32crypt.CryptUnprotectData(_path.read_bytes(), None, None, None, 0)[1]
            _settings = _validate(json.loads(decrypted), _settings)
            _last_status = "Ready" if _settings["url"] else "Not configured"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, pywintypes.error):
            _last_status = "Saved webhook could not be read. Save it again."


def snapshot():
    with _lock:
        return {"configured": bool(_settings["url"]), "enabled": _settings["enabled"],
                "progress_minutes": _settings["progress_minutes"], "status": _last_status}


def configure(value):
    global _settings, _generation, _last_status, _next_progress, _backoff_until
    with _lock:
        updated = _validate(value, _settings)
        if _path is None:
            raise ValueError("Notification storage is not ready.")
        try:
            protected = win32crypt.CryptProtectData(json.dumps(updated).encode(), "XynMacro notifications", None, None, None, 0)
            _path.parent.mkdir(parents=True, exist_ok=True)
            temporary = _path.with_suffix(".tmp")
            temporary.write_bytes(protected)
            os.replace(temporary, _path)
        except (OSError, pywintypes.error):
            raise ValueError("Could not save notification settings.") from None
        if updated["url"] != _settings["url"]:
            _backoff_until = 0.0
        _settings = updated
        _generation += 1
        _next_progress = time.monotonic() + updated["progress_minutes"] * 60
        _last_status = "Ready" if updated["enabled"] else ("Notifications disabled" if updated["url"] else "Not configured")
    return snapshot()


def clear():
    global _settings, _generation, _last_status, _backoff_until
    with _lock:
        # A failed removal must still stop automatic messages in this process.
        # Keep the configured flag on failure so Remove remains available to retry.
        _settings["enabled"] = False
        _generation += 1
        _backoff_until = 0.0
        if _path is not None:
            try:
                _path.unlink(missing_ok=True)
                _path.with_suffix(".tmp").unlink(missing_ok=True)
            except OSError:
                _last_status = "Disabled for this session. Could not remove saved webhook; retry Remove before restarting."
                raise ValueError(_last_status) from None
        _settings = {"url": "", "enabled": False, "progress_minutes": 0}
        _last_status = "Not configured"
    return snapshot()


def _payload(kind, elapsed=0, category=None):
    titles = {"completed": "Training finished", "stopped": "Training stopped",
              "error": "Training needs attention", "progress": "Training progress", "test": "Test notification"}
    title = titles.get(kind)
    if title is None:
        raise ValueError("Unknown notification kind")
    # Only known structured values leave the app; never raw reasons, logs or screen content.
    try:
        seconds = float(elapsed)
    except (TypeError, ValueError):
        seconds = 0.0
    seconds = max(0, min(seconds, 86400 * 365)) if math.isfinite(seconds) else 0
    content = f"XynMacro · {title}"
    if kind != "test":
        content += f"\nElapsed: {int(seconds) // 60}m {int(seconds) % 60}s"
        if category in ("Health", "Agility", "Physical Damage", "Ki Control", "Ki Damage"):
            content += f"\nTrait: {category}"
    return {"content": content, "allowed_mentions": {"parse": []}}


def _post(url, payload):
    # Fixed host, default TLS validation, no redirects or environment proxy credentials.
    connection = http.client.HTTPSConnection("discord.com", timeout=5)
    try:
        connection.request("POST", url.removeprefix("https://discord.com") + "?wait=true",
                           body=json.dumps(payload).encode(),
                           headers={"Content-Type": "application/json", "User-Agent": "XynMacro/notifications"})
        response = connection.getresponse()
        response.read(1024)
        return response.status
    finally:
        connection.close()


def _deliver(item):
    global _last_status, _backoff_until
    generation, payload = item
    with _lock:
        if generation != _generation or not _settings["url"] or time.monotonic() < _backoff_until:
            return
        url = _settings["url"]
    try:
        status = _post(url, payload)
        message = "Message delivered" if status in (200, 204) else f"Delivery failed (HTTP {status}); no retry"
    except (OSError, http.client.HTTPException):
        status = 0
        message = "Delivery failed; check your connection and webhook. No retry."
    with _lock:
        if generation == _generation:
            _last_status = message
            if status == 429:
                _backoff_until = time.monotonic() + 300


def _work():
    while True:
        item = _messages.get()
        try:
            _deliver(item)
        finally:
            _messages.task_done()


def send(kind, elapsed=0, category=None):
    global _worker, _last_status
    if kind not in ("completed", "stopped", "error", "progress", "test"):
        return False
    with _lock:
        if not _settings["url"] or (kind != "test" and not _settings["enabled"]):
            return False
        if time.monotonic() < _backoff_until:
            _last_status = "Discord rate limit: notifications paused for up to 5 minutes"
            return False
        try:
            _messages.put_nowait((_generation, _payload(kind, elapsed, category)))
        except queue.Full:
            _last_status = "Notification queue full; message skipped"
            return False
        _last_status = "Queued"
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True, name="discord-notifications")
            _worker.start()
        return True


def begin_run():
    global _next_progress
    with _lock:
        _next_progress = time.monotonic() + _settings["progress_minutes"] * 60


def progress(elapsed, category):
    global _next_progress
    # UI saves can wait on disk/DPAPI. The minigame loop must never wait with them.
    if not _lock.acquire(blocking=False):
        return
    try:
        interval = _settings["progress_minutes"] * 60
        if not _settings["enabled"] or not interval or time.monotonic() < _next_progress:
            return
        _next_progress = time.monotonic() + interval
        send("progress", elapsed, category)
    finally:
        _lock.release()
