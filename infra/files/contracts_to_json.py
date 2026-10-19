"""Read the contract release list and print the released contracts as one JSON array (DD-88).

Usage:  python3 contracts_to_json.py <contracts directory>

Runs on the Ansible control node, called by infra/playbooks/45-contracts.yml; needs only PyYAML, which
Ansible itself already depends on. Converting here, rather than reading the YAML with Ansible's from_yaml,
keeps Ansible from templating the contracts: they contain literal "{{ window_from }}" placeholders.

Each element:  {source_id, connector, target_raw, contract_version, enabled, definition}
where definition is the whole contract document except "enabled" -- start / stop lives only in
meta.source_contract.enabled, and a second copy inside definition would be a second truth (DD-88).

Checks done here, before anything reaches the database:
  - every listed file exists, is listed once, and is a YAML mapping with the five top-level keys
  - source_id is unique across the release
  - no key anywhere in a contract looks like a secret (LD-01 2.4: contracts carry credential_ref only)
Exit code 0 with JSON on stdout; 1 with the reasons on stderr.
"""

import json
import os
import re
import sys

import yaml

RELEASE_FILE = "release.yml"
REQUIRED = {"source_id": str, "connector": str, "target_raw": str, "contract_version": int, "enabled": bool}
SECRET_KEY = re.compile(r"passw|secret|private_key", re.IGNORECASE)


def secret_keys(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if SECRET_KEY.search(str(key)):
                yield here
            yield from secret_keys(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from secret_keys(value, f"{path}[{i}]")


def main(argv):
    if len(argv) != 2:
        print("usage: contracts_to_json.py <contracts directory>", file=sys.stderr)
        return 1
    base = argv[1]
    errors = []
    with open(os.path.join(base, RELEASE_FILE), encoding="utf-8") as fh:
        release = (yaml.safe_load(fh) or {}).get("release")
    if not isinstance(release, list) or not release or not all(isinstance(f, str) for f in release):
        print(f"{RELEASE_FILE}: 'release' must be a non-empty list of file names", file=sys.stderr)
        return 1
    if len(set(release)) != len(release):
        errors.append(f"{RELEASE_FILE}: a file is listed more than once")

    out, seen = [], {}
    for name in release:
        path = os.path.join(base, name)
        if os.path.basename(name) != name or not os.path.isfile(path):
            errors.append(f"{name}: listed in {RELEASE_FILE} but not a file in {base}")
            continue
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        if not isinstance(doc, dict):
            errors.append(f"{name}: not a YAML mapping")
            continue
        for key, typ in REQUIRED.items():
            # bool is a subclass of int: contract_version: true must not pass as 1
            if not isinstance(doc.get(key), typ) or (typ is int and isinstance(doc.get(key), bool)):
                errors.append(f"{name}: top-level '{key}' missing or not {typ.__name__}")
        for key in secret_keys(doc):
            errors.append(f"{name}: key '{key}' looks like a secret; contracts carry credential_ref only (LD-01 2.4)")
        sid = doc.get("source_id")
        if sid in seen:
            errors.append(f"{name}: source_id '{sid}' already used by {seen[sid]}")
        seen[sid] = name
        definition = {k: v for k, v in doc.items() if k != "enabled"}
        out.append({
            "file": name,
            "source_id": sid,
            "connector": doc.get("connector"),
            "target_raw": doc.get("target_raw"),
            "contract_version": doc.get("contract_version"),
            "enabled": doc.get("enabled"),
            "definition": definition,
        })

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    try:
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    except TypeError as exc:   # e.g. an unquoted date that YAML turned into a date object
        print(f"a contract holds a value JSON cannot represent (quote it in the YAML): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
