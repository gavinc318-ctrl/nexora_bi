"""Bring the oss911 realm of Keycloak to the state 23-keycloak-realm.yml describes, then verify it by logging in.

Usage:  python3 keycloak_realm.py < spec.json        (standard library only; run on the target by the playbook)

The spec comes on stdin, never on the command line, because it holds passwords:
  {"base_url", "admin_user", "admin_password", "realm", "roles": [...], "user_attributes": [...],
   "svc_client", "portal_client", "portal_redirect_uris": [...], "password_grant": bool,
   "users": [{"username", "password", "roles": [...], "attributes": {name: value}}]}

What it makes (DD-99 · LD-07 chapter 2 · LD-04 3.5):
  * the realm, and its four realm roles (call_taker, dispatcher, supervisor, admin): the service layer takes the
    role from realm_access.roles in the access token
  * the user attributes the service layer filters on (agent_no for "only my own rows", agency_type_code for the
    department), declared in the user profile as editable by administrators only: a user who could edit his own
    agent number could read another call taker's rows
  * two clients: svc_client, the service layer itself -- it signs nothing and logs nobody in, it is the audience the
    service checks (aud); portal_client, public, through which people log in (the portal later; for the
    development tests also the password grant, password_grant). Its tokens carry svc_client in aud and the two
    attributes as claims of the same names
  * the users of the spec (the development test users), each with exactly its listed roles among the four
Each step reads first and changes only what differs. Output: one line per change ("changed: ..."), then one line
per verified user ("verified: <user> roles=... agent_no=..."). Exit code 1 with the reason on stderr on any failure.
"""

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


class Failed(Exception):
    pass


def call(method, url, token=None, body=None, form=None, ok=(200, 201, 204)):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as exc:
        text, status = exc.read().decode(errors="replace"), exc.code
    if status not in ok:
        raise Failed(f"{method} {url.split('?')[0]}: HTTP {status} {text[:300]}")
    return status, (json.loads(text) if text.strip().startswith(("{", "[")) else None)


def token_for(base, realm, client, user, password):
    status, data = call("POST", f"{base}/realms/{realm}/protocol/openid-connect/token", ok=(200, 400, 401),
                        form={"grant_type": "password", "client_id": client, "username": user, "password": password})
    return data.get("access_token") if status == 200 and data else None


