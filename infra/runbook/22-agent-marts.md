# 第 22 步 —— 坐席 mart 与封账（DD-101）

## 这一步在做什么

第 20 步把坐席数据接到 core（`fact_agent_session` 签入区间、`fact_agent_day` 平台日累计），服务层和报表还读不到。
这一步加三张 mart 表，发布方式与话务 mart 相同（runbook 17）：

| 表 | 一行是 | 主要的列 | 指标 |
| --- | --- | --- | --- |
| `mart.agents__day_agent__duty` | 日 × 站点 × 坐席 | 签入段数、在岗秒数、未签出段数、`agent_days`（恒为 1）；当日首次签入、末次签出（仅展示） | M-R02 |
| `mart.agents__day_hour__on_duty` | 日 × 站点 × 小时（0–23） | 该小时在岗的不同工号数、在岗秒数 | M-R01 |
| `mart.agents__day_agent__state` | 日 × 站点 × 坐席 | 签入、通话、事后处理、休息、示忙、保持、空闲各秒数，呼入、呼出次数；占用率的分子分母 | M-R03、M-R04 |

前两张来自签入区间，第三张来自平台日累计（DD-65）：两个来源口径不同，所以不放在一张表里。

## 先懂四件事

### 一、跨午夜的区间切开

一段 22:00 签入、次日 02:30 签出的区间，前一天算 2 小时，后一天算 2.5 小时（利雅得时间）。这样每天的合计可以相加，
单个坐席一天不会超过 24 小时。签入段数只算在签入那天。

还没签出的区间，算到平台**最后一次返回该工号**的时刻（`src_confirmed_at`，第 20 步补丁），计入「未签出段数」。

### 二、小时表的人数不能跨小时相加

`agents_on_duty` 是这一小时里在岗的**不同工号数**：同一个人在 8 点和 9 点都在岗，两个小时各算 1。把各小时相加
得到的是「人·小时」，不是人数。所以查这张表必须按小时分组（`metrics/marts.yaml` 的 `required_dimensions: [hour]`）。

### 三、占用率的分子分母同时为空

M-R04 = （通话 + 事后处理）/ 签入时长。三项里任一项平台给的是空值，这一行的 `occupied_seconds` 和
`occupancy_base_seconds` 都为空，不写 0。服务层把各行分子、分母分别相加再相除，两者始终来自同一批行。

M-R03（各状态时长）这次**没有接上服务层**：注册表一条指标只能有一个分子，而它有七个状态。列都在 mart 里，
怎么登记待 Mac 侧定（NOTES 需定 ①）。

### 四、封账不靠回扫

坐席接口没有日期参数，不能回扫（DD-98）。D 日在 D 日之后第一次夜间发布（`publish_marts_nightly`，UTC 04:00）时封账，
条件是两个坐席源（`icp_agent_session`、`icp_agent_day`）各自满足两点：

- D 日的观测批次**全部进了 core**，或在隔离里已被**丢弃**。还**待定**的隔离批次挡住这一天
- 已经有 D 日**之后**某天的批次进了 core，即接入已经越过了 D 日。接入停在下午时，当晚不会把这一天当完整的封掉

其余与话务 mart 相同：按日期顺序封，不跳过；最近 7 个已封账日每晚重算，有差别才写新版本。三张表的封账条件相同。

## 执行

接着第三块之后跑。改了接入程序包（0.5.1，每日汇总多一行对账）、dbt（三张 mart、一项测试、一个变量）、DAG（夜间检查多一项）、
监控配置（预期检查多一项）、指标映射和读取规则。数据库结构不变。

mart 表由 dbt 第一次发布时建，**46 要在它之后**（46 核对指标指向的列存在）：

```bash
sudo -v
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/30-python.yml    -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K

af() { sudo -u airflow bash -c "set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow $*"; }
af dags trigger publish_marts_intraday          # 建出三张坐席 mart，写最近 7 天的临时数
# 等它成功（af dags list-runs publish_marts_intraday，state 为 success）
ansible-playbook playbooks/46-metrics.yml   -e ansible_connection=local -K
af dags trigger publish_marts_nightly           # 封账：符合上面条件的日子
ansible-playbook playbooks/55-monitor.yml   -e ansible_connection=local -K
```

全部跑完后，五个剧本按同样顺序再跑一遍，每个都应是 `changed=0`。

| 步骤 | 预期 |
| --- | --- |
| 30 | `oss911-ingest 0.5.1` |
| 50 | 发布目录换新；DAG 列表不变 |
| 第一次 intraday | 成功；日志里三张新表各 `OK created ... incremental model`，`mart_matches_publication` 全 PASS |
| 46 | M-R01、M-R02、M-R04 为 `updated` 且 implemented；M-R03 仍 not_implemented；三张表 `rule_inserted` |
| 第一次 nightly | 成功；坐席三张表封账到「第一个不满足条件的日子」的前一天 |
| 55 | 监控配置更新，预期检查多一项 `nightly/agent_duty_reconciles_with_day_totals` |

## 核验

