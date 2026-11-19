# -*- coding: utf-8 -*-
"""Fake hindex/agent: historical indexes about agents (interface reference p.935-936, p.1035). Development only.

The real interface takes ccId, vdnId, objectIds and indexIds and nothing else -- no date (DD-98 item one). This fake
answers "today so far", the assumption the ingestion is built on until ICP answers Q36:
  - the platform's day is cut at 00:00 UTC+3 (DAY_OFFSET; Q36 asks which zone it really is)
  - IDX_04_03_001: the agent's sign-in intervals with inTime today, up to now. An interval still open has an empty
    outTime and a duration counted up to now (Q37 asks what the real one does)
  - IDX_04_02_*: day totals so far. The call counts and talk time come from the fake CDRs (generator.py) of today,
    so ingestion and reconciliation see numbers that agree with the call legs; the other states are invented
  - an agent outside the generator's ID range: every value null, as the document's example shows for an unknown
    object. Values are strings; the idle time is written "n.0" like the document's example (DD-98 item four)
Everything is a function of (seed, agent, day) and now, so the same moment always gives the same answer.

Injections (POST /_fake/control {"agent_inject": [...]}), used once by the next request:
  day_bad_value      one day value "12.5"
  session_inverted   one closed interval with outTime before inTime
  extra_agent        a record for an agent that was not asked for
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import generator as gen

INDEX_PATH = "/cmsgateway/resource/hindex/agent"
DAY_OFFSET = timedelta(hours=3)          # the fake platform's day: 00:00 UTC+3
SESSION_INDEX = "IDX_04_03_001"
DAY_INDEXES = [f"IDX_04_02_{i:03d}" for i in range(1, 29)]
MAX_OBJECTS = 20000
INJECTIONS = ("day_bad_value", "session_inverted", "extra_agent")
_cdr_cache: dict = {}


def day_start(now: datetime) -> datetime:
    """The start of the platform's day containing now, as a UTC time without zone (the generator's notation)."""
    local = now + DAY_OFFSET
    return datetime(local.year, local.month, local.day) - DAY_OFFSET


def shifts(seed: int, agent: int, start: datetime) -> list[tuple[datetime, datetime]]:
    """The agent's sign-in intervals on the day beginning at start. The fake CDRs give calls to any agent at any hour,
    so every agent is signed in almost all day -- two intervals split by a break -- and the day totals stay consistent
    with the call legs. A few agents' second interval runs past midnight: it belongs to this day by its inTime (Q38),
    and the next day's first interval of every agent begins after it."""
    rng = random.Random(f"{seed}:agent:{agent}:{start:%Y%m%d}")
    first = start + timedelta(minutes=rng.randint(25, 30), seconds=rng.randint(0, 59))
    cut = start + timedelta(hours=12, minutes=rng.randint(-20, 20))
    second = cut + timedelta(minutes=rng.randint(10, 40))
    end = start + timedelta(hours=23, minutes=rng.randint(45, 55))
    if rng.random() < 0.05:
        end = start + timedelta(days=1, minutes=rng.randint(5, 20))
    return [(first, cut), (second, end)]


def _fmt(t: datetime) -> str:
    return t.strftime(gen.TS_FMT)


def sessions_so_far(seed: int, agent: int, now: datetime) -> list[dict]:
    rng = random.Random(f"{seed}:agent-duration:{agent}:{day_start(now):%Y%m%d}")
    out = []
    for a, b in shifts(seed, agent, day_start(now)):
        if a > now:
            continue
        if b <= now:
            # Mostly the difference of the two times; now and then a few seconds off (reconciliation, not rejected)
            duration = int((b - a).total_seconds()) + (rng.randint(1, 3) if rng.random() < 0.1 else 0)
            out.append({"inTime": _fmt(a), "outTime": _fmt(b), "duration": str(duration)})
        else:
            out.append({"inTime": _fmt(a), "outTime": "", "duration": str(int((now - a).total_seconds()))})
    return out


def _agent_legs(seed: int, now: datetime) -> dict[str, list[dict]]:
    """Today's answered agent legs so far, by agent ID, from the fake CDRs. Cached per (seed, day, minute)."""
    start = day_start(now)
    key = (seed, start, now.replace(second=0, microsecond=0))
    if key not in _cdr_cache:
        _cdr_cache.clear()
        legs: dict[str, list[dict]] = {}
        for r in gen.rows_in_window(seed, start, now, now, "on_leave"):
            if r["DEVICETYPE"] == str(gen.DEVICE_AGENT) and r["CALLBEGIN"] and r["CALLEND"]:
                legs.setdefault(r["DEVICENO"], []).append(r)
        _cdr_cache[key] = legs
    return _cdr_cache[key]


