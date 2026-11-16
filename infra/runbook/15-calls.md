# 第 15 步 —— 通话级：core.fact_call（DD-96）

## 这一步在做什么

`fact_call_leg` 是每张话单（每通电话经过的每个设备）一行。指标大多按「一通电话」算，所以由 dbt 把同一 CALLID 的腿
聚成一行 `core.fact_call`（DD-66：通话级不独立接入，只从腿聚）。规则全文在 LD-03 2.4，这里只讲操作上要懂的。

| 模型 | 什么时候跑 |
| --- | --- |
| `fact_call` | `build_icp_cdr_voice`（新腿进来之后）与 `build_icp_reference`（维度变了、腿重新关联之后）各跑一次 |

## 先懂四件事

### 一、每通电话都有一行；首腿取序号最小的正序号腿，末腿到了才算完整

首腿是序号（CALLIDNUM）最小的正序号腿；只有 -1 腿的通话（只有一张话单的通话可能编 -1，ICP 问题 Q39）以 -1 腿为首腿。
用的是哪一条记在 `first_leg_seq`：正常是 1，-1 是单张话单的通话，2 以上说明 1 号腿缺了或迟到——这时呼叫时刻、呼叫类型、
首队列可能不准，测试 `fact_call_leg_without_first_leg`（WARN）报告有多少；1 号腿补到后该通话自动重算。
末腿（-1）没到的通话照建，`is_complete = false`，结果码、结束时刻、挂断方都为空——发布的指标只算完整的通话，
没完整的单独算「进行中」。

### 二、四种结果，先看谁挂的

| 顺序 | 结果 | 条件 |
| --- | --- | --- |
| 1 | `answered` | 有接通的坐席腿 |
| 2 | `abandoned` | 有队列腿或坐席腿，末腿由主叫释放（不论当时在排队还是坐席在振铃） |
| 3 | `unanswered_system` | 有队列腿或坐席腿、都没接通，不是主叫挂的（含排队超时被系统释放） |
| 4 | `ivr_only` | 既没有队列腿也没有坐席腿 |

转接与恶意呼叫是标志（`is_transferred`、`is_malicious`），不是结果。坐席那一侧的「未接」看腿级（M-C12）。

### 三、只重算腿变了的通话

每次构建只看「上次写入以来腿有变化」（腿被改写或被维度模型重新关联）的通话，再多看一天的余量；重算后只写内容真的变了的行。
同一 CALLID 各腿的 CALLTYPE 不一致时，通话取首腿的，另开一条待澄清（每个 CALLID 一条）。

### 四、每一列都能从腿重算

测试 `fact_call_recomputes_from_legs` 用另一种写法把每一列从腿重算一遍再比；`fact_call_result_rules` 检查结果码的规则；
`fact_call_for_every_call` 检查腿表里的每通电话都有行。三条都是 ERROR 级：通话级与腿级一旦对不上，报表就会自相矛盾。

**检查哪些通话**：构建里跑的这两条（重算、每通都有行）只查「最近一天 fact_call 写过的通话」加「最近一天有腿变化的通话」——
前者抓写错，后者抓漏更新（腿变了而通话行没跟着改，它的 `built_at` 是旧的）与漏建行。时间只用来选通话，选中的通话读它的**全部腿**：
昨天开始、今天结束的长通话，今天检查时带着昨天的腿。全部通话的检查耗时随数据量增长，不能每 5 分钟跑一次，放在夜间的
`maintenance_checks`（03:30 利雅得时间）跑一遍，也兜住构建停了一天以上、变化落在窗口外的情况。

## 执行（结构变更 0010、0011）

0010 给 `fact_call` 加 `is_complete`，往 `dim_call_result` 写入四种结果，给腿表加 `last_change_at` 索引。没有新扩展，20 不必先跑；
dbt 与 DAG 有改动，所以 45 在 50 之前：

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/40-schema.yml    -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 |
| --- | --- |
| 40 | 执行 0010、0011（真机上 0010 已执行过，这次只执行 0011）；`Structural changes 0000 to 0011 executed`；权限验证全过 |
| 45 | 新发布目录；契约跳过 |
| 50 | 安装新的 dbt 项目与 DAG |

第二遍都应 `changed=0`。之后第一次 `build_icp_cdr_voice` 会把已有的全部通话建出来（腿全都「近一天内变过」或表为空）。

