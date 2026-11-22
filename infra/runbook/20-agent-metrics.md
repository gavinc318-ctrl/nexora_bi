# 第 20 步 —— ICP 坐席指标：签入区间与日累计（if03b / if03c）

## 这一步在做什么

ICP 的坐席历史指标接口 `hindex/agent`（接口文档 p.935–936、p.1035）给两样东西，各接成一个源（DD-98）：

| 源 | 指标 | raw | core |
| --- | --- | --- | --- |
| `icp_agent_session` | `IDX_04_03_001` 签入签出区间 | `raw.icp_agent_session`：每段区间、每次有变化的观测一行 | `core.fact_agent_session`：每段区间一行 |
| `icp_agent_day` | `IDX_04_02_*` 十四项日累计 | `raw.icp_agent_day`：每坐席、每天、每次有变化的观测一行 | `core.fact_agent_day`：每坐席每天一行 |

| DAG | 何时 | 做什么 |
| --- | --- | --- |
| `ingest_icp_agent_session` · `ingest_icp_agent_day` | 每 10 分钟 | 观测一次「当日至今」 |
| `build_icp_agent_session` · `build_icp_agent_day` | 每 10 分钟 | dbt 构建 staging 与 core 模型及其测试 |

接入走 pool `icp_cms`、构建走 `dbt_build`，与话单、参照数据不会同时跑。M-R02（在岗时长）读区间，M-R03、M-R04（状态时长、
占用率）读日累计；三条都标了 `depends_unverified`，取数语义未经 ICP 确认。

## 先懂五件事

### 一、接口没有日期参数

请求体只有 ccId、vdnId、objectIds（工号数组）与 indexIds，没有日期。返回的是哪一天、按哪个时区切日，问题单 Q36 在问。
答复之前按「当日至今」实现：每次运行就是一次观测，没有时间窗口、没有水位（`meta.watermark_state` 里没有这两个源），
**也没有回补**——停机漏掉的那几个小时，那一天的日累计就停在停机前最后一次观测。

### 二、观测归哪一天

- 区间按自己的 `inTime` 归日，观测时刻不影响它。跨过午夜的区间归签入那天
- 日累计：观测时刻按 `Asia/Riyadh`（契约 `window.day_timezone`）取日期。**午夜后 30 分钟内的观测整次不做**：报表批有延迟，
  那时取到的可能还是前一天的值。run_log 的 message 写明 `within 30 minutes after midnight: not used`。分钟数可热调
  （`midnight_skip_minutes`），只能调大
- 某天的终值 = 当天最后一次可用观测（当地 23:50 左右），最后十分钟拿不到。这是「当日至今」固有的缺口

### 三、问哪些坐席

接入程序读不到 core，工号从 `raw.icp_reference` 的 agents 快照取（DD-98 第三条）：本中心 VDN（契约 `site_vdn`）当日开始时
有效的那份，加上当日之后取到的每份**可信**快照（未隔离，或隔离后放行并已被 dbt 应用）。当天中途被删的坐席，这一天照样问。
**一份可信快照都没有，运行失败**——先让 `ingest_icp_reference` 跑过（第 14 步）。每 2000 个工号一个请求、一个批次。

问了而没返回的工号**登记、不隔离**（DD-98 评审注，Q40）：每个请求在 `raw.icp_agent_coverage` 留一行——请求的工号、返回的
工号、观测时刻（结构变更 0016）。该批照常接入返回了的记录，run_log 的 message 写 `N agent(s) asked for but not returned`。
空数组即全部缺失，也不隔离；响应根本不是记录数组的，仍整批隔离。

### 四、raw 只写变了的

同一段区间签出前每次观测都在变（duration 增长，签出时 outTime 回填），日累计也一样。每个键（区间：工号 + inTime；
日累计：工号 + 日期）只在内容与它**上一次**观测不同时写一行；待处理或已丢弃批次里的行不算「上一次」。所以值改回
原样也会再写一行。core 每个键取最新的观测。

