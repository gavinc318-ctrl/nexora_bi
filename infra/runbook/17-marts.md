# 第 17 步 —— 话务 mart 与按日发布（DD-97 · DD-81 · DD-99）

## 这一步在做什么

报表和服务层不直接读 core，读 mart：按日预先汇总好的表。第一组五张，都来自 `fact_call` / `fact_call_leg`：

| 表 | 一行是 | 主要的列 | 指标 |
| --- | --- | --- | --- |
| `mart.calls__day__volume` | 日 × 站点 | 呼入、应答、放弃、系统未接、仅 IVR、转接、未完整、非呼入 | M-C01–04、M-C13 |
| `mart.calls__day_queue__service` | 日 × 站点 × 首队列 | 排队、应答、X 秒内应答、放弃、等待合计 | M-C05、M-C06 |
| `mart.legs__day_agent__handling` | 日 × 站点 × 坐席 | 呈现、接通、未接的坐席腿，通话合计 | M-C07、M-C09、M-C10、M-C12 |
| `mart.calls__day__release` | 日 × 站点 × 挂断方 | 通话数、当日合计 | M-C11 |
| `mart.calls__day__repeat_failed` | 日 × 站点 | 当日未接通 ≥ 2 次的主叫数、这些主叫的未接通呼叫数 | M-C15 |

M-C15 的时间窗是自然日（利雅得，Gavin 2026-11-16 定），未接通 = 结果为放弃或系统未接的完整呼入；按主叫号码分组只在库里做，
mart 只存计数，不存号码。

每张表背后有一个同名的计算视图 `staging.mart_calc__<表名>`，它从 core 现算全部日子；mart 表只是把某些日子「发布」出来，
每行带 `close_version` 和 `publication_seq`。

## 先懂四件事

### 一、一天有三种状态

| 状态 | close_version | 什么时候写 | 之后 |
| --- | --- | --- | --- |
| 临时 | 0 | 每小时（`publish_marts_intraday`），最近 7 天里还没封账的日子整日重写 | 下一小时被替换；封账时删掉 |
| 已封账 | 1 | 每晚（`publish_marts_nightly`），**回扫已经进了 core** 的日子 | 不再改动 |
| 修正 | 2、3 … | 每晚重算最近 7 个已封账日，和最新一版逐行比，**有差才写** | 旧版本的行保留 |

「回扫已经进了 core」从库里判断，不看钟点：这一天（利雅得时间）覆盖到的每个源端日（ICP 按 UTC），都有一次成功的回扫，
且它的批次全被 `fact_call_leg` 消费了（在隔离里被**丢弃**的批次也算：人已经决定，不会再有数据；**待定**的批次挡住这一天）。早于第一次回扫的源端日算已定稿（不会再有回扫）。封账**按日期顺序、不跳过**：
中间某天的回扫还没到，它之后的日子也先不封，等它到了一起封。所以回扫失败几天（例如假接口夜里没开）不会出错，
只是封账推迟；补跑成功后的下一晚自动补上。推迟超过 3 天，`maintenance_checks` 的 `mart_closing_not_behind` 变红（见下「核验」后的告警）。

### 二、每次发布都登记，零行也登记

每次写一张 mart 表，`meta.data_publication` 记一行（种类 intraday · nightly · correction）；每写一天，`meta.mart_day_publication`
记一行（表、日、版本、发布序号、行数）。某天没有任何通话也登记，行数 0。服务层先据它决定每天用哪一版，再去读行（DD-99）。
测试 `mart_matches_publication` 每次都核对表里的行和登记一致，并且已封账的日子不再留有临时行。

### 三、比率在服务层算，mart 只存能相加的数

参数型指标的参数（M-C05 的 X 秒、M-C15 的「≥ 2 次」）在 mart 里由 dbt 变量算，在注册表里记在 params；metrics.yaml 的 `param_vars`
写明两者的对应，46 部署时核对相等，不等即失败——否则 mart 列的含义与注册表说的不一致。

