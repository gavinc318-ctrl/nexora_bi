# 第 21 步 —— 监控告警通道（DD-100）

监控通道分三块上线。本文随每块补一节：

| 块 | 内容 | 状态 |
| --- | --- | --- |
| 一 | 测试结果落库 `meta.check_result`、只读角色 `oss911_monitor`、话单腿未关联工号的部分索引、日累计与话单对账 | 本节 |
| 二 | 指标导出程序 `oss911-monitor`、Prometheus 采集与告警规则、预期检查清单 | 待写 |
| 三 | Alertmanager、假网关、心跳、每日汇总 | 待写 |

## 第一块：测试结果落库与对账

### 这一块在做什么

**1. 每次 dbt 命令的结果写进 `meta.check_result`**

dbt 每跑完一次 build、run、test，`on-run-end` 钩子（`src/dbt/macros/check_results.sql`）就把本次跑过或跳过的每个模型、
每项测试各写一行：

- 模型：状态 `success`、`error` 或 `skipped`，以及影响行数
- 测试：状态 `pass`、`warn`、`fail`、`error` 或 `skipped`，以及严重度和返回行数（`failures`）。报错或跳过的测试，
  `failures` 为空，不当成 0
- 都带上是哪个 DAG、哪个任务、第几次尝试跑的（`dag_id`、`task_id`、`try_number`）。手工跑的这几列为空

WARN 以前只留在 Airflow 任务日志里，现在能查了，监控第二块的导出程序也从这里读。dbt 中途崩溃时钩子不执行，表里就没有
这次的行。所以第二块判断的是「每项检查最后一行有多旧」，不是「有没有失败」。

表只增不改，按月分区。`maintenance_partitions` 每晚新增一个任务 `drop_expired_check_results`，把整月过了 90 天的
分区删掉。删除函数只认这一张表，保留期少于 90 天它会拒绝。

**2. 只读角色 `oss911_monitor`**

导出程序将来用的角色。只能读几张登记表，外加 core 里少数几列：工号、日期、`agent_sk`、`src_confirmed_at`。话单腿的号码、
日累计的数值、raw 的原始内容它都读不到。它的登录用户和凭据文件随第二块的导出程序一起建。

角色是 40 在执行结构变更之前建的，结构变更 0017 只给它授权。原因是从 0003 起结构变更以属主角色身份执行，属主角色不能建角色。

**3. 日累计与话单对账**（新测试 `agent_day_reconciles_with_legs`，WARN）

按坐席逐日，拿平台日累计的呼入数 `inbound_calls`，去对 core 里这名坐席当天接通的话单腿数。话单腿只数在日累计行的
`src_confirmed_at` 之前结束的，也就是平台最后一次确认这行的时刻。日终最后十分钟两边都拿不到，一起去掉，就不会天天报差。

- 只比话单已封账的日子（`mart.legs__day_agent__handling` 有封账版本），范围是最近 7 天
- 差额超过 max(2 通, 话单腿数 × 2%) 才算不一致（dbt 变量 `agent_reconcile`）
- WARN，只进每日汇总，不发短信，也不挡封账：平台的切日时刻还没有答复（Q36）
- 不引用模型，构建 DAG 选不到它，由 `maintenance_checks` 每晚 04:30 跑

### 部署

这一块改了结构变更、dbt 项目和 DAG。接入程序没改，30 不用跑。

```bash
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/40-schema.yml -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml -e ansible_connection=local -K
```

每个剧本跑完都再跑一遍，第二遍应是 `changed=0`。

| 剧本 | 第一遍应该看到 |
| --- | --- |
| 40 | 「Create the privilege roles added after 0002」为 changed（建了 `oss911_monitor`）；执行 0017；初始分区那步建出 `meta.check_result` 的分区；权限验证 222 项全过 |
| 45 | 新建发布目录，四个合同都是 unchanged |
| 50 | dbt 项目和 DAG 文件更新 |

### 验证

**一、结果在落库。** 等下一个 10 分钟点过后：

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT dag_id, count(*) AS nodes, count(*) FILTER (WHERE status NOT IN ('success', 'pass')) AS not_ok,
       max(checked_at) AS last
  FROM meta.check_result GROUP BY 1 ORDER BY 1"
```

应看到 `build_icp_agent_day`、`build_icp_agent_session`、`build_icp_cdr_voice` 等各一行，`last` 在 10 分钟以内。
`not_ok` 不为 0 时，可以这样看是哪几项：

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT checked_at, dag_id, node_name, status, severity, failures
  FROM meta.check_result WHERE status NOT IN ('success', 'pass') ORDER BY checked_at DESC LIMIT 10"
```

开发机上 `fact_call_leg_without_dimension` 这类 WARN 在配置补全之前一直不为零，属预期。

**二、对账。** 不用等到明晚，手工触发一次夜间检查：

```bash
af dags trigger maintenance_checks
```

跑完以后：

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT node_name, status, severity, failures, dag_run_id
  FROM meta.check_result WHERE dag_id = 'maintenance_checks' ORDER BY checked_at DESC LIMIT 8"
```

`agent_day_reconciles_with_legs` 是 `pass`，或者 `warn` 加上一个不一致的天数。比的是已封账的 11-21、11-22。这两天的
日累计来自旧假接口，呼入数原本就照假话单算，所以应该一致或只差个别几行。

**三、保留期**在明晚 `maintenance_partitions` 跑过之后看一下：日志里 `drop_expired_check_results` 那个任务打印
`check_result: N partitions past 90 days dropped`。开发机上 N 是 0。
