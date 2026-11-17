"""Call the query API as test users, the way the front end will (60-svc.yml's verification).

Usage:  python3 svc_probe.py < spec.json       (standard library only)
spec: {"token_url", "client", "svc_base", "users": {name: password}, "probe": [names]}
For each probed user: log in through the realm (password grant, development only), POST /v1/query for M-C09 by
agent, and print "<user>|<status>|<agents>|<first agents>|<row scope>" or "<user>|<status>|<error code>".
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

spec = json.load(sys.stdin)


def token(user):
    data = urllib.parse.urlencode({"grant_type": "password", "client_id": spec["client"], "username": user,
                                   "password": spec["users"][user]}).encode()
    with urllib.request.urlopen(urllib.request.Request(spec["token_url"], data=data), timeout=10) as r:
        return json.load(r)["access_token"]


body = json.dumps({"metrics": ["M-C09"], "dimensions": ["agent"],
                   "range": {"from": "2026-10-01", "to": "2027-01-01"}}).encode()
for user in spec["probe"]:
    req = urllib.request.Request(spec["svc_base"] + "/v1/query", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + token(user), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.load(r)
            agents = sorted({row["agent"] for row in j["rows"]})
            print(f"{user}|{r.status}|{len(agents)}|{','.join(agents[:3])}|{j['meta']['row_scope']}")
    except urllib.error.HTTPError as e:
        print(f"{user}|{e.code}|{json.load(e).get('error')}")
