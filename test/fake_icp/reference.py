# -*- coding: utf-8 -*-
"""假 ICP 的配置同步接口（synwas 一组，接口文档 p.907–933）。开发期测试工具，不交付，只用标准库。

真实网关的这一组 GET 读的是**网关自己的副本**（上次 CTI / VDN 同步的结果），都是全量、无分页、无增量（DD-94）。
这里同样只有一份「副本」，由测试控制端点改动它，模拟网关做了一次同步：

  bump        升 VDN 版本，内容不变（Q34：内容没变的同步是否也升版本）
  rename      改一个坐席的名称，并升版本
  remove      删一个坐席，并升版本（源端删除 ≠ 历史不存在，core 应置为非当前）
  add         加一个坐席，并升版本
  set_version 把 VDN 版本直接设成某个值（可以设小：版本倒退，DD-94 第十二条）
  status      把 VDN 同步状态设成 0 / 1 / 2 / 3（只在 2 时可取数，DD-94 第三条）
  end_time    把 VDN 同步的 endTime 设成某个时刻（造「副本多日未刷新」，DD-94 第四条）

照文档原样模仿的类型不一致（〇-i）：ccId / vdnId 在数据里是字符串；versions 的 startTime / endTime 是毫秒数字、
status 是数字；versions 的 result 是对象而非数组。接入程序要能对付这些，假接口不替它把类型理顺。
"""
from __future__ import annotations

import copy
import random
from datetime import datetime, timezone

from generator import AGENT_IDS, QUEUE_IDS, IVR_ACCESS, CALLEE

CC_ID, VDN_ID = 1, 1                       # 单一 VDN（AS-30）
PREFIX = "/cmsgateway/resource/synwas/"
DATASETS = ("agents", "groups", "restreasons", "servertypes", "skills", "skillgroups", "calledconfigs")

_NAMES = ["محمد العتيبي", "عبدالله القحطاني", "فهد الحربي", "سعد الغامدي", "خالد الزهراني", "Ahmed Saleh",
          "Omar Farouk", "Yousef Hamdan", "ناصر الشمري", "Faisal Rahman"]


