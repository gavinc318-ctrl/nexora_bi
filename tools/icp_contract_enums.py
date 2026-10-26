#!/usr/bin/env python3
"""契约 if03a 里的两张值表与 3rd API/ 下 CSV 的一致性（开发工具，不交付）。

运行期只读契约（库内副本）；CSV 是从接口文档生成的开发材料、不随系统部署。两处必须一致，
所以由本脚本生成契约里的那一段、并核对（DD-91 第 5 条）。

  python3 tools/icp_contract_enums.py --check     # 核对：契约与 CSV 不一致即退出码 1
  python3 tools/icp_contract_enums.py --print     # 打印 release_cause.classes 段，供写进契约

核对两项：
  enums.call_type.values         与 3rd API/icp_call_type.csv 的 id 列逐个相同
  enums.release_cause.classes    与 3rd API/icp_release_cause.csv 的（cause_code, release_source）逐个相同
"""
import csv
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
CONTRACT = BASE / "contracts" / "if03a_icp_cdr.yaml"
CALL_TYPE = BASE / "3rd API" / "icp_call_type.csv"
RELEASE = BASE / "3rd API" / "icp_release_cause.csv"
CLASSES = ("agent", "caller", "system")


def csv_classes():
    out = {c: [] for c in CLASSES}
    with open(RELEASE, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["release_source"]].append(int(r["cause_code"]))
    return {c: sorted(v) for c, v in out.items()}


def print_block():
    cls = csv_classes()
    print("    classes:                # 由 tools/icp_contract_enums.py 从 3rd API/icp_release_cause.csv 生成，勿手改；"
          "运行期以此为准（DD-91 第 5 条）")
    for c in CLASSES:
        codes = cls[c]
        print(f"      {c}:")
        items = [str(x) for x in codes]
        # 每行约 16 个码，便于 diff
        rows = [items[i:i + 16] for i in range(0, len(items), 16)]
        print("        [" + ",\n         ".join(", ".join(r) for r in rows) + "]")
    return 0


def check():
    doc = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    problems = []
    with open(CALL_TYPE, encoding="utf-8") as fh:
        want = [int(r["id"]) for r in csv.DictReader(fh)]
    got = doc["enums"]["call_type"].get("values")
    if got != want:
        problems.append(f"enums.call_type.values 与 {CALL_TYPE.name} 不一致")
    got_cls = (doc["enums"]["release_cause"] or {}).get("classes")
    want_cls = csv_classes()
    if not isinstance(got_cls, dict):
        problems.append("enums.release_cause.classes 不存在")
    else:
        for c in CLASSES:
            if sorted(got_cls.get(c) or []) != want_cls[c]:
                problems.append(f"enums.release_cause.classes.{c} 与 {RELEASE.name} 不一致")
        extra = set(got_cls) - set(CLASSES)
        if extra:
            problems.append(f"enums.release_cause.classes 有多余的类：{sorted(extra)}")
    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        n = sum(len(v) for v in want_cls.values())
        print(f"一致：call_type {len(want)} 个值，release_cause {n} 个码（{', '.join(f'{c} {len(want_cls[c])}' for c in CLASSES)}）")
    return 1 if problems else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--print"]:
        sys.exit(print_block())
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__, file=sys.stderr)
    sys.exit(2)