批次检查不过（响应形状不对、返回了没问的工号、时间格式或先后不对、值不是整数或 null）照常写 raw、整批隔离，由 M-03 处理。

内容最后变化的时刻是 `src_observed_at`；**平台最后一次确认的时刻**是 `src_confirmed_at`（0016）：该工号、该日最后一个返回了
它的可用批次的观测时刻，由 dbt 从 `raw.icp_agent_coverage` 推进。仍在线而内容不变的坐席，raw 不写、`src_observed_at` 不动，
`src_confirmed_at` 照样每十分钟前进。「数据截至何时」都读后者（对账截止点、mart 里未签出区间算到何时，DD-101）。

日累计是「当日至今」的累计，只增不减。同一坐席同一归属日的任一累计值比上一次观测**变小**，测试
`agent_day_totals_decrease` 报 ERROR——多半是平台按别的时区切日。它在 `fact_agent_day` 之后跑，只报告、不挡 core 写入；
查近两天的观测，所以一次变小会让 build 任务红到它滑出窗口。
`"10759.0"` 这种写法算整数（文档样例就是这样）；**null 保持 null**，那是「没有这一天」，不是 0。

### 五、配置里没有的坐席照样进 core

core 的 `agent_sk` 允许为空、另存源端工号 `agent_no`（结构变更 0015）。区间关联签入时有效的坐席版本；日累计关联当日结束时
有效的版本，当天中途被删的取当天最后一个有效版本（DD-98 第六条）。哪一版都没有：`agent_sk` 为空，每个工号一条待澄清
（`agentNo of sign-in intervals …` / `agentNo of day totals …`，DD-95）。参照数据变了，`dim_agent` 会把这两张表的关联一并改对，
与话单的坐席腿一样。

## 执行

前提：第 14 步（参照数据）已在跑，`raw.icp_reference` 里有可信的 agents 快照。假接口在跑（第 11 步，`test/fake_icp/start.sh`，
本步要用它新加的 `hindex/agent`，**须重启一次**才有）。

```bash
sudo -v
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/40-schema.yml -e ansible_connection=local -K
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml -e ansible_connection=local -K
```

**30 必须在 45 之前**：45 用已装的接入程序校验契约，0.1.0 不认识这两个新源（`no ingestion for connector 'rest_api' /
source 'icp_agent_day'`），校验不过即停在同步之前，库与发布目录都不动。先跑 30 装上 0.2.0 再跑 45 即可。

| 剧本 | 第一遍预期 | 第二遍 |
| --- | --- | --- |
| 40 | 执行 0015；权限验证全过（178 项） | `changed=0` |
| 30 | 接入程序重装，`oss911-ingest 0.2.0` | `changed=0` |
| 45 | `icp_agent_day|inserted|1`、`icp_agent_session|inserted|1`，另两个 `unchanged`；新发布目录；`dags.json` 重写 | `changed=0` |
| 50 | DAG 与 dbt 项目更新；四个新 DAG 出现，处于暂停 | `changed=0` |

以下 Airflow 命令用第 11 步定义的 `af` 函数。

**一、手工各跑一次接入**

```bash
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest run --source icp_agent_session
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest run --source icp_agent_day
```

预期：各一行 `60 agent(s) of VDN 1/1, 1 request(s)`，一个批次 `received …, written …`，`success`（区间约 120 段，
日累计 60 行）。若在当地 00:00–00:30 之间跑，日累计那条只写 `not used`，正常。马上再跑一遍：仍在线的区间和日累计在变，
写入数不会是 0。

**二、开 DAG**

```bash
af dags unpause ingest_icp_agent_session
af dags unpause ingest_icp_agent_day
af dags unpause build_icp_agent_session
af dags unpause build_icp_agent_day
```

等二十分钟左右：