def claims(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def main():
    spec = json.load(sys.stdin)
    base, realm = spec["base_url"].rstrip("/"), spec["realm"]
    admin = token_for(base, "master", "admin-cli", spec["admin_user"], spec["admin_password"])
    if not admin:
        raise Failed("could not log in to the master realm as the administrator given (admin-cli password grant)")
    api = f"{base}/admin/realms/{realm}"
    out = []

    # Realm
    status, _ = call("GET", api, admin, ok=(200, 404))
    if status == 404:
        call("POST", f"{base}/admin/realms", admin, body={"realm": realm, "enabled": True})
        out.append(f"changed: realm {realm} created")

    # User profile: the filtering attributes, editable by administrators only
    _, profile = call("GET", f"{api}/users/profile", admin)
    have = {a["name"]: a for a in profile.get("attributes", [])}
    wanted_perm = {"view": ["admin"], "edit": ["admin"]}
    touched = False
    for name in spec["user_attributes"]:
        attr = have.get(name)
        if attr is None:
            profile.setdefault("attributes", []).append(
                {"name": name, "displayName": name, "permissions": wanted_perm, "multivalued": False})
            touched = True
        elif attr.get("permissions") != wanted_perm or attr.get("multivalued"):
            attr["permissions"], attr["multivalued"] = wanted_perm, False
            touched = True
    if touched:
        call("PUT", f"{api}/users/profile", admin, body=profile)
        out.append(f"changed: user profile attributes {', '.join(spec['user_attributes'])} (administrators edit them)")

    # Realm roles
    _, roles = call("GET", f"{api}/roles", admin)
    existing_roles = {r["name"] for r in roles}
    for role in spec["roles"]:
        if role not in existing_roles:
            call("POST", f"{api}/roles", admin, body={"name": role})
            out.append(f"changed: realm role {role} created")

    # Clients
    def client(client_id, rep):
        _, found = call("GET", f"{api}/clients?clientId={urllib.parse.quote(client_id)}", admin)
        if not found:
            call("POST", f"{api}/clients", admin, body={"clientId": client_id, "protocol": "openid-connect", **rep})
            out.append(f"changed: client {client_id} created")
            _, found = call("GET", f"{api}/clients?clientId={urllib.parse.quote(client_id)}", admin)
        current = found[0]
        if any(current.get(k) != v for k, v in rep.items()):
            call("PUT", f"{api}/clients/{current['id']}", admin, body={**current, **rep})
            out.append(f"changed: client {client_id} settings")
        return current["id"]

    client(spec["svc_client"], {"enabled": True, "publicClient": False, "standardFlowEnabled": False,
                                "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
                                "serviceAccountsEnabled": False})
    portal = client(spec["portal_client"], {
        "enabled": True, "publicClient": True, "standardFlowEnabled": True, "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": bool(spec["password_grant"]),
        "redirectUris": spec["portal_redirect_uris"], "webOrigins": ["+"]})

    mappers = {"aud-" + spec["svc_client"]: {
        "protocolMapper": "oidc-audience-mapper",
        "config": {"included.client.audience": spec["svc_client"], "access.token.claim": "true",
                   "id.token.claim": "false"}}}
    for name in spec["user_attributes"]:
        mappers["attr-" + name] = {
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {"user.attribute": name, "claim.name": name, "jsonType.label": "String",
                       "access.token.claim": "true", "id.token.claim": "false", "userinfo.token.claim": "false",
                       "multivalued": "false"}}
    _, present = call("GET", f"{api}/clients/{portal}/protocol-mappers/models", admin)
    present = {m["name"]: m for m in present}
    for name, rep in mappers.items():
        cur = present.get(name)
        if cur is None:
            call("POST", f"{api}/clients/{portal}/protocol-mappers/models", admin,
                 body={"name": name, "protocol": "openid-connect", **rep})
            out.append(f"changed: mapper {name} on {spec['portal_client']}")
        elif cur.get("protocolMapper") != rep["protocolMapper"] or any(
                cur.get("config", {}).get(k) != v for k, v in rep["config"].items()):
            call("PUT", f"{api}/clients/{portal}/protocol-mappers/models/{cur['id']}", admin,
                 body={**cur, **rep, "config": {**cur.get("config", {}), **rep["config"]}})
            out.append(f"changed: mapper {name} on {spec['portal_client']} settings")

    # Users
    role_reps = {r["name"]: r for r in call("GET", f"{api}/roles", admin)[1]}
    for u in spec["users"]:
        name = u["username"]
        _, found = call("GET", f"{api}/users?exact=true&username={urllib.parse.quote(name)}", admin)
        # firstName, lastName and email are set so that a login is not stopped by "update your profile"
        rep = {"username": name, "enabled": True, "emailVerified": True, "firstName": name, "lastName": "test",
               "email": f"{name}@oss911.invalid",
               "attributes": {k: [v] for k, v in sorted(u.get("attributes", {}).items())}}
        if not found:
            call("POST", f"{api}/users", admin, body=rep)
            out.append(f"changed: user {name} created")
            _, found = call("GET", f"{api}/users?exact=true&username={urllib.parse.quote(name)}", admin)
        cur = found[0]
        cur_attrs = {k: v for k, v in (cur.get("attributes") or {}).items() if k in spec["user_attributes"]}
        if cur_attrs != rep["attributes"] or any(cur.get(k) != rep[k] for k in
                                                 ("enabled", "firstName", "lastName", "email")):
            call("PUT", f"{api}/users/{cur['id']}", admin, body={**cur, **rep})
            out.append(f"changed: user {name} attributes")
        _, mapped = call("GET", f"{api}/users/{cur['id']}/role-mappings/realm", admin)
        mapped = {r["name"] for r in mapped} & set(spec["roles"])
        add, drop = set(u["roles"]) - mapped, mapped - set(u["roles"])
        if add:
            call("POST", f"{api}/users/{cur['id']}/role-mappings/realm", admin, body=[role_reps[r] for r in sorted(add)])
        if drop:
            call("DELETE", f"{api}/users/{cur['id']}/role-mappings/realm", admin, body=[role_reps[r] for r in sorted(drop)])
        if add or drop:
            out.append(f"changed: user {name} roles {sorted(u['roles'])}")
        if spec["password_grant"] and not token_for(base, realm, spec["portal_client"], name, u["password"]):
            call("PUT", f"{api}/users/{cur['id']}/reset-password", admin,
                 body={"type": "password", "value": u["password"], "temporary": False})
            out.append(f"changed: user {name} password set")

    # Verification: log in as each user and read what the service layer will read
    if spec["password_grant"]:
        for u in spec["users"]:
            tok = token_for(base, realm, spec["portal_client"], u["username"], u["password"])
            if not tok:
                raise Failed(f"verification: {u['username']} cannot log in through {spec['portal_client']}")
            c = claims(tok)
            aud = c.get("aud") if isinstance(c.get("aud"), list) else [c.get("aud")]
            got_roles = sorted(set(c.get("realm_access", {}).get("roles", [])) & set(spec["roles"]))
            problems = []
            # the service and its audit key users by sub (DD-99 note 2026-11-17): it must be the user's stable id
            _, found = call("GET", f"{api}/users?exact=true&username={urllib.parse.quote(u['username'])}", admin)
            if not c.get("sub") or [x["id"] for x in found] != [c.get("sub")]:
                problems.append(f"sub {c.get('sub')!r} is not the user's id")
            if spec["svc_client"] not in aud:
                problems.append(f"aud {aud} lacks {spec['svc_client']}")
            if got_roles != sorted(u["roles"]):
                problems.append(f"roles {got_roles}, expected {sorted(u['roles'])}")
            for k in spec["user_attributes"]:
                if c.get(k) != u.get("attributes", {}).get(k):
                    problems.append(f"claim {k} = {c.get(k)!r}, expected {u.get('attributes', {}).get(k)!r}")
            if problems:
                raise Failed(f"verification: {u['username']}: " + "; ".join(problems))
            out.append(f"verified: {u['username']} roles={','.join(got_roles)} "
                       + " ".join(f"{k}={c.get(k)}" for k in spec["user_attributes"]) + f" iss={c.get('iss')} sub ok")
    print("\n".join(out))


if __name__ == "__main__":
    try:
        main()
    except Failed as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