def day_totals(seed: int, agent: int, now: datetime) -> dict[str, str]:
    sessions = sessions_so_far(seed, agent, now)
    signin = sum(int(s["duration"]) for s in sessions)
    legs = _agent_legs(seed, now).get(str(agent), [])
    talk = sum(int((datetime.strptime(r["CALLEND"], gen.TS_FMT)
                    - datetime.strptime(r["CALLBEGIN"], gen.TS_FMT)).total_seconds()) for r in legs)
    rng = random.Random(f"{seed}:agent-day:{agent}:{day_start(now):%Y%m%d}")
    calls = len(legs)
    acw = calls * rng.randint(10, 40)
    hold = calls * rng.randint(0, 5)
    rest = min(signin // 10, rng.randint(0, 3600)) if signin else 0
    busy = rng.randint(0, 600) if signin else 0
    idle = max(0, signin - talk - acw - hold - rest - busy)
    v = {i: "0" for i in DAY_INDEXES}
    v.update({
        "IDX_04_02_001": str(len(sessions)), "IDX_04_02_002": str(signin),
        "IDX_04_02_003": str(calls), "IDX_04_02_004": str(talk),
        "IDX_04_02_005": str(talk // calls if calls else 0),
        "IDX_04_02_009": str(calls), "IDX_04_02_010": str(acw),
        "IDX_04_02_012": str(1 if rest else 0), "IDX_04_02_013": str(rest),
        "IDX_04_02_015": str(calls // 10), "IDX_04_02_016": str(hold),
        "IDX_04_02_018": str(1 if busy else 0), "IDX_04_02_019": str(busy),
        "IDX_04_02_021": str(talk), "IDX_04_02_022": f"{idle}.0",
        "IDX_04_02_023": str(calls // 20), "IDX_04_02_024": str(calls // 25),
    })
    return v


def respond(seed: int, body: dict | None, now: datetime, inject: list[str]) -> tuple[str, str, list | None]:
    """(retcode, message, result) for a hindex/agent request."""
    if body is None:
        return "000-002", "The interface parameters are invalid.", None
    if str(body.get("ccId", 0)) != "1" or str(body.get("vdnId", 0)) != "1":
        return "200-001", "The VDN does not exist.", None                     # single VDN (AS-30)
    ids, indexes = body.get("objectIds"), body.get("indexIds")
    if not (isinstance(ids, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in ids)) \
            or not (isinstance(indexes, list) and all(isinstance(x, str) for x in indexes)):
        return "000-002", "objectIds must be an array of integers and indexIds an array of strings.", None
    if len(ids) > MAX_OBJECTS:
        return "000-002", f"objectIds holds more than {MAX_OBJECTS} IDs.", None
    known = set(gen.AGENT_IDS)
    result = []
    for a in ids:
        idxs = []
        totals = day_totals(seed, a, now) if a in known and set(indexes) & set(DAY_INDEXES) else {}
        for i in indexes:
            if a not in known:
                val = None
            elif i == SESSION_INDEX:
                val = sessions_so_far(seed, a, now)
            else:
                val = totals.get(i)
            idxs.append({"id": i, "val": val})
        result.append({"id": str(a), "idxs": idxs})
    _apply(result, inject)
    return "0", "", result


def _apply(result: list, inject: list[str]) -> None:
    for name in inject:
        if name == "extra_agent":
            result.append({"id": "999", "idxs": []})
        for rec in result:
            for x in rec["idxs"]:
                if name == "day_bad_value" and x["id"] != SESSION_INDEX and x["val"] is not None:
                    x["val"] = "12.5"
                    break
                if name == "session_inverted" and x["id"] == SESSION_INDEX and x["val"]:
                    closed = [iv for iv in x["val"] if iv["outTime"]]
                    if closed:
                        iv = closed[0]
                        iv["outTime"] = _fmt(datetime.strptime(iv["inTime"], gen.TS_FMT) - timedelta(seconds=5))
                        break
            else:
                continue
            break
