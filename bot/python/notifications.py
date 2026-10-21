"""Optional Discord status messages. The webhook never enters config snapshots or logs."""
import http.client
import json
import math
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import win32crypt
import pywintypes

_lock = threading.RLock()
_path = None
_settings = {"url": "", "enabled": False, "progress_minutes": 0, "screenshots": False}
_generation = 0
_messages = queue.Queue(maxsize=4)
_worker = None
_last_status = "Not configured"
_next_progress = 0.0
_backoff_until = 0.0
# PTB, Canary and the legacy discordapp.com host copy links that work on discord.com.
_WEBHOOK = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com(/api/webhooks/[0-9]{5,24}/[A-Za-z0-9_-]{20,200})\Z"
)


def _validate(value, previous):
    if not isinstance(value, dict):
        raise ValueError("Invalid notification settings.")
    settings = dict(previous)
    if "url" in value:
        url = value["url"]
        if not isinstance(url, str):
            raise ValueError("Use a Discord webhook URL from discord.com.")
        url = url.strip()
        if url:
            match = _WEBHOOK.fullmatch(url)
            if not match:
                raise ValueError("Use a Discord webhook URL from discord.com.")
            # _post only ever connects to discord.com, so store that form.
            url = "https://discord.com" + match.group(1)
        settings["url"] = url
    if "enabled" in value:
        if type(value["enabled"]) is not bool:
            raise ValueError("Notifications must be enabled or disabled.")
        settings["enabled"] = value["enabled"]
    if "progress_minutes" in value:
        minutes = value["progress_minutes"]
        if type(minutes) is not int or minutes not in (0, 5, 10, 15, 30, 60):
            raise ValueError("Choose one of the listed progress intervals.")
        settings["progress_minutes"] = minutes
    if "screenshots" in value:
        if type(value["screenshots"]) is not bool:
            raise ValueError("The progress picture must be on or off.")
        settings["screenshots"] = value["screenshots"]
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
                "progress_minutes": _settings["progress_minutes"],
                "screenshots": bool(_settings.get("screenshots")), "status": _last_status}


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
        # Only a new webhook or disabling should drop queued messages; changing
        # the progress interval mid-run must not lose "Training finished".
        if updated["url"] != _settings["url"] or not updated["enabled"]:
            _generation += 1
        _settings = updated
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
        _settings = {"url": "", "enabled": False, "progress_minutes": 0, "screenshots": False}
        _last_status = "Not configured"
    return snapshot()


STATS = ("Health", "Agility", "Ki Control", "Physical Damage", "Ki Damage")
_TITLES = {"completed": "Training finished", "stopped": "Training stopped",
           "error": "Training needs attention", "progress": "Training progress", "test": "Test notification"}
_COLOURS = {"completed": 0x3BA55D, "stopped": 0x99AAB5, "error": 0xED4245,
            "progress": 0x5865F2, "test": 0xB18CFF}
_ACTIVITY = (("ki_clicks", "Ki clicks"), ("health_hits", "Health hits"),
             ("senzu_eaten", "Senzu eaten"), ("switches", "Stat switches"))
IMAGE_NAME = "progress.png"
_IMAGE_MAX_BYTES = 2 * 1024 * 1024


def _count(value, limit=1_000_000):
    return value if type(value) is int and 0 <= value <= limit else None


def _elapsed_text(elapsed):
    try:
        seconds = float(elapsed)
    except (TypeError, ValueError):
        seconds = 0.0
    seconds = int(max(0, min(seconds, 86400 * 365)) if math.isfinite(seconds) else 0)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s" if hours else f"{minutes}m {seconds:02d}s"


