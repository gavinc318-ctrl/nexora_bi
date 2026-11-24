"""Fake delivery gateway: run as python3 -m unittest test_fake_gateway (in this directory)."""

import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import server

RECIPIENTS = {
    "channels": {"page": ["email", "sms"], "notify": ["email"], "digest": ["email"]},
    "night": {"start": "22:00", "end": "07:00", "timezone": "Asia/Riyadh"},
    "recipients": [
        {"name": "ops", "email": "ops@example.invalid", "sms": "+966500000001", "levels": ["page", "notify", "digest"],
         "sms_at_night": True},
        {"name": "lead", "email": "lead@example.invalid", "sms": "+966500000002", "levels": ["page"]},
    ],
}


def am_payload(*alerts):
    return {"version": "4", "receiver": "page", "status": "firing", "alerts": list(alerts)}


def alert(name, severity, status="firing", **labels):
    return {"status": status, "labels": {"alertname": name, "severity": severity, "prometheus": "dev01", **labels},
            "annotations": {"summary": f"{name} summary"}, "startsAt": "2026-11-24T05:00:00Z",
            "endsAt": "2026-11-24T05:10:00Z"}


class ExpandTest(unittest.TestCase):
    def test_levels_channels_and_night(self):
        day = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)      # 12:00 Riyadh
        night = datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc)   # 00:00 Riyadh
        msg = {"kind": "alert", "level": "page", "subject": "s" * 300, "body": "b"}
        rows = server.expand(RECIPIENTS, msg, day)
        self.assertEqual(sorted((r["recipient"], r["channel"]) for r in rows),
                         [("lead", "email"), ("lead", "sms"), ("ops", "email"), ("ops", "sms")])
        self.assertEqual(len([r for r in rows if r["channel"] == "sms"][0]["text"]), 160)
        rows = server.expand(RECIPIENTS, msg, night)
        self.assertEqual([(r["recipient"], r.get("skipped")) for r in rows if r["channel"] == "sms"],
                         [("ops", None), ("lead", "night")])
        rows = server.expand(RECIPIENTS, {**msg, "level": "notify"}, day)
        self.assertEqual([(r["recipient"], r["channel"]) for r in rows], [("ops", "email")])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        (d / "r.json").write_text(json.dumps(RECIPIENTS), encoding="utf-8")
        self.gw = server.Gateway(str(d / "spool"), str(d / "r.json"))
        self.srv = server._Server(("127.0.0.1", 0), server.make_handler(self.gw))
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.dir.cleanup()

    def post(self, path, doc):
        req = urllib.request.Request(self.url + path, data=json.dumps(doc).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def get(self, path):
        with urllib.request.urlopen(self.url + path) as r:
            return json.loads(r.read())

    def lines(self, name):
        p = Path(self.dir.name) / "spool" / name
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_heartbeat_recorded_not_delivered(self):
        self.assertIsNone(self.get("/heartbeat?source=dev01")["last_received"])
        self.assertEqual(self.post("/alertmanager", am_payload(alert("OSS911Heartbeat", "heartbeat"))),
                         {"deliveries": 0})
        self.assertIsNotNone(self.get("/heartbeat?source=dev01")["last_received"])
        self.assertIsNone(self.get("/heartbeat?source=other")["last_received"])
        self.assertEqual(self.lines("outbox.jsonl"), [])
        self.assertEqual(self.lines("received.jsonl"), [])
        again = server.Gateway(str(Path(self.dir.name) / "spool"), str(Path(self.dir.name) / "r.json"))
        self.assertIn("dev01", again.heartbeats)                          # kept across a restart

    def test_alert_delivered_per_recipient_and_channel(self):
        n = self.post("/alertmanager", am_payload(alert("OSS911SourceFailing", "page", source_id="icp_cdr_voice"),
                                                  alert("OSS911SourceFailing", "page", "resolved",
                                                        source_id="icp_agent_day")))
        self.assertEqual(n["deliveries"], 4)
        out = self.lines("outbox.jsonl")
        self.assertEqual(out[0]["subject"], "[OSS911 PAGE] FIRING OSS911SourceFailing: OSS911SourceFailing summary"
                                            " (+1 more: 1 firing, 1 resolved)")
        self.assertIn("source_id=icp_cdr_voice", [r for r in out if r["channel"] == "email"][0]["body"])
        self.assertNotIn("dev01", [r for r in out if r["channel"] == "email"][0]["body"])
        self.assertEqual(len(self.lines("received.jsonl")), 1)

    def test_direct_message_and_validation(self):
        self.assertEqual(self.post("/direct", {"kind": "digest", "level": "digest", "subject": "d", "body": "b"}),
                         {"deliveries": 1})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/direct", {"kind": "x", "level": "loud", "subject": "s"})
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