```bash
# 三张表每种版本有几天、几行
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT mart_table, close_version, count(DISTINCT date_key) AS days, sum(row_count) AS rows,
       min(date_key), max(date_key)
  FROM meta.mart_day_publication WHERE mart_table LIKE 'mart.agents%' GROUP BY 1, 2 ORDER BY 1, 2"

# 每天：坐席数、签入段数、在岗总时长；小时表的合计应与之只差几秒（各行四舍五入）
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT d.date_key, d.close_version, count(*) AS agents, sum(d.session_count) AS sessions,
       sum(d.on_duty_seconds) AS duty_seconds, sum(d.open_session_count) AS open_pieces,
       (SELECT sum(h.on_duty_seconds) FROM mart.agents__day_hour__on_duty h
         WHERE h.date_key = d.date_key AND h.close_version = d.close_version) AS hour_seconds,
       (SELECT max(h.agents_on_duty) FROM mart.agents__day_hour__on_duty h
         WHERE h.date_key = d.date_key AND h.close_version = d.close_version) AS peak_agents
  FROM mart.agents__day_agent__duty d
 WHERE d.close_version = (SELECT max(close_version) FROM mart.agents__day_agent__duty x WHERE x.date_key = d.date_key)
 GROUP BY 1, 2 ORDER BY 1 DESC LIMIT 10"
```

`max(on_duty_seconds)` 不应超过 86400。已封账的日子 `open_pieces` 是封账时仍未签出的区间：那天的在岗时长算到了午夜。

### 封账停住时

```bash
# 两个坐席源每天的批次：总数、进了 core 的、在隔离里待定的
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT b.source_id, b.window_from AS day, count(*) AS batches,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM meta.staging_batch_state s
                                       WHERE s.model = replace(b.source_id, 'icp_', 'fact_')
                                         AND s.run_id = b.run_id AND s.batch_seq = b.batch_seq)) AS in_core,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM meta.quarantine_batch q
                                       WHERE q.run_id = b.run_id AND q.batch_seq = b.batch_seq
                                         AND q.state = 'pending')) AS pending
  FROM meta.ingest_batch b
 WHERE b.source_id IN ('icp_agent_session', 'icp_agent_day') AND b.mode = 'observation'
 GROUP BY 1, 2 ORDER BY 2 DESC, 1 LIMIT 20"
```

`pending` 不为 0 的那天（以及它之后的日子）不会封账，处理隔离（M-06）后的下一晚自动补上。某个源最近一天没有批次，
说明接入停了，前一天要等接入恢复。停住超过 3 天，`mart_closing_not_behind` 与 `OSS911ClosingBehind` 会报。

## 对账（WARN，只进每日汇总）

`agent_duty_reconciles_with_day_totals`（`maintenance_checks` 每晚）：已封账的最近 7 天，每个坐席每天的签入段数对平台的
`signin_count`，在岗秒数对平台的 `signin_seconds`（差超过 300 秒与 2% 中较大者算不一致；dbt 变量 `agent_duty_reconcile`）。
差异是平台两个接口之间的，照实报告，不由本方调平。行数增加时发一封 notify；每日汇总「只进汇总」一节列出结果。

## 补丁（DD-101 注 2026-11-24）：M-R01 改为日均，M-R03 拆为六条

- 小时表加一列 `day_count`（每行 1）。M-R01 = `agents_on_duty` 合计 / `day_count` 合计，仍须按小时查：查一天是当天各小时的人数，
  查一周是各小时的日均人数
- M-R03 不再发布（留在注册表，既有文档的引用指下面这一组）；拆为 M-R10 通话、M-R11 事后处理、M-R12 休息、M-R13 示忙、
  M-R14 保持、M-R15 空闲，分子是状态表对应的秒数列，分母 `agent_days`
- mart 表**加列**的做法：发布时若计算视图多了列，先给 mart 表加上（`mart_add_new_columns`，在 `macros/mart.sql`）。已有的行
  新列为空——封账版本是当时提供出去的历史，不改写；夜间修正会给最近 7 个已封账日各写一个新版本（重算的行与旧行不同了），
  更早的日子保持为空。视图少了列则拒绝发布：mart 的列不删（DD-97 第二条）

```bash
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K
af() { sudo -u airflow bash -c "set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow $*"; }
af dags trigger publish_marts_intraday          # 给小时表加上 day_count；等它 success
ansible-playbook playbooks/46-metrics.yml   -e ansible_connection=local -K
af dags trigger publish_marts_nightly           # 给已封账的 11-22、11-23 写带 day_count 的修正版本
```

三个剧本再跑一遍应为 `changed=0`。

| 步骤 | 预期 |
| --- | --- |
| 第一次 intraday | 成功；小时表的日志里没有报错，`mart_matches_publication` 全 PASS |
| 46 | M-R01 `redefined`（上线前口径变更不升版本）；M-R03 `updated`、not_implemented；M-R10 至 M-R15 `inserted`、implemented；其余 unchanged |
| nightly | 小时表 11-22、11-23 各多一个 close_version 2；另两张表不变 |

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT date_key, close_version, count(*) AS rows, sum(day_count) AS day_count
  FROM mart.agents__day_hour__on_duty GROUP BY 1, 2 ORDER BY 1, 2"
```

第 1 版的 `day_count` 为空，第 2 版为 24，今天的临时行为 24。
