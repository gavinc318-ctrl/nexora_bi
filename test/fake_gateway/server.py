"""Fake delivery gateway for the development machine (DD-100 block three). Not delivered.

Stands in for the delivery service of LD-01 10.2 (Alertmanager -> webhook -> our delivery service -> the main
contractor's SMS and e-mail gateways) until that service exists. It sends nothing: every message it would send is
written to a file, one line per recipient and channel, so that "an alert reaches the people configured for it" can be
checked on the development machine.

Interface (what the delivery service has to offer too, so that nothing upstream changes when it replaces this one):

  POST /alertmanager   Alertmanager's webhook (version 4). Alerts with severity heartbeat are not delivered: they
                       only record that the heartbeat arrived, per source (the alert's label prometheus).
  POST /direct         a message that does not come through Alertmanager: the heartbeat checker's "alert path
                       broken / recovered" and the daily digest. JSON {kind, level, source, subject, body}.
  GET  /heartbeat?source=S   {"source", "last_received" (ISO time or null), "gateway_started_at"}
  GET  /healthz

Recipients come from a JSON file, read again at every message (alert-recipients.json, written by 56-alerting.yml from
the inventory's alert_recipients): which levels each recipient takes, which channel each level uses, and whether SMS
goes out at night. An SMS not sent at night is still written, marked skipped, so the rule can be seen working.

Files in the spool directory:
  received.jsonl   every message as it came in, heartbeats left out (the evidence the delivery service will keep
                   in audit.delivery_log)
  outbox.jsonl     one line per recipient and channel: what would have been sent
  heartbeat.json   the last heartbeat per source

Standard library only: it runs under the system python3, as its own account (oss911-fake-gateway.service).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
from datetime import datetime, time as dtime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

SMS_MAX = 160
LEVELS = ("page", "notify", "digest")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def load_recipients(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    channels = doc.get("channels") or {}
    for level in LEVELS:
        if not isinstance(channels.get(level), list):
            raise ValueError(f"channels.{level} must be a list of channels")
    for r in doc.get("recipients") or []:
        if not isinstance(r.get("name"), str) or not isinstance(r.get("levels"), list):
            raise ValueError("every recipient needs a name and a list of levels")
    return doc


def is_night(cfg: dict, at: datetime) -> bool:
    night = cfg.get("night") or {}
    if not night:
        return False
    local = at.astimezone(ZoneInfo(night.get("timezone", "UTC"))).time()
    start, end = (dtime.fromisoformat(night["start"]), dtime.fromisoformat(night["end"]))
    return local >= start or local < end if start > end else start <= local < end


def expand(cfg: dict, msg: dict, at: datetime) -> list[dict]:
    """The deliveries of one message: one per recipient of its level and channel of that level"""
    level = msg["level"]
    night = is_night(cfg, at)
    out = []
    for r in cfg.get("recipients") or []:
        if level not in r["levels"]:
            continue
        for channel in cfg["channels"][level]:
            address = r.get(channel)
            if not address:
                continue
            line = {"at": iso(at), "kind": msg["kind"], "level": level, "source": msg.get("source"),
                    "recipient": r["name"], "channel": channel, "to": address, "subject": msg["subject"]}
            if channel == "sms":
                line["text"] = msg["subject"][:SMS_MAX]
                if night and not r.get("sms_at_night", False):
                    line["skipped"] = "night"
            else:
                line["body"] = msg.get("body", "")
            out.append(line)
    return out


def from_alertmanager(payload: dict) -> list[dict]:
    """Alertmanager webhook -> messages: one per notification, the level from the alerts' severity"""
    alerts = payload.get("alerts") or []
    by_level: dict[str, list[dict]] = {}
    for a in alerts:
        by_level.setdefault((a.get("labels") or {}).get("severity", "notify"), []).append(a)
    msgs = []
    for level, group in by_level.items():
        firing = [a for a in group if a.get("status") == "firing"]
        resolved = [a for a in group if a.get("status") != "firing"]
        names = sorted({(a.get("labels") or {}).get("alertname", "?") for a in group})
        state = "FIRING" if firing else "RESOLVED"
        # The subject is the SMS: the first place's summary, and how many more the body lists
        first = (firing or resolved)[0]
        subject = (f"[OSS911 {level.upper()}] {state} {', '.join(names)}: "
                   f"{(first.get('annotations') or {}).get('summary') or ''}")
        if len(group) > 1:
            subject += f" (+{len(group) - 1} more: {len(firing)} firing, {len(resolved)} resolved)"
        lines = []
        for a in firing + resolved:
            lab = a.get("labels") or {}
            where = ", ".join(f"{k}={v}" for k, v in sorted(lab.items())
                              if k not in ("alertname", "severity", "env", "site", "prometheus", "job", "instance"))
            lines.append(f"{a.get('status', '?').upper():8} {lab.get('alertname', '?')}  {where}\n"
                         f"         {(a.get('annotations') or {}).get('summary', '')}\n"
                         f"         since {a.get('startsAt', '?')}"
                         + (f", ended {a.get('endsAt')}" if a.get("status") != "firing" else ""))
        msgs.append({"kind": "alert", "level": level if level in LEVELS else "notify",
                     "source": (group[0].get("labels") or {}).get("prometheus"), "subject": subject,
                     "body": "\n".join(lines)})
    return msgs


