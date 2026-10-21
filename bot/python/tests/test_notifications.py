import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notifications as notify


# Deliberately synthetic, never submitted to Discord.
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "x" * 60


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        notify._path = Path(self.directory.name) / "notifications.dpapi"
        notify._settings = {"url": "", "enabled": False, "progress_minutes": 0}
        notify._generation = 0
        notify._worker = None
        notify._messages = queue.Queue(maxsize=4)
        notify._backoff_until = 0
        notify._last_status = "Not configured"
        self.thread = patch.object(notify.threading, "Thread").start()
        self.thread.return_value.is_alive.return_value = True
        self.addCleanup(patch.stopall)

    def tearDown(self):
        notify._settings = {"url": "", "enabled": False, "progress_minutes": 0}
        notify._path = None
        notify._worker = None
        notify._messages = queue.Queue(maxsize=4)

    def test_encrypted_round_trip_and_secret_free_snapshot(self):
        notify.configure({"url": WEBHOOK, "enabled": True, "progress_minutes": 5})
        self.assertNotIn(WEBHOOK.encode(), notify._path.read_bytes())
        self.assertNotIn(WEBHOOK, json.dumps(notify.snapshot()))
        notify._settings["url"] = ""
        notify.load(notify._path)
        self.assertEqual(notify._settings["url"], WEBHOOK)
        self.assertTrue(notify.snapshot()["enabled"])

    def test_rejects_arbitrary_hosts_redirect_paths_and_bad_types_without_leaking(self):
        urls = [WEBHOOK.replace("discord.com", "discord.com.evil.test"), WEBHOOK.replace("https:", "http:"),
                WEBHOOK + "?redirect=secret", WEBHOOK + "#secret", WEBHOOK.replace("discord.com", "127.0.0.1"),
                WEBHOOK.replace("discord.com", "name:password@discord.com"), None, 123]
        for url in urls:
            with self.subTest(url_type=type(url).__name__):
                with self.assertRaises(ValueError) as caught:
                    notify.configure({"url": url})
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("password", str(caught.exception))
        for value in [{"enabled": "false"}, {"enabled": True}, {"progress_minutes": -1},
                      {"progress_minutes": float("nan")}, {"progress_minutes": True}, []]:
            with self.assertRaises(ValueError):
                notify.configure(value)

    def test_ptb_canary_and_legacy_hosts_are_stored_as_discord_com(self):
        for host in ("ptb.discord.com", "canary.discord.com", "discordapp.com"):
            with self.subTest(host=host):
                notify.configure({"url": WEBHOOK.replace("discord.com", host)})
                self.assertEqual(notify._settings["url"], WEBHOOK)
        with self.assertRaises(ValueError):
            notify.configure({"url": WEBHOOK.replace("discord.com", "evil.discord.com")})

    def test_interval_change_keeps_queued_final_message(self):
        notify.configure({"url": WEBHOOK, "enabled": True})
        notify.send("completed", 40, "Health")
        item = notify._messages.get_nowait()
        notify.configure({"progress_minutes": 10})
        with patch.object(notify, "_post", return_value=204) as post:
            notify._deliver(item)
            post.assert_called_once()

    def test_failed_save_preserves_previous_settings(self):
        notify.configure({"url": WEBHOOK, "enabled": True})
        with patch.object(notify.os, "replace", side_effect=OSError("private path")):
            with self.assertRaisesRegex(ValueError, "Could not save notification settings"):
                notify.configure({"enabled": False})
        self.assertTrue(notify.snapshot()["enabled"])

    def test_clear_or_disable_discards_queued_messages(self):
        for clear in (True, False):
            notify.configure({"url": WEBHOOK, "enabled": True})
            notify.send("completed", 40, "Health")
            item = notify._messages.get_nowait()
            if clear:
                notify.clear()
            else:
                notify.configure({"enabled": False})
            with patch.object(notify, "_post") as post:
                notify._deliver(item)
                post.assert_not_called()

    def test_message_uses_only_allowlisted_fields_and_no_mentions(self):
        payload = notify._payload("error", 65, "@everyone PRIVATE",
                                  {"stats": {"PRIVATE": (1, 20, 3)}, "telemetry": {"PRIVATE": 9}})
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(set(payload), {"embeds", "allowed_mentions"})
        text = json.dumps(payload)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("everyone", text)
        self.assertIn("1m 05s", text)

    def test_no_redirects_and_delivery_errors_never_expose_url(self):
        notify.configure({"url": WEBHOOK, "enabled": True})
        notify.send("completed")
        item = notify._messages.get_nowait()
        with patch.object(notify, "_post", side_effect=OSError(WEBHOOK)):
            notify._deliver(item)
        self.assertNotIn(WEBHOOK, notify.snapshot()["status"])
        connection = Mock()
        connection.getresponse.return_value.status = 302
        with patch.object(notify.http.client, "HTTPSConnection", return_value=connection) as connect:
            self.assertEqual(notify._post(WEBHOOK, notify._payload("test")), 302)
        connect.assert_called_once_with("discord.com", timeout=5)
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_disabled_final_messages_explicit_test_queue_bound_and_rate_limit(self):
        notify.configure({"url": WEBHOOK})
        self.assertFalse(notify.send("completed"))
        for _ in range(4):
            self.assertTrue(notify.send("test"))
        self.assertFalse(notify.send("test"))
        with patch.object(notify, "_post", return_value=429):
            notify._deliver(notify._messages.get_nowait())
        self.assertFalse(notify.send("test"))
        self.assertIn("rate limit", notify.snapshot()["status"])

    def test_progress_is_spaced_and_no_catchup_burst_after_pause(self):
        notify.configure({"url": WEBHOOK, "enabled": True, "progress_minutes": 5})
        with patch.object(notify.time, "monotonic", return_value=100):
            notify.begin_run()
        with patch.object(notify.time, "monotonic", return_value=399), patch.object(notify, "send") as send:
            notify.progress(299, "Health")
            send.assert_not_called()
        with patch.object(notify.time, "monotonic", return_value=1200), patch.object(notify, "send") as send:
            notify.progress(1100, "Health")
            notify.progress(1100, "Health")
            send.assert_called_once_with("progress", 1100, "Health", None, None)

    def test_failed_removal_disables_in_memory_and_invalidates_queued_work(self):
        notify.configure({"url": WEBHOOK, "enabled": True})
        notify.send("completed")
        item = notify._messages.get_nowait()
        with patch.object(Path, "unlink", side_effect=OSError("locked")):
            with self.assertRaisesRegex(ValueError, "retry Remove before restarting"):
                notify.clear()
        self.assertFalse(notify.snapshot()["enabled"])
        self.assertTrue(notify.snapshot()["configured"])
        with patch.object(notify, "_post") as post:
            notify._deliver(item)
            post.assert_not_called()

    def test_old_inflight_rate_limit_does_not_block_replacement_webhook(self):
        notify.configure({"url": WEBHOOK, "enabled": True})
        notify.send("completed")
        item = notify._messages.get_nowait()
        def replace_during_request(*args):
            notify.configure({"url": WEBHOOK.replace("x" * 60, "y" * 60)})
            return 429
        with patch.object(notify, "_post", side_effect=replace_during_request):
            notify._deliver(item)
        self.assertEqual(notify._backoff_until, 0)
        self.assertTrue(notify.send("test"))

    def test_invalid_elapsed_never_breaks_final_notification(self):
        self.assertIn("0m 00s", json.dumps(notify._payload("completed", None)))

    def test_progress_never_waits_for_a_settings_save(self):
        busy_lock = Mock()
        busy_lock.acquire.return_value = False
        with patch.object(notify, "_lock", busy_lock), patch.object(notify, "send") as send:
            notify.progress(600, "Health")
        busy_lock.acquire.assert_called_once_with(blocking=False)
        busy_lock.release.assert_not_called()
        send.assert_not_called()

    def test_embed_shows_stats_activity_and_long_runs_in_hours(self):
        summary = {"stats": {"Health": (10, 20, 0), "Ki Control": (3, 20, 5), "Agility": (1, 20, "x")},
                   "telemetry": {"ki_clicks": 151, "health_hits": 0, "switches": 4}}
        embed = notify._payload("progress", 6003, "Ki Control", summary)["embeds"][0]
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertEqual(fields["Elapsed"], "1h 40m 03s")
        self.assertEqual(fields["Current stat"], "Ki Control")
        stats = fields["Stats (last menu check)"].split("\n")
        self.assertEqual(len(stats), 2)          # the malformed Agility reading is dropped
        self.assertIn("Health", stats[0])
        self.assertIn("\u2713", stats[0])       # 10/20 with 0 Points Left is done
        self.assertNotIn("\u2713", stats[1])
        self.assertEqual(fields["Activity"], "Ki clicks 151 \u00b7 Stat switches 4")

    def test_picture_is_attached_only_when_turned_on(self):
        notify.configure({"url": WEBHOOK, "enabled": True})
        self.assertTrue(notify.send("completed", 5, "Health", None, b"PNG"))
        _generation, payload, image = notify._messages.get_nowait()
        self.assertIsNone(image)
        self.assertNotIn("image", payload["embeds"][0])
        notify.configure({"screenshots": True})
        self.assertTrue(notify.snapshot()["screenshots"])
        self.assertTrue(notify.send("completed", 5, "Health", None, b"PNG"))
        _generation, payload, image = notify._messages.get_nowait()
        self.assertEqual(image, b"PNG")
        self.assertEqual(payload["embeds"][0]["image"], {"url": "attachment://progress.png"})
        self.assertTrue(notify.send("completed", 5, "Health", None, b"x" * (3 * 1024 * 1024)))
        self.assertIsNone(notify._messages.get_nowait()[2])

    def test_picture_setting_must_be_a_bool(self):
        with self.assertRaises(ValueError):
            notify.configure({"screenshots": "yes"})

    def test_multipart_body_carries_the_json_and_the_png(self):
        body, content_type = notify._multipart({"embeds": []}, b"\x89PNGDATA")
        boundary = content_type.split("boundary=")[1]
        self.assertTrue(body.startswith(f"--{boundary}".encode()))
        self.assertIn(b'name="payload_json"', body)
        self.assertIn(b'filename="progress.png"', body)
        self.assertIn(b"\x89PNGDATA", body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))
