# Run inline by infra/playbooks/30-python.yml via python3 -c (not deployed as a file: the airflow user cannot read the repository).
# Parses the pool list from the output of `airflow pools list -o json` and prints "name|slots|description" per line.
#
# Parsing simply from the first "[" will not do: Airflow 3 marks the pools subcommands deprecated (in favour of airflowctl),
# so log lines may precede the output, and log lines themselves start with "[timestamp]". Hence try each line starting with "["
# until one parses into a list whose elements all have a "pool" key.
import json, sys
t = sys.stdin.read()
dec = json.JSONDecoder()
pools = None
for i, ch in enumerate(t):
    if ch == "[" and (i == 0 or t[i - 1] == "\n"):
        try:
            v, _ = dec.raw_decode(t[i:])
        except ValueError:
            continue
        if isinstance(v, list) and all(isinstance(p, dict) and "pool" in p for p in v):
            pools = v
            break
if pools is None:
    sys.exit("could not parse a pool list from the output of airflow pools list")
for p in pools:
    print("{}|{}|{}".format(p["pool"], int(p["slots"]), p.get("description") or ""))
