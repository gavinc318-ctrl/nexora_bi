# -*- coding: utf-8 -*-
"""合成话单生成器（AS-32 · NOTES.md「假设登记草稿」第二、三节）。

开发期测试工具，不交付。只用标准库。

核心性质：**同一通呼叫无论被哪个窗口取到，内容都一致**。做法是把时间轴按分钟切开，
每一分钟里有哪些呼叫、各呼叫的全部腿，都只由 (seed, 该分钟) 决定。于是：
  - 相邻两次取数的回看区间重叠时，重叠部分取到的是逐字相同的腿（边界情况 3）
  - 同一窗口在不同时刻取，差别只来自「此刻（now）」：已发生的腿出现、进行中的没有
    -1 腿（边界情况 1）、长通话的结束晚于回看区间（边界情况 4）

时间一律按 UTC 的「无时区」时间处理（照文档猜的行为第 1、2 条）。

腿在什么时刻出现在接口里，文档没写（第 15 条），两种模式都实现，由 emit_mode 选：
  on_leave（默认）  腿在离开设备时才出现，此时它的字段都已齐全；整通结束时最后一条腿
                   即以 CALLIDNUM = -1 出现，不会改号
  on_enter          腿一进入设备就出现，尚未发生的时间字段为空，随时间补齐；整通结束时
                   最后一条腿由正序号改为 -1——同一条腿的幂等键会变，这正是要让接入程序
                   在开发期就遇到的最坏情况
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

EPOCH = datetime(1970, 1, 1)

# 18 列，顺序照接口文档第 1019 页的表头（第 10 条：列序以文档为准）
COLUMNS = [
    "CALLID", "CALLIDNUM", "CALLERNO", "CALLEENO", "WAITBEGIN", "WAITEND", "ACKBEGIN", "ACKEND",
    "CALLBEGIN", "CALLEND", "DEVICETYPE", "DEVICENO", "DEVICEIN", "WAITCAUSE", "RELEASECAUSE",
    "CALLTYPE", "UCENTERREASON", "UCLEAVEREASON",
]
TS_FMT = "%Y-%m-%d %H:%M:%S"

# 取值来自 3rd API/ 下的枚举值表；比例是编的（第 18 条：不得用于口径或性能结论）
DEVICE_QUEUE, DEVICE_AGENT, DEVICE_IVR = 1, 2, 3
CALLTYPE_INBOUND = [0, 0, 0, 0, 0, 0, 0, 0, 5, 13]      # 绝大多数是普通呼入
CALLTYPE_UNKNOWN = 9999                                 # 不在值表内（边界情况 6）
RELEASE_CAUSES = [518, 518, 518, 537, 502]              # 只在末腿上有意义（第 17 条）
QUEUE_IDS = [11, 12, 13]                                # 阿语 / 英语 / 医疗之类的技能队列
AGENT_IDS = list(range(101, 161))                       # VDN 用户工号段（第 903 页）
IVR_ACCESS = "9110"
CALLEE = "911"


@dataclass
class Leg:
    devicetype: int
    deviceno: str
    devicein: str
    waitbegin: datetime
    waitend: datetime
    ackend: datetime | None        # 接通前为 None（排队腿、未接通的坐席腿）
    callend: datetime | None
    waitcause: int
    ucenter: int
    ucleave: int

    @property
    def leave(self) -> datetime:
        """离开该设备的时刻。"""
        return self.callend or self.waitend


@dataclass
class Call:
    callid: str
    callerno: str
    calltype: int
    release: int
    legs: list[Leg] = field(default_factory=list)

    @property
    def end(self) -> datetime:
        return max(l.leave for l in self.legs)


def _ts(dt: datetime | None) -> str:
    return dt.strftime(TS_FMT) if dt else ""


def _minute_calls(seed: int, minute: int) -> list[Call]:
    """某一分钟内开始的全部呼叫。只由 (seed, minute) 决定。"""
    rng = random.Random(f"{seed}:{minute}")
    base = EPOCH + timedelta(minutes=minute)
    n = rng.choices([0, 1, 2, 3, 4, 6], weights=[10, 25, 25, 20, 12, 8])[0]
    calls = []
    for i in range(n):
        # 每分钟第一通有一定概率恰好在 :00 开始，用来制造「腿恰在窗口边界上」（边界情况 8）
        sec = 0 if (i == 0 and rng.random() < 0.15) else rng.randint(0, 59)
        t0 = base + timedelta(seconds=sec)
        epoch_s = int((t0 - EPOCH).total_seconds())
        call = Call(
            callid=f"{epoch_s}-{i + 1}",                # 格式照示例「秒级时间戳-序号」（第 19 条）
            callerno="05" + "".join(str(rng.randint(0, 9)) for _ in range(8)),
            calltype=rng.choice(CALLTYPE_INBOUND),
            release=rng.choice(RELEASE_CAUSES),
        )
        # IVR 腿
        ivr_end = t0 + timedelta(seconds=rng.randint(3, 25))
        call.legs.append(Leg(DEVICE_IVR, IVR_ACCESS, IVR_ACCESS, t0, ivr_end, ivr_end, ivr_end, 0, 0, 0))
        # 排队腿
        q = rng.choice(QUEUE_IDS)
        wait = rng.choice([rng.randint(2, 30)] * 6 + [rng.randint(30, 240)])
        q_end = ivr_end + timedelta(seconds=wait)
        call.legs.append(Leg(DEVICE_QUEUE, str(q), "DEVICE_QUEUE", ivr_end, q_end, None, None, 2, 0, 0))
        if rng.random() < 0.08:                          # 排队中放弃：只有 IVR 与排队两条腿
            calls.append(call)
            continue
        # 坐席腿；少数转接到第二位坐席；少数是超过回看长度的长通话（边界情况 4）
        ring = rng.randint(1, 8)
        talk = rng.randint(30, 400) if rng.random() > 0.05 else rng.randint(16 * 60, 45 * 60)
        agent = rng.choice(AGENT_IDS)
        a_wait_end = q_end + timedelta(seconds=ring)
        a_end = a_wait_end + timedelta(seconds=talk)
        call.legs.append(Leg(DEVICE_AGENT, str(agent), "DEVICE_AGENT", q_end, a_wait_end,
                             a_wait_end, a_end, 0, 0, 0))
        if rng.random() < 0.06:
            agent2 = rng.choice([a for a in AGENT_IDS if a != agent])
            b_wait_end = a_end + timedelta(seconds=rng.randint(2, 10))
            b_end = b_wait_end + timedelta(seconds=rng.randint(30, 300))
            call.legs.append(Leg(DEVICE_AGENT, str(agent2), "DEVICE_AGENT", a_end, b_wait_end,
                                 b_wait_end, b_end, 0, 0, 0))
        calls.append(call)
    return calls


def _rows_for_call(call: Call, now: datetime, emit_mode: str) -> list[dict]:
    """此刻（now）在接口里能看到的这通呼叫的腿。"""
    ended = call.end <= now
    rows = []
    legs = call.legs
    for idx, leg in enumerate(legs, 1):
        is_last = idx == len(legs)
        if emit_mode == "on_leave":
            if leg.leave > now:
                continue
            seq = -1 if (is_last and ended) else idx
            vis = lambda t: t                                         # noqa: E731
        else:  # on_enter
            if leg.waitbegin > now:
                continue
            seq = -1 if (is_last and ended) else idx
            vis = lambda t: t if (t is not None and t <= now) else None  # noqa: E731
        waitend = vis(leg.waitend)
        ackend = vis(leg.ackend)
        callend = vis(leg.callend)
        rows.append({
            "CALLID": call.callid,
            "CALLIDNUM": str(seq),
            "CALLERNO": call.callerno,
            "CALLEENO": CALLEE,
            "WAITBEGIN": _ts(leg.waitbegin),
            "WAITEND": _ts(waitend),
            "ACKBEGIN": _ts(waitend),                 # 文档：AckBegin = WaitEnd
            "ACKEND": _ts(ackend),
            "CALLBEGIN": _ts(ackend),                 # 文档：CallBegin = AckEnd
            "CALLEND": _ts(callend),
            "DEVICETYPE": str(leg.devicetype),
            "DEVICENO": leg.deviceno,
            "DEVICEIN": leg.devicein,
            "WAITCAUSE": str(leg.waitcause),
            "RELEASECAUSE": str(call.release if seq == -1 else 0),   # 非末腿填 0（第 17 条）
            "CALLTYPE": str(call.calltype),
            "UCENTERREASON": str(leg.ucenter),
            "UCLEAVEREASON": str(leg.ucleave),
        })
    return rows


def rows_in_window(seed: int, start: datetime, end: datetime, now: datetime,
                   emit_mode: str = "on_leave") -> list[dict]:
    """WAITBEGIN 落在 [start, end) 内、且此刻可见的全部腿（窗口边界按半开区间，第 6 条）。

    一通呼叫的各条腿可能分属不同窗口——按 WAITBEGIN 过滤的是腿，不是呼叫（LD-01 4.4）。
    最长的呼叫约 50 分钟，故从窗口起点往前多看 60 分钟内开始的呼叫。
    """
    first_min = int((start - EPOCH).total_seconds() // 60) - 60
    last_min = int((end - EPOCH).total_seconds() // 60)
    out = []
    for m in range(first_min, last_min + 1):
        for call in _minute_calls(seed, m):
            for r in _rows_for_call(call, now, emit_mode):
                wb = datetime.strptime(r["WAITBEGIN"], TS_FMT)
                if start <= wb < end:
                    out.append(r)
    return out