def _stat_lines(stats):
    lines = []
    for stat in STATS:
        reading = stats.get(stat) if isinstance(stats, dict) else None
        if not isinstance(reading, (tuple, list)) or len(reading) != 3:
            continue
        level, target, points_left = (_count(value, 999) for value in reading)
        if None in (level, target, points_left) or not target:
            continue
        done = " \u2713" if level >= min(target, level + points_left) else ""
        lines.append(f"**{stat}** \u00b7 {level}/{target} \u00b7 {points_left} Points Left{done}")
    return lines


def _payload(kind, elapsed=0, category=None, summary=None, image=False):
    title = _TITLES.get(kind)
    if title is None:
        raise ValueError("Unknown notification kind")
    # Only known structured values leave the app; never raw reasons, logs or screen
    # content other than the optional Training Progression picture.
    embed = {"title": title, "color": _COLOURS[kind],
             "footer": {"text": "XynMacro"},
             "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if kind == "test":
        embed["description"] = "Your webhook works. Run updates will arrive here."
    else:
        fields = [{"name": "Elapsed", "value": _elapsed_text(elapsed), "inline": True}]
        if category in STATS:
            fields.append({"name": "Current stat", "value": category, "inline": True})
        summary = summary if isinstance(summary, dict) else {}
        lines = _stat_lines(summary.get("stats"))
        if lines:
            fields.append({"name": "Stats (last menu check)", "value": "\n".join(lines), "inline": False})
        telemetry = summary.get("telemetry") if isinstance(summary.get("telemetry"), dict) else {}
        activity = [f"{label} {_count(telemetry.get(key))}" for key, label in _ACTIVITY
                    if _count(telemetry.get(key))]
        if activity:
            fields.append({"name": "Activity", "value": " \u00b7 ".join(activity), "inline": False})
        embed["fields"] = fields
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if image:
        embed["image"] = {"url": f"attachment://{IMAGE_NAME}"}
        payload["attachments"] = [{"id": 0, "filename": IMAGE_NAME}]
    return payload


def _multipart(payload, image):
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{IMAGE_NAME}\"\r\nContent-Type: image/png\r\n\r\n")
    return head.encode() + image + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def _post(url, payload, image=None):
    # Fixed host, default TLS validation, no redirects or environment proxy credentials.
    if image:
        body, content_type = _multipart(payload, image)
    else:
        body, content_type = json.dumps(payload).encode(), "application/json"
    connection = http.client.HTTPSConnection("discord.com", timeout=10 if image else 5)
    try:
        connection.request("POST", url.removeprefix("https://discord.com") + "?wait=true",
                           body=body,
                           headers={"Content-Type": content_type, "User-Agent": "XynMacro/notifications"})
        response = connection.getresponse()
        response.read(1024)
        return response.status
    finally:
        connection.close()


def _deliver(item):
    global _last_status, _backoff_until
    generation, payload, image = item
    with _lock:
        if generation != _generation or not _settings["url"] or time.monotonic() < _backoff_until:
            return
        url = _settings["url"]
    try:
        status = _post(url, payload, image)
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


def send(kind, elapsed=0, category=None, summary=None, image=None):
    """Queue a message. image: PNG bytes of the Training Progression panel, sent
    only when the user turned the progress picture on."""
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
            if not (_settings.get("screenshots") and isinstance(image, bytes)
                    and 0 < len(image) <= _IMAGE_MAX_BYTES):
                image = None
            _messages.put_nowait(
                (_generation, _payload(kind, elapsed, category, summary, image is not None), image))
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


def progress(elapsed, category, extras=None):
    """extras: callable returning (summary, image); only called when a message goes out."""
    global _next_progress
    # UI saves can wait on disk/DPAPI. The minigame loop must never wait with them.
    if not _lock.acquire(blocking=False):
        return
    try:
        interval = _settings["progress_minutes"] * 60
        if not _settings["enabled"] or not interval or time.monotonic() < _next_progress:
            return
        _next_progress = time.monotonic() + interval
        summary, image = extras() if extras else (None, None)
        send("progress", elapsed, category, summary, image)
    finally:
        _lock.release()
