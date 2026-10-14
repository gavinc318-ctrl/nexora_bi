# -*- coding: utf-8 -*-
"""假 ICP 接口服务（AS-32）。开发期测试工具，不交付，只用标准库。

照接口文档实现与真实接口相同的请求与响应，接入程序按正常路径调用它，与调用真实接口的
唯一差别是部署时注入的地址与账号。接入程序里不得有任何「合成模式」分支。

真实接口的两个端点（照文档）：
  POST /cmsgateway/resource/userauth/auth                 鉴权，第 905 页
  POST /cmsgateway/resource/reports/vdnvoicecalldetail    话单下载，第 1019–1020 页

另有两个**只供测试用**的端点，真实接口没有，接入程序永远不调：
  POST /_fake/control    调整「此刻」、token 有效期，注入故障与边界数据
  GET  /_fake/state      查看当前设置与调用计数

照文档猜的行为（NOTES.md 草稿第三节，每条都是联调核实项）在代码里以「猜 N」标出。

用法：
  python3 test/fake_icp/server.py --port 18080 --seed 1 --account <工号>:<口令>
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import generator as gen

AUTH_PATH = "/cmsgateway/resource/userauth/auth"
CDR_PATH = "/cmsgateway/resource/reports/vdnvoicecalldetail"
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")   # 只接受补零格式（猜 5）


class State:
    """服务的全部可变状态。所有访问都在 lock 下。"""

    def __init__(self, seed: int, accounts: dict[str, str], token_ttl: int, emit_mode: str):
        self.lock = threading.Lock()
        self.download_lock = threading.Lock()     # 同一时刻只服务一个下载（文档：不支持并发）
        self.seed = seed
        self.accounts = accounts
        self.token_ttl = token_ttl                # 秒；开发期设短，让鉴权重取路径常被走到（猜 11）
        self.emit_mode = emit_mode                # on_leave / on_enter（猜 15）
        self.now_override: datetime | None = None
        self.tokens: dict[str, datetime] = {}     # token -> 过期时刻
        self.delay_seconds = 0.0                  # 每次下载人为延时，用来制造并发冲突
        self.fail_next: list[str] = []            # 下面几次下载直接返回这些 retcode
        self.inject: list[str] = []               # 下一次下载要混入的边界数据，用一次即清
        self.calls = {"auth": 0, "cdr": 0, "cdr_rejected_concurrent": 0}

    def now(self) -> datetime:
        return self.now_override or datetime.now(timezone.utc).replace(tzinfo=None)   # 按 UTC（猜 1、2）


def _result_header(retcode: str, message: str) -> str:
    # 文档第 1019 页原样：result → {message=get report scuccess, retcode=0}
    # 是 Java Map 的文本形式，不是 JSON；拼写错误「scuccess」也照抄
    return "{message=%s, retcode=%s}" % (message, retcode)


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-icp/0.1"
    state: State          # 由 make_server 注入

    # ---------- 工具 ----------
    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        if "retcode" in body:
            self.send_header("result", _result_header(body["retcode"], body.get("message", "")))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _fail(self, retcode: str, message: str) -> None:
        # 失败时 HTTP 仍为 200，result 头与 JSON 正文都带 retcode，不带 CSV（猜 8）
        self._json(200, {"message": message, "retcode": retcode})

    def _read_json(self) -> dict | None:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            v = json.loads(raw.decode("utf-8") or "{}")
            return v if isinstance(v, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    def log_message(self, fmt, *args):       # 安静；测试里不刷屏
        pass

    # ---------- 非 POST 一律拒绝（文档：只支持 POST）----------
    def do_GET(self):
        if self.path == "/_fake/state":
            st = self.state
            with st.lock:
                body = {"seed": st.seed, "emit_mode": st.emit_mode, "token_ttl": st.token_ttl,
                        "now": st.now().strftime(gen.TS_FMT), "now_overridden": st.now_override is not None,
                        "delay_seconds": st.delay_seconds, "fail_next": list(st.fail_next),
                        "inject": list(st.inject), "calls": dict(st.calls)}
            return self._json(200, body)
        self._json(405, {"message": "method not allowed", "retcode": "000-002"})

    do_PUT = do_DELETE = do_PATCH = lambda self: self._json(405, {"message": "method not allowed",
                                                                  "retcode": "000-002"})

    def do_POST(self):
        if self.path == AUTH_PATH:
            return self._auth()
        if self.path == CDR_PATH:
            return self._cdr()
        if self.path == "/_fake/control":
            return self._control()
        self._json(404, {"message": "not found", "retcode": "000-002"})

    # ---------- 鉴权 ----------
    def _auth(self):
        st = self.state
        with st.lock:
            st.calls["auth"] += 1
        body = self._read_json()
        if body is None or not isinstance(body.get("agentId"), str) or not isinstance(body.get("password"), str):
            return self._fail("300-002", "Parameters carried in the user login request are incorrect.")
        with st.lock:
            if st.accounts.get(body["agentId"]) != body["password"]:
                return self._fail("300-011", "The agent ID and password do not match each other.")
            token = str(uuid.uuid4())
            st.tokens[token] = st.now() + timedelta(seconds=st.token_ttl)
            # 重复鉴权不使旧 token 失效（猜 11）
        self._json(200, {"message": "", "retcode": "0", "result": {"token": token}})

    # ---------- 话单下载 ----------
    def _cdr(self):
        st = self.state
        with st.lock:
            st.calls["cdr"] += 1
        token = self.headers.get("cmsgateway-token")
        with st.lock:
            exp = st.tokens.get(token or "")
            if exp is None or exp <= st.now():
                st.tokens.pop(token or "", None)
                return self._fail("300-008", "The token specified by cmsgateway-token in the HTTP request does not exist.")
            if st.fail_next:
                code = st.fail_next.pop(0)
                return self._fail(code, "injected failure")
        body = self._read_json()
        if body is None:
            return self._fail("000-002", "The interface parameters are invalid.")
        if body.get("vdnId", 1) != 1 or body.get("ccId", 1) != 1:
            return self._fail("200-001", "The VDN does not exist.")     # 单一 VDN（AS-30）
        s, e = body.get("startTime"), body.get("endTime")
        if not (isinstance(s, str) and isinstance(e, str) and TIME_RE.match(s) and TIME_RE.match(e)):
            return self._fail("000-002", "startTime / endTime must be yyyy-MM-dd HH:mm:ss.")
        start, end = datetime.strptime(s, gen.TS_FMT), datetime.strptime(e, gen.TS_FMT)
        span = (end - start).total_seconds()
        if span < 5 or span > 24 * 3600:
            # 文档只说上下限，没有专门错误码（猜 4）
            return self._fail("000-002", "The interval must be at least 5 seconds and at most 24 hours.")

        # 不支持并发：第二个同时到达的下载直接拒绝（猜 3：真实平台可能排队、超时或返回残缺数据）
        if not st.download_lock.acquire(blocking=False):
            with st.lock:
                st.calls["cdr_rejected_concurrent"] += 1
            return self._fail("000-002", "Concurrent download is not supported.")
        try:
            with st.lock:
                delay, now, seed, mode = st.delay_seconds, st.now(), st.seed, st.emit_mode
                inject, st.inject = st.inject, []
            if delay:
                threading.Event().wait(delay)
            rows = gen.rows_in_window(seed, start, end, now, mode)
            rows = _apply_injections(rows, inject, start)
            # 故意打乱顺序：文档说按 CALLEND 降序，契约写明不得依赖顺序（第一节第 4 条）
            random.Random(f"{seed}:{s}:{e}:order").shuffle(rows)
            self._send_csv(rows, s, e)
        finally:
            st.download_lock.release()

    def _send_csv(self, rows: list[dict], s: str, e: str) -> None:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=gen.COLUMNS, lineterminator="\n")   # RFC 4180 引号（猜 10）
        w.writeheader()                                                       # 空结果也有表头（猜 9）
        w.writerows(rows)
        data = buf.getvalue().encode("utf-8")                                 # 无 BOM（猜 10）
        self.send_response(200)
        self.send_header("Content-Type", "application/csv;charset=UTF-8")
        self.send_header("Content-Disposition", f"attachment; filename=bill_{s}_{e}.csv")
        self.send_header("result", _result_header("0", "get report scuccess"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------- 测试控制 ----------
    def _control(self):
        body = self._read_json()
        if body is None:
            return self._json(400, {"error": "body must be a JSON object"})
        st = self.state
        with st.lock:
            if "now" in body:
                st.now_override = datetime.strptime(body["now"], gen.TS_FMT) if body["now"] else None
            if "token_ttl" in body:
                st.token_ttl = int(body["token_ttl"])
            if body.get("expire_tokens"):
                st.tokens.clear()
            if "delay_seconds" in body:
                st.delay_seconds = float(body["delay_seconds"])
            if "fail_next" in body:
                st.fail_next = [str(x) for x in body["fail_next"]]
            if "inject" in body:
                bad = set(body["inject"]) - set(INJECTIONS)
                if bad:
                    return self._json(400, {"error": f"unknown injection: {sorted(bad)}",
                                            "known": sorted(INJECTIONS)})
                st.inject = list(body["inject"])
            if "emit_mode" in body:
                if body["emit_mode"] not in ("on_leave", "on_enter"):
                    return self._json(400, {"error": "emit_mode must be on_leave or on_enter"})
                st.emit_mode = body["emit_mode"]
        self._json(200, {"ok": True})


# ---------- 边界数据注入（NOTES.md 草稿第二节）----------
def _inject_inversion(rows, start):           # 边界情况 2：时序倒置
    if rows:
        r = dict(rows[0])
        if r["CALLEND"] and r["CALLBEGIN"]:
            r["CALLEND"], r["CALLBEGIN"] = r["CALLBEGIN"], r["CALLEND"]
        else:
            r["WAITEND"] = (datetime.strptime(r["WAITBEGIN"], gen.TS_FMT) - timedelta(seconds=5)).strftime(gen.TS_FMT)
        rows[0] = r
    return rows


def _inject_null_waitbegin(rows, start):      # 边界情况 5：WAITBEGIN 为空
    r = {c: "" for c in gen.COLUMNS}
    r.update({"CALLID": "0-0", "CALLIDNUM": "1", "CALLEENO": "911", "DEVICETYPE": "1", "CALLTYPE": "0"})
    rows.append(r)
    return rows


def _inject_unknown_calltype(rows, start):    # 边界情况 6：CALLTYPE 不在值表内
    if rows:
        rows[0] = dict(rows[0], CALLTYPE=str(gen.CALLTYPE_UNKNOWN))
    return rows


def _inject_dup_callidnum(rows, start):       # 边界情况 7：同一 CALLID 内 CALLIDNUM 重复
    if rows:
        rows.append(dict(rows[0], DEVICENO="999"))
    return rows


def _inject_comma_in_callerno(rows, start):   # 第 10 条：主叫可为邮件或网页地址，可能含逗号
    if rows:
        rows[0] = dict(rows[0], CALLERNO="web:https://example.invalid/a,b")
    return rows


INJECTIONS = {
    "inversion": _inject_inversion,
    "null_waitbegin": _inject_null_waitbegin,
    "unknown_calltype": _inject_unknown_calltype,
    "dup_callidnum": _inject_dup_callidnum,
    "comma_in_callerno": _inject_comma_in_callerno,
}


def _apply_injections(rows, names, start):
    for n in names:
        rows = INJECTIONS[n](rows, start)
    return rows


def make_server(host: str, port: int, state: State) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer((host, port), handler)


def main():
    ap = argparse.ArgumentParser(description="假 ICP 接口服务（开发期测试工具，不交付）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--account", action="append", required=True,
                    help="agentId:password，可重复；必须显式给出——仓库里不留任何口令，哪怕是假接口的")
    ap.add_argument("--token-ttl", type=int, default=600)
    ap.add_argument("--emit-mode", choices=["on_leave", "on_enter"], default="on_leave")
    a = ap.parse_args()
    accounts = dict(x.split(":", 1) for x in a.account)
    srv = make_server(a.host, a.port, State(a.seed, accounts, a.token_ttl, a.emit_mode))
    print(f"fake-icp listening on http://{a.host}:{a.port}  seed={a.seed} emit_mode={a.emit_mode}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