### 0011（2026-11-16 规则更正）

0011 清空 `fact_call`（按 0010 规则建的行：缺 1 号腿的通话没有行，只有队列腿、系统释放的通话被记成 `ivr_only`），
加 `first_leg_seq` 列。表空了，下一次构建就按新规则把全部通话重建。命令同上（40 → 45 → 50，中间不停）。

**先暂停两个构建 DAG，并确认没有 dbt 会话还在跑**：TRUNCATE 要等所有读写 fact_call 的会话结束。Airflow 的任务超时（30 分钟）
只杀 dbt 进程，库里的查询会继续跑，所以 Airflow 里看不到任务，库里也可能还有 dbt 会话。

```bash
for d in build_icp_cdr_voice build_icp_reference; do
  sudo -u airflow bash -c "set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow dags pause $d"
done
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT pid, now() - xact_start AS xact_age, state FROM pg_stat_activity WHERE usename = 'dbt' AND state <> 'idle'"
```

有残留就 `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'dbt' AND state <> 'idle'`
（构建都是可重跑的，回滚无害）。50 完成后把两个 DAG `unpause`。

40 跑完到 50 装好新模型之间，如果调度正好触发构建，**旧模型的 fact_call 会失败**（`The source and target schemas on this
incremental model are out of sync`）——这是有意的：新列 NOT NULL、无默认值，旧模型写不进来，不会按旧规则把表填回去。
50 之后的下一次构建正常。

## 核验

```bash
# 结果分布：完整的四种之一，未完整的为空
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT r.code, f.is_complete, count(*)
  FROM core.fact_call f LEFT JOIN core.dim_call_result r USING (call_result_sk)
 GROUP BY 1, 2 ORDER BY 1, 2"

# 腿表的通话数应等于通话行数
sudo -u postgres psql -d oss911 -tAc "
SELECT (SELECT count(DISTINCT call_sk) FROM core.fact_call_leg), (SELECT count(*) FROM core.fact_call)"

# 首腿是哪一条：1 正常；-1 单张话单的通话；2 以上是 1 号腿缺了或迟到
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT first_leg_seq, is_complete, count(*) FROM core.fact_call GROUP BY 1, 2 ORDER BY 1, 2"
```

两个数相等；构建日志里 `fact_call_recomputes_from_legs`、`fact_call_result_rules`、`fact_call_for_every_call` 都是 PASS。

## 2026-11-16 第二批：检查范围、夜间全量检查、dbt 语句超时、假接口新增三类通话

| 改了什么 | 生效靠 |
| --- | --- |
| 两条一致性测试只查近一天的通话（见「四」）；新 DAG `maintenance_checks` 每晚全量查一遍 | 45 → 50 |
| dbt 登录用户 `statement_timeout = 25min`（略小于构建任务 30 分钟的超时：Airflow 超时只杀 dbt 进程，库里的查询会留下来） | 40 |
| 假接口另加三类通话：只经 IVR（单张话单、编 -1）、排队超时由系统释放、坐席振铃未接由系统释放，约占 6% | 重启假接口 |

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/40-schema.yml    -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K
sudo -u airflow bash -c "set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow dags unpause maintenance_checks"
```

40 里 `dbt: set its statement_timeout` 第一遍 changed、第二遍 ok；没有新的结构变更。50 应列出 `maintenance_checks`。
假接口：到它的终端 Ctrl+C，再 `test/fake_icp/start.sh`（参照数据的版本见 runbook 14「执行」一节的说明）。原有通话的内容不变，
新增的三类通话 CALLID 以 `-91` 结尾，回扫会把前一天的也补进来。

```bash
# 语句超时已在库里
sudo -u postgres psql -d oss911 -tAc "SELECT rolconfig FROM pg_roles WHERE rolname = 'dbt'"       # {statement_timeout=25min}

# 新通话到了之后（下一次 build_icp_cdr_voice），四种结果都应出现；ivr_only 的 first_leg_seq 为 -1
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT r.code, f.first_leg_seq, count(*)
  FROM core.fact_call f LEFT JOIN core.dim_call_result r USING (call_result_sk)
 WHERE f.is_complete GROUP BY 1, 2 ORDER BY 1, 2"
```

语句超时在 pgBouncer 已有的服务端连接上不立即生效，连接回收（`server_lifetime`）后才带上；不影响什么，只是别以为马上就有。
