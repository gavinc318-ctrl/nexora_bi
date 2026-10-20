"""Print the contracts listed in release.yml as one JSON array, for contract_sync.sql (DD-88).

Usage:  <venv-ingest>/bin/python contracts_to_json.py <contracts directory>

Conversion only. The rules are checked beforehand by `oss911-ingest validate-contract`, the single set of
contract rules (DD-88 item fourteen); nothing here re-checks them. 45-contracts.yml runs this on the target,
over the same copy of the files that was validated, with venv-ingest's PyYAML.

Each element:  {file, source_id, connector, target_raw, contract_version, enabled, definition}
where definition is the whole contract document except "enabled" -- start / stop lives only in
meta.source_contract.enabled, and a second copy inside definition would be a second truth (DD-88).
Exit code 0 with JSON on stdout; 1 with the reason on stderr.
"""

import json
import os
import sys

import yaml


def main(argv):
    if len(argv) != 2:
        print("usage: contracts_to_json.py <contracts directory>", file=sys.stderr)
        return 1
    base = argv[1]
    with open(os.path.join(base, "release.yml"), encoding="utf-8") as fh:
        release = (yaml.safe_load(fh) or {}).get("release")
    if not isinstance(release, list) or not release or len(set(release)) != len(release):
        print("release.yml: 'release' must be a non-empty list of distinct file names", file=sys.stderr)
        return 1
    out = []
    for name in release:
        with open(os.path.join(base, os.path.basename(name)), encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        out.append({
            "file": name,
            "source_id": doc["source_id"],
            "connector": doc["connector"],
            "target_raw": doc["target_raw"],
            "contract_version": doc["contract_version"],
            "enabled": doc["enabled"],
            "definition": {k: v for k, v in doc.items() if k != "enabled"},
        })
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