class Gateway:
    def __init__(self, spool: str, recipients_path: str) -> None:
        self.spool = spool
        self.recipients_path = recipients_path
        self.started_at = now_utc()
        self.lock = threading.Lock()
        os.makedirs(spool, exist_ok=True)
        self.hb_path = os.path.join(spool, "heartbeat.json")
        try:
            with open(self.hb_path, encoding="utf-8") as fh:
                self.heartbeats = json.load(fh)
        except (OSError, ValueError):
            self.heartbeats = {}

    def _append(self, name: str, rows: list[dict]) -> None:
        with open(os.path.join(self.spool, name), "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def deliver(self, via: str, raw: dict, msgs: list[dict]) -> int:
        at = now_utc()
        cfg = load_recipients(self.recipients_path)
        rows = [d for m in msgs for d in expand(cfg, m, at)]
        with self.lock:
            self._append("received.jsonl", [{"received_at": iso(at), "via": via, "payload": raw}])
            self._append("outbox.jsonl", rows)
        return len(rows)

    def heartbeat(self, payload: dict) -> list[dict]:
        """Records heartbeats; returns the alerts that are not heartbeats"""
        rest = []
        beats = set()
        for a in payload.get("alerts") or []:
            lab = a.get("labels") or {}
            if lab.get("severity") == "heartbeat":
                if a.get("status") == "firing":
                    beats.add(lab.get("prometheus") or "default")
            else:
                rest.append(a)
        if beats:
            with self.lock:
                for src in beats:
                    self.heartbeats[src] = iso(now_utc())
                tmp = self.hb_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(self.heartbeats, fh)
                os.replace(tmp, self.hb_path)
        return rest


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # The host's address on the container network may come up after this service: bind before it exists
        self.socket.setsockopt(socket.SOL_IP, getattr(socket, "IP_FREEBIND", 15), 1)
        super().server_bind()


def make_handler(gw: Gateway):
    class Handler(BaseHTTPRequestHandler):
        server_version = "oss911-fake-gateway"
        sys_version = ""

        def _reply(self, code: int, doc: dict) -> None:
            body = (json.dumps(doc) + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):   # noqa: N802
            url = urlparse(self.path)
            if url.path == "/healthz":
                self._reply(200, {"status": "ok"})
            elif url.path == "/heartbeat":
                src = (parse_qs(url.query).get("source") or ["default"])[0]
                with gw.lock:
                    last = gw.heartbeats.get(src)
                self._reply(200, {"source": src, "last_received": last, "gateway_started_at": iso(gw.started_at)})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):   # noqa: N802
            url = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("the body must be a JSON object")
                if url.path == "/alertmanager":
                    rest = gw.heartbeat(payload)
                    n = gw.deliver("alertmanager", payload, from_alertmanager({**payload, "alerts": rest})) if rest else 0
                elif url.path == "/direct":
                    for key in ("kind", "level", "subject"):
                        if not isinstance(payload.get(key), str):
                            raise ValueError(f"{key} is missing")
                    if payload["level"] not in LEVELS:
                        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
                    n = gw.deliver("direct", payload, [payload])
                else:
                    self._reply(404, {"error": "not found"})
                    return
            except (ValueError, KeyError, OSError) as exc:
                print(f"fake-gateway: {url.path}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                self._reply(400 if not isinstance(exc, OSError) else 500, {"error": str(exc)})
                return
            self._reply(200, {"deliveries": n})

        def log_message(self, fmt, *args):   # heartbeats every minute: no access log
            pass

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fake delivery gateway (development only)")
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9196)
    ap.add_argument("--recipients", required=True, help="alert-recipients.json")
    ap.add_argument("--spool", required=True, help="directory for received.jsonl, outbox.jsonl, heartbeat.json")
    args = ap.parse_args(argv)
    load_recipients(args.recipients)   # refuse to start on a file that does not load
    gw = Gateway(args.spool, args.recipients)
    server = _Server((args.listen, args.port), make_handler(gw))
    print(f"fake-gateway listening on {args.listen}:{args.port}, spool {args.spool}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