mart 里没有「应答率」「平均等待」这样的列，只有应答数、呼入数、等待合计这样的数；服务层把选中的行各自相加再相除
（DD-97 第三条）。否则按周或跨队列汇总时就成了「均值的均值」。挂断方表的「当日合计」在每个挂断方的行上重复存放，
所以查它必须按挂断方分组（`metrics/marts.yaml` 的 `required_dimensions`）。

### 四、谁能读哪张表，写在 `metrics/marts.yaml`

坐席处理表：接警员只见本人（按工号匹配），主管与管理员见全部，调度员不可见；坐席姓名只有主管与管理员可见。
中心级三张：只有主管与管理员可见（DD-99 更正第七条）。服务层（第 9 步）按这份文件执行；46 部署时核对它写的表和列都存在。

## 执行

没有新的结构变更。mart 表由 dbt 第一次发布时建，**46 要在它之后**（46 核对指标指向的列存在）：

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K

af() { sudo -u airflow bash -c "set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow $*"; }
af dags unpause publish_marts_intraday
af dags trigger publish_marts_intraday          # 第一次：建出四张 mart 表，写最近 7 天的临时数
# 等它成功（界面上变绿，或 af dags list-runs publish_marts_intraday）
ansible-playbook playbooks/46-metrics.yml   -e ansible_connection=local -K
af dags unpause publish_marts_nightly
af dags trigger publish_marts_nightly           # 第一次：把回扫已完成的历史日子全部封账
```

| 步骤 | 预期 |
| --- | --- |
| 50 | DAG 列表多出 `publish_marts_intraday`、`publish_marts_nightly`（暂停） |
| 第一次 intraday | 成功；日志里四张表各 `OK created ... incremental model`，测试全 PASS |
| 46 | 12 条 `updated`（M-C01–07、M-C09–13 加了 mart 映射）；implemented 列出这 12 条 |
| 第一次 nightly | 成功；封账到「第一个回扫还没完成的日子」的前一天 |

如果回扫这几天的失败还没补跑（troubleshooting「`resweep_icp_cdr_voice` 每天都失败」一节的 `tasks clear`），nightly 只会封到失败的第一天之前，
这是对的；补跑成功后的下一次 nightly 接着封。

## 核验

```bash
# 每张表每种版本有几天、几行
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT mart_table, close_version, count(DISTINCT date_key) AS days, sum(row_count) AS rows,
       min(date_key), max(date_key)
  FROM meta.mart_day_publication GROUP BY 1, 2 ORDER BY 1, 2"

# 当前每天用哪一版（服务层的选法：已封账取最大版本，否则取最近一次临时发布）
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT date_key, close_version, offered_calls, answered_calls, abandoned_calls, unanswered_system_calls, ivr_only_calls,
       incomplete_calls
  FROM mart.calls__day__volume v
 WHERE close_version = (SELECT max(close_version) FROM mart.calls__day__volume x WHERE x.date_key = v.date_key)
 ORDER BY date_key DESC LIMIT 10"
```

已封账的日子 `incomplete_calls` 应为 0 或个位数（封账时仍未完整的通话，不计入呼入）；今天的临时行有「进行中」的通话。

## 两个夜间告警（`maintenance_checks`，ERROR 级）

| 检查 | 红了说明 | 先看 |
| --- | --- | --- |
| `batches_reach_core` | 有 CDR 批次入库满一天还没进 `fact_call_leg` | troubleshooting「nightly 只封账到某一天」 |
| `mart_closing_not_behind` | 某张 mart 最新封账日落后今天超过 3 天，或一天都没封过 | 回扫是否失败、隔离里有无待定的回扫批次、nightly 是否在跑 |

监控批次接通道（M-11）之前，`maintenance_checks` 变红就是告警（Mac 侧 2026-11-16）。
