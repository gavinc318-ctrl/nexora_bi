# 由 infra/playbooks/30-python.yml 以 python3 -c 内联执行（不部署为文件：airflow 用户读不到仓库）。
# 从 `airflow pools list -o json` 的输出中解析 pool 列表，逐行打印「名称|槽位|说明」。
#
# 不能简单地从第一个「[」开始解析：Airflow 3 的 pools 子命令已标为过时（改用 airflowctl），
# 输出前面可能有日志行，而日志行本身也以「[时间]」开头。故逐个尝试以「[」开头的行，
# 直到解出一个元素都带 "pool" 键的列表。
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
    sys.exit("未能从 airflow pools list 的输出中解析出 pool 列表")
for p in pools:
    print("{}|{}|{}".format(p["pool"], int(p["slots"]), p.get("description") or ""))