```bash
af dags list-runs build_icp_agent_day -o table | head -5
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT 'session' AS t, count(*), count(agent_sk) AS linked, count(DISTINCT agent_no) AS agents, max(src_observed_at) FROM core.fact_agent_session
UNION ALL
SELECT 'day', count(*), count(agent_sk), count(DISTINCT agent_no), max(src_observed_at) FROM core.fact_agent_day"
```

预期：build 的运行 `success`；两行的 linked 等于 count，agents 为 60，`max` 在最近十分钟内。

**三、看一名坐席**

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT agent_no, signin_at, signout_at, duration_seconds, src_observed_at
  FROM core.fact_agent_session WHERE agent_no = '101' ORDER BY signin_at DESC LIMIT 3"
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT agent_no, date_key, signin_count, signin_seconds, inbound_calls, talk_seconds, acw_seconds, idle_seconds
  FROM core.fact_agent_day WHERE agent_no = '101' ORDER BY date_key DESC LIMIT 2"
```

预期：假接口下每名坐席每天两段（中间休息），仍在线的那段 `signout_at` 为空、duration 随观测增长；日累计的
`signin_count` 与当天段数一致，`inbound_calls` 与当天话单里该坐席接起的腿数一致（假接口按同一份假话单算）。

## 开发机上验不到的

- 真实接口返回的是哪一天、按哪个时区切日（Q36）；未签出区间的 duration（Q37）；跨日区间返回几次（Q38）。三条都写在契约的
  `integration_checks` 里，联调时逐条核实，核实结论回写契约并递增版本
- 报表批的时间表：午夜后 30 分钟够不够，日终值能拿全到什么程度
- 2000 个工号一次请求的耗时与响应大小（文档上限 20000）

## 补丁：缺失工号登记与最后确认时刻（2026-11-22，DD-98 评审注）

第 7 步已上真机之后的修正：结构变更 0016、接入程序 0.3.0、两份契约 v2（`id` 指回 `agent_no`）、dbt 两个 core 模型与新测试
`agent_day_totals_decrease`、假接口（日累计各项只增不减；新注入 `agent_missing`）。**30 在 45 之前**（45 用已装的接入程序
校验契约，应由与契约同一发布的 0.3.0 来校验）。

```bash
sudo -v
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/40-schema.yml -e ansible_connection=local -K
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 | 第二遍 |
| --- | --- | --- |
| 40 | 执行 0016；权限验证全过（187 项） | `changed=0` |
| 30 | 接入程序重装，`oss911-ingest 0.3.0` | `changed=0` |
| 45 | `icp_agent_day|updated|2`、`icp_agent_session|updated|2`，另两个 `unchanged` | `changed=0` |
| 50 | dbt 项目更新（新测试、两个 core 模型） | `changed=0` |

然后重启假接口（跑 `test/fake_icp/start.sh` 的终端 Ctrl+C 再运行）。等二十分钟：

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT source_id, count(*) AS requests, sum(missing_count) AS missing, max(observed_at)
  FROM raw.icp_agent_coverage GROUP BY 1"
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT 'session' AS t, count(*) FILTER (WHERE src_confirmed_at > src_observed_at) AS confirmed_later, max(src_confirmed_at)
  FROM core.fact_agent_session WHERE date_key = to_char(now() AT TIME ZONE 'Asia/Riyadh', 'YYYYMMDD')::int
UNION ALL
SELECT 'day', count(*) FILTER (WHERE src_confirmed_at > src_observed_at), max(src_confirmed_at)
  FROM core.fact_agent_day WHERE date_key = to_char(now() AT TIME ZONE 'Asia/Riyadh', 'YYYYMMDD')::int"
```

预期：两源各有若干请求、`missing` 为 0；`max` 在最近十分钟内。`confirmed_later` 大于 0：已签出的区间内容不再变，
确认时刻仍在前进。

想看缺失登记：`curl -s -X POST localhost:18080/_fake/control -d '{"agent_inject": ["agent_missing"]}'`，下一次接入的
批次行写 `1 agent(s) not returned (registered, not quarantined)`，任务仍 `success`，`missing` 加 1。
