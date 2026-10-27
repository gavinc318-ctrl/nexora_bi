# -*- coding: utf-8 -*-
"""假 ICP 接口服务的自测。验的是假接口自己：它照文档守住了约束，也真能造出边界情况。

运行：python3 test/fake_icp/test_fake_icp.py -v
（不要用 python3 -m unittest test/...：仓库的 test/ 目录名与标准库的 test 包重名，会导入失败）
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generator as gen   # noqa: E402
import server as srv      # noqa: E402

ACCOUNT, PASSWORD = "900", "test-only"
T = gen.TS_FMT


def ts(dt: datetime) -> str:
    return dt.strftime(T)


class FakeICPTest(unittest.TestCase):

    def setUp(self):
        self.state = srv.State(seed=7, accounts={ACCOUNT: PASSWORD}, token_ttl=600, emit_mode="on_leave")
        self.httpd = srv.make_server("127.0.0.1", 0, self.state)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.control(now="2026-10-14 12:00:00")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # ---------- 工具 ----------
    def post(self, path, body, token=None):
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json; charset=UTF-8"})
        if token:
            req.add_header("cmsgateway-token", token)
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8")

    def control(self, **kw):
        return self.post("/_fake/control", kw)

    def token(self):
        _, _, body = self.post(srv.AUTH_PATH, {"agentId": ACCOUNT, "password": PASSWORD})
        return json.loads(body)["result"]["token"]

    def cdr(self, start, end, token=None, **extra):
        body = {"ccId": 1, "vdnId": 1, "startTime": start, "endTime": end, **extra}
        return self.post(srv.CDR_PATH, body, token or self.token())

    def rows(self, start, end, **kw):
        status, headers, body = self.cdr(start, end, **kw)
        self.assertEqual(headers.get("Content-Type"), "application/csv;charset=UTF-8", body[:200])
        return list(csv.DictReader(io.StringIO(body)))

    def retcode(self, resp):
        return json.loads(resp[2])["retcode"]

    # ---------- 鉴权 ----------
    def test_auth_ok_and_failures(self):
        _, h, b = self.post(srv.AUTH_PATH, {"agentId": ACCOUNT, "password": PASSWORD})
        self.assertEqual(json.loads(b)["retcode"], "0")
        self.assertEqual(h["result"], "{message=, retcode=0}")
        self.assertEqual(self.retcode(self.post(srv.AUTH_PATH, {"agentId": ACCOUNT, "password": "x"})), "300-011")
        self.assertEqual(self.retcode(self.post(srv.AUTH_PATH, {"agentId": 900})), "300-002")

    def test_cdr_requires_valid_token(self):
        self.assertEqual(self.retcode(self.cdr("2026-10-14 08:00:00", "2026-10-14 08:05:00", token="nope")), "300-008")
        tok = self.token()
        self.control(expire_tokens=True)
        self.assertEqual(self.retcode(self.cdr("2026-10-14 08:00:00", "2026-10-14 08:05:00", token=tok)), "300-008")

    def test_token_expires_by_ttl(self):
        self.control(token_ttl=60)
        tok = self.token()
        self.control(now="2026-10-14 12:02:00")
        self.assertEqual(self.retcode(self.cdr("2026-10-14 08:00:00", "2026-10-14 08:05:00", token=tok)), "300-008")

    def test_reauth_keeps_old_token(self):                       # 猜 11
        t1 = self.token()
        self.token()
        self.rows("2026-10-14 08:00:00", "2026-10-14 08:05:00", token=t1)

    # ---------- 请求约束 ----------
    def test_window_limits_and_format(self):
        tok = self.token()
        bad = [("2026-10-14 00:00:00", "2026-10-15 00:00:01"),   # > 24h
               ("2026-10-14 00:00:00", "2026-10-14 00:00:04"),   # < 5s
               ("2026-10-14 00:00:05", "2026-10-14 00:00:00"),   # 倒序
               ("2026-10-14 8:00:00", "2026-10-14 09:00:00")]    # 不补零（猜 5）
        for s, e in bad:
            self.assertEqual(self.retcode(self.cdr(s, e, token=tok)), "000-002", (s, e))
        self.rows("2026-10-14 00:00:00", "2026-10-15 00:00:00", token=tok)   # 恰好 24h 可以
        self.rows("2026-10-14 00:00:00", "2026-10-14 00:00:05", token=tok)   # 恰好 5s 可以
        self.assertEqual(self.retcode(self.cdr("2026-10-14 08:00:00", "2026-10-14 08:05:00", token=tok, vdnId=2)), "200-001")

    def test_only_post(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + srv.CDR_PATH)
        self.assertEqual(cm.exception.code, 405)

    def test_concurrent_download_rejected(self):
        self.control(delay_seconds=1.0)
        tok = self.token()
        results = []
        def go():
            results.append(self.cdr("2026-10-14 08:00:00", "2026-10-14 08:05:00", token=tok))
        ts_ = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts_]; [t.join() for t in ts_]
        kinds = sorted("csv" if r[1].get("Content-Type", "").startswith("application/csv") else self.retcode(r)
                       for r in results)
        self.assertEqual(kinds, ["000-002", "csv"])
        self.assertEqual(self.state.calls["cdr_rejected_concurrent"], 1)

    def test_injected_failure(self):
        self.control(fail_next=["300-015"])
        self.assertEqual(self.retcode(self.cdr("2026-10-14 08:00:00", "2026-10-14 08:05:00")), "300-015")
        self.rows("2026-10-14 08:00:00", "2026-10-14 08:05:00")            # 只失败一次

    # ---------- 响应形态 ----------
    def test_csv_shape(self):
        status, h, body = self.cdr("2026-10-14 08:00:00", "2026-10-14 09:00:00")
        self.assertEqual(h["result"], "{message=get report scuccess, retcode=0}")
        self.assertIn("attachment; filename=bill_2026-10-14 08:00:00_2026-10-14 09:00:00.csv", h["Content-Disposition"])
        self.assertFalse(body.startswith("﻿"))
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0], gen.COLUMNS)
        self.assertTrue(len(rows) > 10)
        self.assertTrue(all(len(r) == 18 for r in rows))

    def test_empty_result_is_header_only(self):                 # 猜 9：窗口在「此刻」之后
        _, _, body = self.cdr("2026-10-14 13:00:00", "2026-10-14 14:00:00")
        self.assertEqual(body.strip(), ",".join(gen.COLUMNS))

    def test_order_is_shuffled(self):                           # 第一节第 4 条
        rows = self.rows("2026-10-14 08:00:00", "2026-10-14 10:00:00")
        by_callend = sorted(rows, key=lambda r: r["CALLEND"], reverse=True)
        self.assertNotEqual([r["CALLID"] + r["CALLIDNUM"] for r in rows],
                            [r["CALLID"] + r["CALLIDNUM"] for r in by_callend])

    # ---------- 生成器的性质 ----------
    def key(self, r):
        return (r["CALLID"], r["CALLIDNUM"])

    def test_deterministic_and_overlap_consistent(self):        # 边界情况 3
        a = {self.key(r): r for r in self.rows("2026-10-14 08:00:00", "2026-10-14 08:20:00")}
        a2 = {self.key(r): r for r in self.rows("2026-10-14 08:00:00", "2026-10-14 08:20:00")}
        self.assertEqual(a, a2)
        b = {self.key(r): r for r in self.rows("2026-10-14 08:05:00", "2026-10-14 08:25:00")}
        common = set(a) & set(b)
        self.assertTrue(common)
        for k in common:
            self.assertEqual(a[k], b[k])

    def test_half_open_window(self):                            # 猜 6 · 边界情况 8
        rows = self.rows("2026-10-14 06:00:00", "2026-10-14 10:00:00")
        on_minute = [r for r in rows if r["WAITBEGIN"].endswith(":00")]
        self.assertTrue(on_minute, "生成器应能造出恰在整分上开始的腿")
        r = on_minute[0]
        wb = datetime.strptime(r["WAITBEGIN"], T)
        inside = {self.key(x) for x in self.rows(ts(wb), ts(wb + timedelta(minutes=1)))}
        before = {self.key(x) for x in self.rows(ts(wb - timedelta(minutes=1)), ts(wb))}
        self.assertIn(self.key(r), inside)
        self.assertNotIn(self.key(r), before)

    def test_in_progress_call_has_no_final_leg_then_gets_it(self):   # 边界情况 1
        # 找一通含坐席腿、还在通话中的呼叫：此刻能看到前几条腿，看不到 -1
        start, end = "2026-10-14 11:30:00", "2026-10-14 12:00:00"
        rows = self.rows(start, end)
        by_call = {}
        for r in rows:
            by_call.setdefault(r["CALLID"], []).append(r["CALLIDNUM"])
        open_calls = [c for c, nums in by_call.items() if "-1" not in nums]
        self.assertTrue(open_calls, "此刻附近应有尚未结束的呼叫")
        self.control(now="2026-10-14 13:30:00")
        later = {}
        for r in self.rows(start, end):
            later.setdefault(r["CALLID"], []).append(r["CALLIDNUM"])
        finished = [c for c in open_calls if "-1" in later.get(c, [])]
        self.assertTrue(finished, "时间推后，原先进行中的呼叫应出现 -1 腿")
        for c in finished:                                      # on_leave 模式下已出现的腿不改号
            self.assertTrue(set(by_call[c]) - {"-1"} <= set(later[c]))

    def test_long_calls_exist(self):                            # 边界情况 4
        rows = self.rows("2026-10-14 00:00:00", "2026-10-14 12:00:00")
        long_ = [r for r in rows if r["CALLBEGIN"] and r["CALLEND"]
                 and datetime.strptime(r["CALLEND"], T) - datetime.strptime(r["CALLBEGIN"], T) > timedelta(minutes=15)]
        self.assertTrue(long_, "应有超过 15 分钟回看长度的长通话")

    def test_on_enter_mode_blank_fields_and_renumbering(self):  # 猜 15
        self.control(emit_mode="on_enter", now="2026-10-14 12:00:00")
        rows = self.rows("2026-10-14 11:00:00", "2026-10-14 12:00:00")
        self.assertTrue(any(r["WAITBEGIN"] and not r["WAITEND"] for r in rows), "进入设备即出现，等待结束前 WAITEND 为空")
        open_keys = {(r["CALLID"], r["WAITBEGIN"], r["DEVICENO"]): r["CALLIDNUM"] for r in rows}
        self.control(now="2026-10-14 14:00:00")
        later = {(r["CALLID"], r["WAITBEGIN"], r["DEVICENO"]): r["CALLIDNUM"]
                 for r in self.rows("2026-10-14 11:00:00", "2026-10-14 12:00:00")}
        renumbered = [k for k, n in open_keys.items() if n != "-1" and later.get(k) == "-1"]
        self.assertTrue(renumbered, "on_enter 模式下末腿应由正序号改为 -1——同一条腿的幂等键会变")

    # ---------- 边界数据注入 ----------
    def test_injections_apply_once(self):
        s, e = "2026-10-14 08:00:00", "2026-10-14 08:30:00"
        self.control(inject=["unknown_calltype", "null_waitbegin", "dup_callidnum", "comma_in_callerno", "inversion"])
        rows = self.rows(s, e)
        self.assertTrue(any(r["CALLTYPE"] == str(gen.CALLTYPE_UNKNOWN) for r in rows))
        self.assertTrue(any(r["WAITBEGIN"] == "" for r in rows))
        keys = [self.key(r) for r in rows]
        self.assertNotEqual(len(keys), len(set(keys)), "应有重复的 (CALLID, CALLIDNUM)")
        self.assertTrue(any("," in r["CALLERNO"] for r in rows), "含逗号的字段应能经 CSV 引号原样读回")
        inverted = [r for r in rows
                    if (r["CALLBEGIN"] and r["CALLEND"] and r["CALLEND"] < r["CALLBEGIN"])
                    or (r["WAITEND"] and r["WAITBEGIN"] and r["WAITEND"] < r["WAITBEGIN"])]
        self.assertTrue(inverted, "应有时序倒置的行")
        clean = self.rows(s, e)                                 # 注入只用一次
        self.assertFalse(any(r["CALLTYPE"] == str(gen.CALLTYPE_UNKNOWN) or r["WAITBEGIN"] == "" for r in clean))

    def test_inversion_always_inverts(self):
        # 曾经对调 CALLBEGIN 与 CALLEND：IVR 腿两者相等，对调后不变，注入静默失效。逐个窗口查，不靠碰巧
        for h in range(0, 12):                                  # setUp 把「此刻」定在 12:00，只取之前的窗口
            self.control(inject=["inversion"])
            rows = self.rows(f"2026-10-14 {h:02d}:00:00", f"2026-10-14 {h:02d}:10:00")
            self.assertTrue(any(r["WAITEND"] and r["WAITEND"] < r["WAITBEGIN"] for r in rows), f"{h} 点的窗口没有倒置行")

    def test_unknown_injection_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.control(inject=["no_such_thing"])
        self.assertEqual(cm.exception.code, 400)

    # ---------- 配置同步（synwas）----------
    def get(self, path, token=None):
        req = urllib.request.Request(self.base + path, method="GET")
        if token:
            req.add_header("cmsgateway-token", token)
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_synwas_requires_token_and_vdn(self):
        self.assertEqual(self.get("/cmsgateway/resource/synwas/versions")["retcode"], "300-008")
        t = self.token()
        self.assertEqual(self.get("/cmsgateway/resource/synwas/agents/1/2", t)["retcode"], "300-010")
        self.assertEqual(self.get("/cmsgateway/resource/synwas/agents/1/1", t)["retcode"], "0")

    def test_synwas_shapes_follow_the_document(self):           # 〇-i：类型不一致照文档原样
        t = self.token()
        v = self.get("/cmsgateway/resource/synwas/versions", t)["result"]
        self.assertIsInstance(v, dict)                          # 文档标 array，样例是对象
        self.assertIsInstance(v["vdnsVersion"][0]["endTime"], int)
        agents = self.get("/cmsgateway/resource/synwas/agents/1/1", t)["result"]
        self.assertIsInstance(agents[0]["ccId"], str)
        self.assertTrue(any(a["agentName"] == "" for a in agents))
        self.assertTrue(any(a["skillId"] is None for a in agents))
        self.assertTrue(any(";" in (a["skillId"] or "") for a in agents))
        for ds in ("groups", "restreasons", "servertypes", "skills", "skillgroups", "calledconfigs"):
            self.assertTrue(self.get(f"/cmsgateway/resource/synwas/{ds}/1/1", t)["result"], ds)

    def test_synwas_control(self):
        t = self.token()
        ver = lambda: self.get("/cmsgateway/resource/synwas/versions", t)["result"]["vdnsVersion"][0]  # noqa: E731
        v0 = ver()["version"]
        self.control(ref={"rename": {"agentNo": "101", "agentName": "X"}})
        self.assertEqual(ver()["version"], v0 + 1)
        agents = self.get("/cmsgateway/resource/synwas/agents/1/1", t)["result"]
        self.assertEqual([a["agentName"] for a in agents if a["agentNo"] == "101"], ["X"])
        self.control(ref={"remove": "102"})
        agents = self.get("/cmsgateway/resource/synwas/agents/1/1", t)["result"]
        self.assertFalse(any(a["agentNo"] == "102" for a in agents))
        self.control(ref={"set_version": v0, "status": 1})
        self.assertEqual((ver()["version"], ver()["status"]), (v0, 1))
        with self.assertRaises(urllib.error.HTTPError):
            self.control(ref={"remove": "no-such-agent"})


if __name__ == "__main__":
    unittest.main()
