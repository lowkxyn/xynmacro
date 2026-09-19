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
        payload = notify._payload("error", 65, "@everyone PRIVATE")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(set(payload), {"content", "allowed_mentions"})
        self.assertNotIn("PRIVATE", payload["content"])
        self.assertIn("1m 5s", payload["content"])

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
            send.assert_called_once_with("progress", 1100, "Health")

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
        self.assertIn("0m 0s", notify._payload("completed", None)["content"])

    def test_progress_never_waits_for_a_settings_save(self):
        busy_lock = Mock()
        busy_lock.acquire.return_value = False
        with patch.object(notify, "_lock", busy_lock), patch.object(notify, "send") as send:
            notify.progress(600, "Health")
        busy_lock.acquire.assert_called_once_with(blocking=False)
        busy_lock.release.assert_not_called()
        send.assert_not_called()
