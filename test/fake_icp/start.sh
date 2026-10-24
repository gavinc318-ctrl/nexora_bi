#!/bin/bash
# 起假 ICP 接口服务（开发期工具，不交付，AS-32）。
#
# 用法：  test/fake_icp/start.sh [种子]          种子默认 1
#
# 账号与端口都从 /etc/oss911/icp.env 读——那是接入程序读的同一份文件（30-python 在开发机上生成），
# 所以两边永远一致，不用记口令。前台运行，Ctrl+C 停止；要一直开着就另开一个终端跑它。
#
# 口令会出现在假接口进程的命令行里（ps 看得到）。假接口只在开发机、只监听 127.0.0.1，可以接受；
# 不得照搬到任何真实服务（runbook/09）。
set -euo pipefail

ENV_FILE=/etc/oss911/icp.env
PREFIX=OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT
HOST_VAR=OSS911_HOST_ICP_CMS_GATEWAY
SEED=${1:-1}
HERE=$(cd "$(dirname "$0")" && pwd)

# icp.env 是 airflow 所有、0600，只能经 sudo 读；只取需要的三行
lines=$(sudo grep -E "^(${PREFIX}_USER|${PREFIX}_PASSWORD|${HOST_VAR})=" "$ENV_FILE") || {
    echo "读不到 $ENV_FILE（不存在，或缺变量）。先跑 30-python。" >&2; exit 1; }
user=$(sed -n "s/^${PREFIX}_USER=//p" <<<"$lines")
pass=$(sed -n "s/^${PREFIX}_PASSWORD=//p" <<<"$lines")
url=$(sed -n "s/^${HOST_VAR}=//p" <<<"$lines")
port=$(sed -nE 's#^https?://[^:/]+:([0-9]+).*#\1#p' <<<"$url")
if [[ -z $user || -z $pass || -z $port ]]; then
    echo "$ENV_FILE 里账号、口令或地址（带端口）不全：地址为 '$url'" >&2; exit 1
fi

# 端口已被占用多半是上一次的假接口还开着
if ss -ltn "sport = :$port" | grep -q LISTEN; then
    echo "端口 $port 已被占用。查看占用者：  sudo ss -ltnp 'sport = :$port'" >&2
    echo "若是上一次的假接口，到它的终端按 Ctrl+C，或：  pkill -f 'fake_icp/server.py --port $port'" >&2
    exit 1
fi

echo "假 ICP 接口：端口 $port，种子 $SEED，账号 $user（取自 $ENV_FILE）。Ctrl+C 停止。"
exec python3 "$HERE/server.py" --port "$port" --seed "$SEED" --account "$user:$pass"
