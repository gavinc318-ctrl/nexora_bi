"""The monitoring configuration to install: the release's contracts/monitoring.yml with each expected check's
deployed_at (DD-100 review note, item three: a new check's grace period starts when it is deployed).

Usage:  <venv-ingest>/bin/python monitoring_install.py <release monitoring.yml> <installed monitoring.yml> <now>
                                                        <release dbt_project.yml>

Prints the file to install. An expected check already in the installed file keeps its deployed_at; a new one gets
<now> (an ISO 8601 time with offset). The file is validated with the exporter's own rules (oss911_ingest.monitor,
load_config) before it is printed, so 55-monitor never installs one the exporter would refuse. The closing threshold
is written twice -- thresholds.closing_lag_days here for the alert, the dbt variable closing_lag_days for the nightly
test mart_closing_not_behind -- so a release where the two differ is refused (NOTES 2026-11-23, open item one). Exit code 0 with
the file on stdout; 1 with the reason on stderr. 55-monitor.yml runs this on the target with venv-ingest.
"""

import sys
from datetime import datetime

import yaml

from oss911_ingest.monitor import MonitorConfigError, load_config


def main(argv):
    if len(argv) != 5:
        print("usage: monitoring_install.py <release monitoring.yml> <installed monitoring.yml> <now>"
              " <release dbt_project.yml>", file=sys.stderr)
        return 1
    _, new_path, old_path, now, dbt_path = argv
    try:
        datetime.fromisoformat(now)
    except ValueError:
        print(f"<now> is not an ISO 8601 time: {now!r}", file=sys.stderr)
        return 1
    with open(new_path, encoding="utf-8") as fh:
        text = fh.read()
    doc = yaml.safe_load(text)
    try:
        with open(old_path, encoding="utf-8") as fh:
            old = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        old = {}
    before = {c.get("id"): c.get("deployed_at") for c in (old.get("expected_checks") or []) if isinstance(c, dict)}
    for c in doc.get("expected_checks") or []:
        if isinstance(c, dict):
            if "deployed_at" in c:
                print(f"expected check {c.get('id')}: deployed_at is set at installation, not in the release",
                      file=sys.stderr)
                return 1
            c["deployed_at"] = str(before.get(c.get("id")) or now)
    out = ("# Installed by infra/playbooks/55-monitor.yml from the release's contracts/monitoring.yml, do not edit.\n"
           "# deployed_at: when the expected check first came with a release (the start of its grace period)\n"
           + yaml.safe_dump(doc, sort_keys=False, allow_unicode=False, width=120))
    try:
        load_config(out)
    except (MonitorConfigError, yaml.YAMLError) as exc:
        print(f"the monitoring configuration is not valid: {exc}", file=sys.stderr)
        return 1
    with open(dbt_path, encoding="utf-8") as fh:
        dbt_lag = ((yaml.safe_load(fh) or {}).get("vars") or {}).get("closing_lag_days")
    lag = (doc.get("thresholds") or {}).get("closing_lag_days")
    if dbt_lag != lag:
        print(f"thresholds.closing_lag_days is {lag!r} but the dbt variable closing_lag_days is {dbt_lag!r}"
              f" ({dbt_path}): the alert and the nightly test mart_closing_not_behind would disagree", file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