def _ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def initial(seed: int, now: datetime) -> dict:
    """副本的初始内容。只由 seed 决定（now 只用于同步时刻）。"""
    rng = random.Random(f"{seed}:reference")
    cc, vdn = str(CC_ID), str(VDN_ID)
    groups = [{"ccId": cc, "vdnId": vdn, "groupId": str(g), "groupName": f"Shift {chr(64 + g)}",
               "monitorId": str(AGENT_IDS[(g - 1) * 20])} for g in (1, 2, 3)]
    agents = []
    for i, a in enumerate(AGENT_IDS):
        name = rng.choice(_NAMES)
        if i == 7:
            name = ""                       # 文档样例：agentName 可为空串
        elif i == 8:
            name = str(a)                   # 文档样例：agentName 等于工号
        skills = sorted(rng.sample(QUEUE_IDS, rng.randint(1, len(QUEUE_IDS))))
        agents.append({
            "ccId": cc, "vdnId": vdn, "agentNo": str(a), "agentName": name,
            "skillGroupId": None if i == 9 else "1",                       # 文档样例：可为 null
            "skillId": None if i == 9 else ";".join(map(str, skills)),
            "groupId": None if i == 9 else str(i // 20 + 1),
            "roleId": "4;3" if i % 20 == 0 else "4",                       # 组长兼质检员
        })
    skills = [{"ccId": cc, "vdnId": vdn, "skillId": str(q), "skillName": n, "realFlag": "1", "serverId": "1",
               "mediaType": "5"} for q, n in zip(QUEUE_IDS, ("Arabic", "English", "Medical"))]
    skills.append({"ccId": cc, "vdnId": vdn, "skillId": "90", "skillName": "Overflow", "realFlag": "0",
                   "serverId": "1", "mediaType": "5"})                     # 虚队列
    skillgroups = [{"ccId": cc, "vdnId": vdn, "skillGroupId": "1", "skillGroupName": "Emergency",
                    "skillIds": ";".join(map(str, QUEUE_IDS))},
                   {"ccId": cc, "vdnId": vdn, "skillGroupId": "2", "skillGroupName": "Language",
                    "skillIds": f"{QUEUE_IDS[0]};{QUEUE_IDS[1]}"}]        # 技能与技能组多对多
    restreasons = [{"ccId": cc, "vdnId": vdn, "reasonCode": str(c), "reasonInfo": t}
                   for c, t in ((1, "Break"), (2, "Prayer"), (3, "Meeting"), (4, "Training"))]
    servertypes = [{"ccId": cc, "vdnId": vdn, "serviceId": "1", "serviceName": "Emergency voice"}]
    calledconfigs = [
        {"ccId": cc, "vdnId": vdn, "accessCode": CALLEE, "subCode": None, "deviceType": "3",
         "deviceName": "Main IVR", "deviceNo": IVR_ACCESS, "clientLevel": "0"},
        {"ccId": cc, "vdnId": vdn, "accessCode": CALLEE, "subCode": "1", "deviceType": "1",
         "deviceName": "Arabic", "deviceNo": str(QUEUE_IDS[0]), "clientLevel": "0"},
    ]
    end = _ms(now)
    return {
        "vdns": [{"ccId": CC_ID, "vdnId": VDN_ID, "vdnName": "Makkah 911"}],
        "agents": agents, "groups": groups, "restreasons": restreasons, "servertypes": servertypes,
        "skills": skills, "skillgroups": skillgroups, "calledconfigs": calledconfigs,
        "cti": {"version": 6, "startTime": end - 4000, "endTime": end, "status": 2},
        "vdn": {"version": 13, "startTime": end - 9000, "endTime": end, "status": 2},
    }


def versions_body(ref: dict) -> dict:
    cti, vdn = ref["cti"], ref["vdn"]
    cluster = {"clusterId": "1", "clusterName": "cmsgw-1"}
    return {"message": "", "retcode": "0", "result": {
        "ccId": CC_ID, "version": cti["version"], "startTime": cti["startTime"], "endTime": cti["endTime"],
        "status": cti["status"], **cluster,
        "vdnsVersion": [{"vdnId": VDN_ID, "version": vdn["version"], "startTime": vdn["startTime"],
                         "endTime": vdn["endTime"], "status": vdn["status"], **cluster}]}}


def route(ref: dict, path: str) -> tuple[int, dict] | None:
    """GET 路径 → (HTTP 状态, JSON 正文)；不是 synwas 的路径返回 None。"""
    if not path.startswith(PREFIX):
        return None
    rest = path[len(PREFIX):].split("/")
    if rest == ["versions"]:
        return 200, versions_body(ref)
    if rest == ["vdns"]:
        return 200, {"message": "", "retcode": "0", "result": copy.deepcopy(ref["vdns"])}
    if len(rest) == 3 and rest[0] in DATASETS:
        if rest[1:] != [str(CC_ID), str(VDN_ID)]:
            return 200, {"message": "The cc/vdn of the VDN user mismatch.", "retcode": "300-010"}
        return 200, {"message": "", "retcode": "0", "result": copy.deepcopy(ref[rest[0]])}
    return 404, {"message": "not found", "retcode": "000-002"}


def control(ref: dict, body: dict, now: datetime) -> str | None:
    """应用 body["ref"] 里的改动；返回错误说明或 None。"""
    ops = body.get("ref")
    if not isinstance(ops, dict):
        return "ref must be an object"
    vdn = ref["vdn"]

    def bump():
        vdn["version"] += 1
        vdn["startTime"], vdn["endTime"] = _ms(now) - 9000, _ms(now)

    agents = ref["agents"]
    for op, arg in ops.items():
        if op == "bump":
            bump()
        elif op == "rename":
            hit = [a for a in agents if a["agentNo"] == str(arg["agentNo"])]
            if not hit:
                return f"no agent {arg['agentNo']}"
            hit[0]["agentName"] = arg["agentName"]
            bump()
        elif op == "remove":
            if not any(a["agentNo"] == str(arg) for a in agents):
                return f"no agent {arg}"
            ref["agents"] = agents = [a for a in agents if a["agentNo"] != str(arg)]
            bump()
        elif op == "add":
            agents.append({"ccId": str(CC_ID), "vdnId": str(VDN_ID), "agentNo": str(arg["agentNo"]),
                           "agentName": arg.get("agentName", ""), "skillGroupId": "1",
                           "skillId": str(QUEUE_IDS[0]), "groupId": "1", "roleId": "4"})
            bump()
        elif op == "set_version":
            vdn["version"] = int(arg)
        elif op == "status":
            if int(arg) not in (0, 1, 2, 3):
                return "status must be 0, 1, 2 or 3"
            vdn["status"] = int(arg)
        elif op == "end_time":
            vdn["endTime"] = _ms(datetime.strptime(arg, "%Y-%m-%d %H:%M:%S"))
        else:
            return f"unknown ref operation {op!r}"
    return None
