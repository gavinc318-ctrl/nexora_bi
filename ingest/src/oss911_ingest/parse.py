"""Turning a received CSV file into rows, and the content hash (LD-01 2.5 · DD-82).

Nothing here interprets values: raw records the source's text as received (DD-02). Interpretation is
staging's job.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field


@dataclass
class Parsed:
    header: list[str]
    rows: list[list[str]]                       # every row has len(header) values
    unparseable: str | None = None              # set when the file cannot be read reliably; rows is then empty
    sha256: bytes = b""
    size: int = 0
    header_problems: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)


def parse_csv(data: bytes, expected: list[str]) -> Parsed:
    """Parse a CSV file and compare its header with the contract's column list.

    A file that cannot be read reliably (not UTF-8, unbalanced quotes, rows with a different number of fields,
    a repeated column name) gives no rows at all: guessing which value belongs to which column would put
    values into the wrong columns without any error (guess 10). The batch is then quarantined whole and the
    original file is the only record of it -- which is why archiving it (DD-87) is required before go-live.
    """
    p = Parsed(header=[], rows=[], sha256=hashlib.sha256(data).digest(), size=len(data))
    try:
        text = data.decode("utf-8")              # strict: a BOM shows up as part of the first column name
    except UnicodeDecodeError as exc:
        p.unparseable = f"not UTF-8 at byte {exc.start}"
        return p
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        p.unparseable = f"CSV syntax: {exc}"
        return p
    if not records:
        p.unparseable = "empty file: not even a header row (guess 9 says an empty result still has one)"
        return p
    header, body = records[0], records[1:]
    if len(set(header)) != len(header):
        p.unparseable = f"repeated column names in the header: {sorted({h for h in header if header.count(h) > 1})}"
        return p
    for n, rec in enumerate(body, 2):
        if len(rec) != len(header):
            p.unparseable = f"line {n} has {len(rec)} fields, the header has {len(header)}"
            return p
    p.header, p.rows = header, body

    if header != expected:
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        if missing:
            p.header_problems.append(f"columns missing: {missing}")
        if extra:
            p.header_problems.append(f"columns not in the contract: {extra}")
            p.unknown_columns = extra
        if not missing and not extra:
            p.header_problems.append(f"column order differs from the contract: {header}")
    return p


def content_hash(header: list[str], values: list[str]) -> bytes:
    """SHA-256 over all received column names and values, in the order received (LD-01 2.5 · DD-88 item eight).

    Columns the contract does not know yet are included: a new column with a new value is a new observation.
    """
    pairs = [[name, value] for name, value in zip(header, values)]
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).digest()
