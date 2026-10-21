"""Batch checks for ICP CDRs: the quality.batch_checks of contract if03a written as code.

The contract states the checks in words, which cannot be executed; this module is their executable form and
must change with them. Outcome of a batch (LD-01 7.1 · DD-76 · DD-82):
  - any quarantine reason -> the whole batch is written to raw as usual and registered in meta.quarantine_batch
  - CALLTYPE outside the value table -> not quarantined; each value goes to meta.clarification_queue
A CALLID without a CALLIDNUM = -1 row is a call still in progress: left to the lookback window, not an error.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# strptime alone also accepts "2026-10-14 8:03:00"; the source's declared format is zero-padded (guess 5),
# and staging relies on the text sorting like the time it writes
TIME_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
TIME_COLUMNS = ["WAITBEGIN", "WAITEND", "ACKBEGIN", "ACKEND", "CALLBEGIN", "CALLEND"]
# Pairs that must not be inverted: (earlier, later) on the same device (LD-01 4.3)
ORDER_PAIRS = [("WAITBEGIN", "WAITEND"), ("ACKBEGIN", "ACKEND"), ("CALLBEGIN", "CALLEND")]


@dataclass
class CheckResult:
    quarantine: list[str] = field(default_factory=list)
    unknown_values: dict[str, Counter] = field(default_factory=dict)   # column -> value -> rows


def check_cdr(header: list[str], rows: list[list[str]], definition: dict) -> CheckResult:
    res = CheckResult()
    col = {name: i for i, name in enumerate(header)}
    for needed in ("CALLID", "CALLIDNUM", "WAITBEGIN", "CALLTYPE"):
        if needed not in col:
            res.quarantine.append(f"column {needed} missing, batch checks cannot run")
    if res.quarantine:
        return res

    def get(row, name):
        return row[col[name]] if name in col else ""

    empty_key = bad_seq = empty_wb = 0
    bad_time: Counter = Counter()
    inverted: Counter = Counter()
    seqs: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        callid, seq = get(row, "CALLID"), get(row, "CALLIDNUM")
        if not callid or not seq:
            empty_key += 1                      # a NULL idempotency key would make re-runs duplicate rows (LD-01 2.2)
        else:
            try:
                int(seq)
            except ValueError:
                bad_seq += 1
            seqs[callid][seq] += 1
        if not get(row, "WAITBEGIN"):
            empty_wb += 1
        times = {}
        for name in TIME_COLUMNS:
            value = get(row, name)
            if value:
                try:
                    if not TIME_SHAPE.fullmatch(value):
                        raise ValueError(value)
                    times[name] = datetime.strptime(value, TIME_FORMAT)
                except ValueError:
                    bad_time[name] += 1
        for earlier, later in ORDER_PAIRS:
            if earlier in times and later in times and times[later] < times[earlier]:
                inverted[f"{later} < {earlier}"] += 1

    if empty_key:
        res.quarantine.append(f"CALLID or CALLIDNUM empty ({empty_key} rows)")
    if bad_seq:
        res.quarantine.append(f"CALLIDNUM not an integer ({bad_seq} rows)")
    dup = sum(1 for counts in seqs.values() for n in counts.values() if n > 1)
    if dup:
        res.quarantine.append(f"CALLIDNUM repeated within a CALLID ({dup} cases)")
    if empty_wb:
        res.quarantine.append(f"WAITBEGIN empty ({empty_wb} rows)")
    for name, n in sorted(bad_time.items()):
        res.quarantine.append(f"{name} not in the format {TIME_FORMAT} ({n} rows)")
    for pair, n in sorted(inverted.items()):
        res.quarantine.append(f"time inversion {pair} ({n} rows)")

    known = {str(v) for v in (((definition.get("enums") or {}).get("call_type") or {}).get("values") or [])}
    if known:
        unknown = Counter(v for v in (get(r, "CALLTYPE") for r in rows) if v not in known)
        if unknown:
            res.unknown_values["CALLTYPE"] = unknown
    else:
        # The run cannot judge CALLTYPE without the value table; that is a contract defect, not a data defect
        res.quarantine.append("contract has no enums.call_type.values; CALLTYPE cannot be checked")
    return res
