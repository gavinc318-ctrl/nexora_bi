# -*- coding: utf-8 -*-
"""Check that executable code contains no Chinese characters.

Rule (2026-10-14, Gavin): executable code is written in English -- comments, task names,
messages and identifiers included. Documents (runbooks, NOTES, development log, LLD
mirrors, CLAUDE.md) and business-facing YAML (contracts/, metrics/) stay in Chinese.
Development support tools (tools/: document conversion, consistency checks) are out of
scope -- they are not delivered and do not run in the target environment.

Two kinds of exception, both explicit:
  1. Files already executed and checksummed in meta.ddl_change (DD-73). Editing them would
     make the deployment playbook fail by design; they stay as they are. Listed in
     EXECUTED_DDL below -- extend it whenever a new structural change has been executed.
  2. Functional Chinese: string literals that must match Chinese text verbatim (headings
     in the .docx originals, exception messages raised by an already-executed change).
     Such a line must carry the marker  cjk-ok  together with a reason, e.g.
         HEADING = "第"   # cjk-ok: matches chapter headings in the .docx originals
     Keep such literals together in one constant block per file.

Usage:  python3 tools/check_no_cjk.py      exit code 0 = clean, 1 = violations found
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CODE_DIRS = [
    "infra/playbooks",
    "infra/files",
    "infra/inventory",
    "infra/ansible.cfg",
    "ddl",
    "test",
    "ingest",
    "recon",
]
CODE_SUFFIXES = {".yml", ".yaml", ".j2", ".sql", ".py", ".toml", ".cfg", ".ini", ".sh", ".lock", ""}
SKIP_SUFFIXES = {".csv", ".md"}          # data and documents, not code

EXECUTED_DDL = {
    "ddl/01_schemas_and_helpers.sql",
    "ddl/02_core_dimensions.sql",
    "ddl/03_core_facts.sql",
    "ddl/04_meta_and_audit.sql",
    "ddl/05_partitions_and_indexes.sql",
    "ddl/changes/0000_ddl_change.sql",
    "ddl/changes/0002_roles_and_grants.sql",
    "ddl/changes/0003_quarantine_and_param_log.sql",
}

CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
MARKER = "cjk-ok"


def iter_files():
    for entry in CODE_DIRS:
        path = os.path.join(ROOT, entry)
        if os.path.isfile(path):
            yield entry
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "build") and not d.endswith(".egg-info")]
            for name in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
                suffix = os.path.splitext(name)[1]
                if suffix in SKIP_SUFFIXES or suffix not in CODE_SUFFIXES:
                    continue
                yield rel


def main():
    violations = []
    for rel in iter_files():
        if rel in EXECUTED_DDL:
            continue
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if CJK.search(line) and MARKER not in line:
                    violations.append((rel, lineno, line.rstrip()))
    if not violations:
        print("check_no_cjk: no Chinese in executable code")
        return 0
    files = sorted({v[0] for v in violations})
    for rel, lineno, line in violations:
        print(f"{rel}:{lineno}: {line[:120]}")
    print(f"\ncheck_no_cjk: {len(violations)} line(s) in {len(files)} file(s) contain Chinese", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
