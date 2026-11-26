#!/usr/bin/env python3
"""Unit + smoke tests for JARVIS voice control (no microphone required)."""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / "system_files" / "overrides" / "usr" / "lib" / "jarvis"
sys.path.insert(0, str(LIB / "voice"))
sys.path.insert(0, str(LIB / "assist"))

import actions  # noqa: E402
import commands  # noqa: E402
import config  # noqa: E402
import server  # noqa: E402


class ParserTests(unittest.TestCase):
    def test_wake_word_is_stripped(self):
        self.assertEqual(commands.strip_wake("Hey JARVIS, volume up"), "volume up")
        self.assertEqual(commands.strip_wake("jarvis open steam"), "open steam")

    def test_local_commands(self):
        cases = {
            "jarvis volume up": ("volume", {"direction": "up"}),
            "jarvis set volume to 35": ("volume", {"percent": 35, "direction": "set"}),
            "jarvis open steam": ("open_app", {"app": "steam"}),
            "jarvis take a screenshot": ("screenshot", {}),
            "jarvis set a timer for 5 minutes": ("timer", {"seconds": 300}),
            "jarvis install spotify": ("install_app", {"app": "com.spotify.Client"}),
            "jarvis reboot the computer": ("power", {"action": "reboot"}),
        }
        for text, (action, args) in cases.items():
            with self.subTest(text=text):
                parsed = commands.parse(text)
                self.assertEqual(parsed.action, action)
                self.assertEqual(parsed.args, args)

    def test_shell_command_punctuation_survives(self):
        parsed = commands.parse("jarvis run echo hi=1 && date")
        self.assertEqual(parsed.action, "command")
        self.assertEqual(parsed.args["command"], "echo hi=1 && date")

    def test_fallback_to_chat(self):
        self.assertIsNone(commands.parse("jarvis why is my controller laggy").action)

    def test_time_answer_is_local(self):
        self.assertTrue(commands.parse("jarvis what time is it").spoken.startswith("It is"))


class ActionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.manager = actions.ActionManager(audit_file=pathlib.Path(tmp.name) / "audit.jsonl")

    def test_unknown_action(self):
        result = self.manager.prepare("no_such_action")
        self.assertEqual(result["status"], "error")

    def test_app_allowlist(self):
        result = self.manager.prepare("open_app", {"app": "/bin/sh"})
        self.assertEqual(result["status"], "error")

    def test_safe_action_runs(self):
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            result = self.manager.prepare("volume", {"direction": "up"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(run.call_args.args[0], ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"])

    def test_high_risk_needs_confirmation_and_phrase(self):
        prepared = self.manager.prepare("install_app", {"app": "com.spotify.Client"})
        self.assertEqual(prepared["status"], "confirmation_required")
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            denied = self.manager.confirm(prepared["id"], "no")  # explicit denial
        self.assertEqual(denied["status"], "denied")
        prepared = self.manager.prepare("install_app", {"app": "com.spotify.Client"})
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            confirmed = self.manager.confirm(prepared["id"], "confirm")
        self.assertEqual(confirmed["status"], "ok")
        self.assertEqual(run.call_args.args[0][:4], ["flatpak", "install", "-y", "--noninteractive"])

    def test_free_form_command_challenge(self):
        prepared = self.manager.prepare("command", {"command": "echo hi"})
        self.assertEqual(prepared["challenge"], "confirm command")
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="hi", stderr="")
            self.assertEqual(self.manager.confirm(prepared["id"], "confirm")["status"], "denied")
        prepared = self.manager.prepare("command", {"command": "echo hi"})
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="hi", stderr="")
            self.assertEqual(
                self.manager.confirm(prepared["id"], "confirm command")["status"], "ok"
            )
            self.assertEqual(run.call_args.args[0], ["bash", "-lc", "echo hi"])

    def test_audit_log_written(self):
        self.manager.prepare("power", {"action": "reboot"})
        lines = self.manager.audit_file.read_text().splitlines()
        self.assertTrue(lines)
        self.assertEqual(json.loads(lines[-1])["action"], "power")


class VoiceDispatchTests(unittest.TestCase):
    def test_spoken_confirmation_resolves_pending_action(self):
        manager = mock.Mock()
        manager.pending = {"abc123": object()}
        manager.confirm.return_value = {"status": "ok", "message": "Done."}
        with mock.patch.object(server, "ACTION_MANAGER", manager), \
             mock.patch.object(server, "publish"), \
             mock.patch.object(server, "speak_async"):
            server.handle_voice_utterance("confirm command")
        manager.confirm.assert_called_once_with("abc123", "confirm command", source="voice")

    def test_spoken_denial_resolves_pending_action(self):
        manager = mock.Mock()
        manager.pending = {"abc123": object()}
        manager.deny.return_value = {"status": "denied", "message": "Cancelled."}
        with mock.patch.object(server, "ACTION_MANAGER", manager), \
             mock.patch.object(server, "publish"), \
             mock.patch.object(server, "speak_async"):
            server.handle_voice_utterance("cancel")
        manager.deny.assert_called_once_with("abc123", source="voice")


class BackendSmokeTests(unittest.TestCase):
    def test_http_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.update({
                "JARVIS_VOICE_CONFIG": str(pathlib.Path(tmp) / "config.json"),
                "JARVIS_ASSIST_PORT": "0",
                "JARVIS_VOICE_AUTOSTART": "0",
            })
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            server = subprocess.Popen(
                [sys.executable, str(LIB / "assist" / "server.py"), "--port", str(port)],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.addCleanup(lambda: (server.terminate(), server.wait(timeout=5)))
            base = f"http://127.0.0.1:{port}"
            for _ in range(40):
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                        self.assertEqual(r.status, 200)
                    break
                except Exception:
                    time.sleep(0.15)
            else:
                self.fail("backend did not start")

            def get(path):
                with urllib.request.urlopen(base + path, timeout=5) as r:
                    return json.loads(r.read())

            actions_data = get("/api/actions")
            self.assertTrue(actions_data["available"])
            self.assertIn("open_app", [a["name"] for a in actions_data["actions"]])
            status = get("/api/voice/status")
            self.assertIn("config", status)
            self.assertIn("models", status)

            req = urllib.request.Request(
                base + "/api/voice/config",
                data=json.dumps({"wake_enabled": False}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertFalse(json.loads(r.read())["wake_enabled"])
            self.assertEqual(json.loads((pathlib.Path(tmp) / "config.json").read_text())["wake_enabled"], False)


if __name__ == "__main__":
    unittest.main()
