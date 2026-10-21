"""Client for the ICP CMS gateway (LD-01 4.2 · 4.7). Standard library only.

The same code talks to the real gateway and to the fake one used in development (AS-32): the only
difference is the address and account in /etc/oss911/icp.env. There is no "synthetic mode" here.

Behaviour the interface document does not state is implemented as the assumption recorded in NOTES.md
("guess N"); each is to be verified during integration testing (IR-45):
  - a failure comes back as HTTP 200 with retcode in the "result" header and the JSON body (guess 8)
  - the token may expire at any time; on 300-008 the token is obtained again and the request retried once,
    and only a second failure is reported (contract auth.on_auth_failure, LD-01 4.7)
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

TOKEN_INVALID = "300-008"           # the token in cmsgateway-token does not exist (any more)
RESULT_RETCODE = re.compile(r"retcode=([^,}\s]+)")
DEFAULT_TIMEOUT = 300               # seconds; a 24-hour CDR download can be large


class IcpError(Exception):
    """The gateway refused or failed a request. Never carries the password or the token."""


@dataclass
class Download:
    body: bytes
    content_type: str
    filename: str | None


class IcpClient:
    def __init__(self, base_url: str, user: str, password: str, auth_path: str, token_header: str,
                 timeout: int = DEFAULT_TIMEOUT):
        self._base = base_url.rstrip("/")
        self._user = user
        self._password = password
        self._auth_path = auth_path
        self._token_header = token_header
        self._timeout = timeout
        self._token: str | None = None
        self.auth_count = 0

    # ---- plumbing ----
    def _post(self, path: str, payload: dict, headers: dict | None = None):
        req = urllib.request.Request(
            self._base + path, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json; charset=UTF-8", **(headers or {})})
        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)   # noqa: S310 -- URL comes from deployment config
        except urllib.error.HTTPError as exc:
            raise IcpError(f"POST {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise IcpError(f"POST {path}: {getattr(exc, 'reason', exc)}") from None
        with resp:
            return resp.status, resp.headers, resp.read()

    @staticmethod
    def _retcode(headers, body: bytes, content_type: str) -> tuple[str | None, str]:
        """retcode and message; from the "result" header, or from a JSON body when there is one."""
        code, message = None, ""
        match = RESULT_RETCODE.search(headers.get("result") or "")
        if match:
            code = match.group(1)
        if "json" in content_type:
            try:
                doc = json.loads(body.decode("utf-8"))
                code = str(doc.get("retcode", code))
                message = str(doc.get("message", ""))
            except (ValueError, UnicodeDecodeError, AttributeError):
                pass
        return code, message

    # ---- authentication ----
    def authenticate(self) -> None:
        self.auth_count += 1
        status, headers, body = self._post(self._auth_path, {"agentId": self._user, "password": self._password})
        try:
            doc = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise IcpError(f"authentication: HTTP {status}, response is not JSON") from None
        if str(doc.get("retcode")) != "0":
            raise IcpError(f"authentication refused: retcode {doc.get('retcode')} {doc.get('message', '')}".strip())
        token = (doc.get("result") or {}).get("token")
        if not isinstance(token, str) or not token:
            raise IcpError("authentication: retcode 0 but no token in the response")
        self._token = token

    # ---- CSV download ----
    def download(self, path: str, payload: dict) -> Download:
        """POST and return the CSV attachment. Retries once with a new token if the token is rejected."""
        for attempt in (1, 2):
            if self._token is None:
                self.authenticate()
            status, headers, body = self._post(path, payload, {self._token_header: self._token})
            ctype = headers.get("Content-Type") or ""
            code, message = self._retcode(headers, body, ctype)
            if code == TOKEN_INVALID and attempt == 1:
                self._token = None
                continue
            if code != "0":
                raise IcpError(f"POST {path}: retcode {code} {message}".strip()
                               if code is not None else f"POST {path}: HTTP {status}, no retcode in the response")
            if "json" in ctype:
                raise IcpError(f"POST {path}: retcode 0 but a JSON body instead of the CSV attachment")
            disposition = headers.get("Content-Disposition") or ""
            fname = re.search(r'filename="?([^";]+)"?', disposition)
            return Download(body=body, content_type=ctype, filename=fname.group(1) if fname else None)
        raise IcpError(f"POST {path}: token rejected twice ({TOKEN_INVALID})")
