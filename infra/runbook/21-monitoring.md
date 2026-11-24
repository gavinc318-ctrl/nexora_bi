# 第 21 步 —— 监控告警通道（DD-100）

监控通道分三块上线。本文随每块补一节：

| 块 | 内容 | 状态 |
| --- | --- | --- |
| 一 | 测试结果落库 `meta.check_result`、只读角色 `oss911_monitor`、话单腿未关联工号的部分索引、日累计与话单对账 | 第一节 |
| 二 | 指标导出程序 `oss911-monitor`、Prometheus 采集与告警规则、预期检查清单 | 第二节 |
| 三 | Alertmanager、假网关、心跳检查、每日汇总 | 第三节 |

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

## 第二块：导出程序、告警规则与预期检查清单

### 这一块在做什么

**1. 导出程序 `oss911-monitor`**

它是接入程序包的第二个入口，和接入程序同一个 venv（`oss911-ingest` 0.4.0）。由 systemd 常驻运行，账号是自己的 `oss911mon`，
数据库登录用户是 `monitor`（角色 `oss911_monitor`，走事务池），凭据在 `/etc/oss911/monitor.env`。它只绑宿主机在容器网络上的
地址（`10.89.10.1:9188`），Prometheus 从容器里来采集。

它只报「是多少」，不判断「该不该响」，判断由 Prometheus 的规则做。每分钟查一轮库，结果缓存 60 秒：

| 指标 | 含义 |
| --- | --- |
| `oss911_source_consecutive_failures` · `oss911_source_last_success_age_seconds` | 各源连续失败次数、距上次成功多久 |
| `oss911_resweep_missing_days` | 到期了、回扫却没进 core 的源日数（这次 11-15 那种缺口） |
| `oss911_mart_closed_lag_days` | 各 mart 表最新封账日离今天几天 |
| `oss911_unprocessed_batches` · `_oldest_age_seconds` | 各模型还没收的批次，及最老一批的时长 |
| `oss911_partition_days_ahead` | 各分区表最后一个月分区还剩几天 |
| `oss911_test_status` · `oss911_test_failures` · `_previous` | 每项 dbt 测试最近一次的状态与行数，以及上一次的行数 |
| `oss911_expected_check_state` | 预期检查清单里每一项当前的状态 |
| `oss911_agents_not_returned` | 连续 N 次请求都没返回的坐席数，分「仍在配置里」与「已删除」 |
| `oss911_quarantine_pending` · `oss911_reference_sync_age_seconds` · `oss911_clarification_*` · `oss911_unlinked_agents` | 隔离待处理、参照副本同步时长、待澄清、未关联工号 |
| `oss911_threshold` | 规则用的阈值，来自发布配置或库里的热调值 |
| `oss911_monitor_query_errors` · `oss911_monitor_config_ok` | 导出程序自己的健康：哪项查询失败、配置是否载入 |

某项查询失败时，这一项的指标整组不出，绝不拿上一轮的旧值顶替，同时 `oss911_monitor_query_errors{query=...}` 置 1。

**2. 预期检查清单**（`contracts/monitoring.yml`，随发布走）

清单列出每个构建 DAG、两个发布 DAG、夜间检查 DAG，以及几项关键测试，各自带周期和允许延迟。导出程序逐项判断状态：

| 状态 | 含义 |
| --- | --- |
| `normal` | 最近一次结果是 pass 或 success，且没有过期 |
| `warned` · `failed` · `error` · `skipped` | 最近一次结果的状态 |
| `stale` | 最近一次结果比「周期加允许延迟」还旧。旧的 pass 不算 pass |
| `never_run` | 从来没有结果，而且宽限期已过 |
| `grace` | 新部署的项，宽限期内还没结果。宽限期从这一项随发布首次部署的时刻算起，长度是一个周期加允许延迟 |

各项首次部署的时刻由 55 写进 `/etc/oss911/monitoring.yml` 的 `deployed_at`，以后每次部署都保留原值。

业务阈值也在这个文件里（封账落后 3 天、批次积压 1 小时、坐席连续 3 次未返回、分区剩余 20 天），各源的连续失败次数与
最长成功间隔也在这里。参照副本过期天数仍是库里的热调值 `version_stale_days`。

**3. 告警规则**（`infra/files/prometheus/alerts.yml`，22 部署）

规则里不写业务阈值，都拿 `oss911_threshold` 来比。阈值缺了，对应的规则就永远不会响，所以每个阈值另有一条 `absent()`
兜底告警。规则分两级：

- `page`（将来发短信加邮件）：源连续失败或停滞、回扫缺日、封账落后、批次积压、分区快用完、ERROR 测试失败、预期检查过期或从未运行、
  任何采集目标 `up == 0`、导出程序查询失败或配置无效、阈值缺失
- `notify`（将来只发邮件）：WARN 比上一次增加、配置内坐席连续未返回、参照副本过期、隔离批次超过 1 小时没人处理

另有一条常亮的心跳 `OSS911Heartbeat`，第三块拿它验证投递链路。**在第三块之前没有 Alertmanager，告警只显示在 Prometheus 的
告警页面上，不会发到任何地方。**

部署前，22 先用镜像里自带的 promtool 检查规则，再跑 `alerts_test.yml` 里的规则单元测试，都过了才换上新规则。

**4. 顺带修了三处**

- **HAProxy 采集目标：** 原来一直是 down，因为统计页只绑了回环地址，也没开 `/metrics`。现在加绑了容器网关地址，并开了 HAProxy
  自带的 Prometheus 导出
- **systemd 重启次数限制：** 6 个 unit 都把 `StartLimitIntervalSec` 写在 `[Service]` 里，systemd 一直忽略它。现在挪到了
  `[Unit]`。30 和 60 会因此重启 Airflow 的四个服务和查询服务
- **构建 DAG 和夜间检查不再重试：** 测试失败两分钟后照样失败，三次尝试每次占住 pool 4 分半钟。构建反正按源的周期会再跑，
  检查结果经监控通道上报。发布 DAG 与 `maintenance_partitions` 保留重试

### 部署

顺序要紧：21 先跑，monitor 这个登录用户才能通过事务池的检查；22 要在 55 之前，55 的最后一步要检查 Prometheus 已经在采集它。

```bash
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K
ansible-playbook playbooks/40-schema.yml -e ansible_connection=local -K
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml -e ansible_connection=local -K
ansible-playbook playbooks/22-containers.yml -e ansible_connection=local -K
ansible-playbook playbooks/55-monitor.yml -e ansible_connection=local -K
ansible-playbook playbooks/60-svc.yml -e ansible_connection=local -K
```

全部跑完后，按同样的顺序再跑一遍，每个都应是 `changed=0`。

| 剧本 | 第一遍应该看到 |
| --- | --- |
| 21 | pgBouncer 的 HBA 多了 monitor 那一行；HAProxy 配置变了，重新载入 |
| 40 | 新建登录用户 `monitor` 及系统账号 `oss911mon`，写出 `monitor.env`；monitor 经事务池登录成功、在会话池被拒；权限验证 222 项全过 |
| 30 | 接入程序 `oss911-ingest 0.4.0`；Airflow 四个服务的 unit 变了，会重启 |
| 45 | 新建发布目录，四个合同都是 unchanged |
| 50 | DAG 文件更新 |
| 22 | 「promtool checks passed」为 2；Prometheus 重启；「Alert rules loaded: 22 rules in 5 groups」 |
| 55 | 「11 queries without error」，以及当前各预期检查的状态（刚部署时多为 `grace`）；/metrics 有响应；`up{job="oss911_monitor"} = 1` |
| 60 | 查询服务的 unit 变了，会重启；其余照旧 |

30 重启 Airflow 时，正在跑的任务会被打断，下一个周期会自动重跑。

### 验证

**一、采集目标都是 up：**

```bash
curl -s 127.0.0.1:9090/api/v1/targets | python3 -c "import json,sys; [print(t['labels']['job'], t['health'], t.get('lastError')) for t in json.load(sys.stdin)['data']['activeTargets']]"
```

应看到 `prometheus`、`haproxy`、`oss911_monitor` 三行，都是 `up`。

**二、现在有哪些告警：**

```bash
curl -s 127.0.0.1:9090/api/v1/alerts | python3 -c "import json,sys; [print(a['state'], a['labels']['severity'], a['labels']['alertname'], a['annotations']['summary']) for a in json.load(sys.stdin)['data']['alerts']]"
```

`OSS911Heartbeat` 一定在，它常亮。其余各条都要能讲清来由，把输出贴给我。开发机上可能出现的：
- `OSS911ReferenceStale`：假接口里某个参照版本很久没变
- `OSS911QuarantineWaiting`：有待处理的隔离批次

**三、实测一次「源中断」**（可选，大约 20 分钟）：在跑 `start.sh` 的终端按 Ctrl+C 停掉假接口，等话单连续失败 3 次（约 15 分钟）。
上面那条查询应出现 `firing page OSS911SourceFailing`，`icp_cdr_voice` 先出，坐席两个源随后。重启假接口，下一次成功后告警消失。

## 第三块：Alertmanager、假网关、心跳检查与每日汇总

### 这一块在做什么

**1. 投递链路**

```
Prometheus（规则）→ Alertmanager → webhook → 投递端 → 收件人（短信 / 邮件）
```

- **Alertmanager**（容器，v0.34.2，按 digest 钉死）：负责分组、重复和静默，所有通知都经同一个 webhook 交给投递端。按级别分三路：
  - `page`：立即发，持续未恢复的每 4 小时重发一次
  - `notify`：每 24 小时重发一次
  - `heartbeat`：每分钟发一次，不发恢复消息

  恢复的告警也会发出。同名告警合成一条通知：标题写第一处，再注明「另有几处」，正文逐处列出。比如一次源中断让 5 张表同时封账落后，
  只发一条短信，不是五条。配置部署前由镜像自带的 amtool 检查，规则仍由 promtool 检查。
- **投递端**：生产上是统一投递服务（LD-01 10.2，尚未建）。开发机上是假网关 `test/fake_gateway/server.py`，以自己的账号 `oss911gw`
  运行（`oss911-fake-gateway.service`），只绑 `10.89.10.1:9196`。它**不发任何东西**，把要发的内容写成文件，每个收件人的每个渠道一行：

  | 文件（`/var/lib/oss911gw/`） | 内容 |
  | --- | --- |
  | `outbox.jsonl` | 会发给谁、经什么渠道、标题与正文。夜间不发的短信也写一行，标 `"skipped": "night"` |
  | `received.jsonl` | 收到的每条原始消息（心跳除外）。将来投递服务的 `audit.delivery_log` 留的就是这份交接点证据 |
  | `heartbeat.json` | 每个来源的心跳最后到达时刻 |

- **收件人是配置**：inventory 的 `alert_recipients` 写明三件事，56 把它写成 `/etc/oss911/alert-recipients.json` 给投递端读，
  假网关每条消息都重读一次，改了不用重启：
  - 每个人收哪几级
  - 每一级走什么渠道：`page` 发短信加邮件，`notify` 和 `digest` 只发邮件
  - 夜间几点到几点不发短信，以及哪些人夜间也收短信

  开发机只有一个占位收件人。谁收、夜间是否发短信，要等运维方确认。

**2. 心跳检查**（`oss911-heartbeat-check.timer`，每分钟一次）

规则 `OSS911Heartbeat` 一直处于触发状态，每分钟走一遍整条链路。检查任务问投递端「心跳最后什么时候到的」：
- 超过 5 分钟没到，就认为链路断了。它**不经 Alertmanager**（断的可能正是它），直接经投递端发一条 `page`：
  「Alert path broken」。之后每 4 小时再发一次，恢复时发一次「recovered」。
- 检查任务每次运行都写一个状态文件（`/var/lib/oss911mon/alertpath/`），导出程序把它导出成指标，再由规则盯住：

  | 规则 | 何时响 | 级别 |
  | --- | --- | --- |
  | `OSS911HeartbeatCheckStopped` | 检查任务超过 5 分钟没运行 | page |
  | `OSS911HeartbeatCheckMissing` | 没有状态文件 | page |
  | `OSS911AlertPathCheckFailing` | 检查任务连不上投递端 | page |

  也就是说，链路由检查任务盯着，检查任务又由链路盯着。

**3. 每日汇总**（`oss911-digest.timer`，每天 08:00 Asia/Riyadh）

范围是前一个自然日，数据从 Prometheus 读，经投递端以 `digest` 级发出，不靠 Alertmanager 的重复通知。内容分五节：

1. 当天响过的每条告警：几点到几点、共几分钟、分几段，已恢复的也列。到日终仍在响的单独标出
2. WARN 测试：当天有变化的列出前后行数；没变化的每项一行，写明从何时起没变
3. 只进汇总的项：最老的待澄清已挂几天（超过 7 天标出）、已删除坐席未返回数、日累计与话单对账的结果
4. 日终仍不正常的预期检查
5. 覆盖率：导出程序当天有多少比例的分钟在被采集。低于 99% 时会写明「这些时段本汇总无法担保」——没有数据的一天不等于平静的一天

任何一节读不到，标题就带 `INCOMPLETE`，其余各节照常发。同一天的汇总只发一次。汇总任务本身也写状态文件：超过 26 小时没运行响
`OSS911DigestMissing`，运行失败响 `OSS911DigestFailed`，都是 notify。

**4. 顺带改的两处**

- **封账落后的天数原来写了两处，现在由部署时核对：** 夜间测试 `mart_closing_not_behind` 改为读 dbt 变量 `closing_lag_days`。
  55 会核对这个变量和 `contracts/monitoring.yml` 里的同名阈值，不相等就拒绝部署。
- **`fact_call_leg_without_first_leg` 改成每通电话一行：** 原来不管缺多少通，都只返回一行汇总，`failures` 永远是 1，
  所以 `OSS911WarnIncreased` 对它永远不会响。真机现在有 3 通，**部署后的第一次构建它会从 1 变成 3，发一封 notify 邮件**，这是预期的。

**5. 已知局限：单节点**

开发机只有一台。整机宕机、断电、断网时，Prometheus、Alertmanager、检查任务和假网关一起停，**没有任何东西会发现**。
检查任务也依赖投递端：投递端本身宕了，它谁也通知不到，只能把状态写成失败。这个失败要经链路才能报出来，而链路的终点正是投递端。

生产上两个节点互查，消除这一局限：每个节点的检查任务盯对端的心跳，配置是 `heartbeat_source` 指向对端，投递服务也是两节点。
本方不依赖总部或总包商的监控平台（DD-100 评审注第五条）。

### 部署

改了接入程序包（0.5.0）、dbt 的两个测试和变量、监控配置的注释，以及 Prometheus 和 Alertmanager 的配置。数据库结构没有变化，
不用跑 21、40、60。

```bash
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml -e ansible_connection=local -K
ansible-playbook playbooks/22-containers.yml -e ansible_connection=local -K
ansible-playbook playbooks/55-monitor.yml -e ansible_connection=local -K
ansible-playbook playbooks/56-alerting.yml -e ansible_connection=local -K
```

全部跑完后，按同样的顺序再跑一遍。56 第二遍的「Post a test alert」那一步不计 changed，所以每个剧本都应是 `changed=0`。

| 剧本 | 第一遍应该看到 |
| --- | --- |
| 30 | 接入程序 `oss911-ingest 0.5.0` |
| 45 | 新建发布目录 |
| 50 | DAG 清单与 dbt 项目更新 |
| 22 | 拉取 Alertmanager 镜像；「amtool: the Alertmanager configuration is valid」；「promtool checks passed」；Prometheus 与 Alertmanager 启动或重启，Keycloak、SeaweedFS 不重启；「Alert rules loaded: 27 rules in 5 groups」；「Prometheus sends to http://alertmanager:9093/api/v2/alerts」 |
| 55 | `monitor.conf` 多了一行 `MONITOR_STATE_DIR`，导出程序重启；「Monitoring configuration valid (15 expected checks)」 |
| 56 | 建账号 `oss911gw`，起假网关和两个定时器；「Heartbeat from dev01 last arrived at …」；「Test alert delivered: 1 outbox line(s)」；最后打印昨天汇总的标题 |

22 之后、56 之前的几分钟里，Alertmanager 连不上假网关，会在自己的日志里报 webhook 错误并重试。56 起来之后就会把积压的告警补发出去。

### 验证

**一、看投递端收到了什么：**

```bash
sudo tail -n 20 /var/lib/oss911gw/outbox.jsonl | python3 -c "import json,sys; [print(d['at'], d['level'], d['recipient'], d['channel'], d.get('skipped') or '', d['subject'][:100]) for d in map(json.loads, sys.stdin)]"
```

预期能看到这几类：
- `OSS911DeliveryTest`：一条 FIRING，两分钟后一条 RESOLVED
- 第二块部署后至今仍在响的 page 告警
- 首腿测试变成 3 之后的那一封 `OSS911WarnIncreased`

把输出贴给我。

**二、停掉 Alertmanager，看检查任务能否报出来**（约 8 分钟，Mac 侧要求的验证）：

```bash
sudo systemctl stop alertmanager
# 等 6 到 7 分钟
sudo grep path_ /var/lib/oss911gw/outbox.jsonl | tail -n 2
sudo systemctl start alertmanager
# 再等 1 到 2 分钟
sudo grep path_ /var/lib/oss911gw/outbox.jsonl | tail -n 2
```

停掉 5 分钟后，下一分钟应出现 `path_failure`：`page` 级，邮件一行，短信一行。夜间（22:00–07:00）的短信那行带 `"skipped": "night"`。
重新启动后应出现 `path_recovered`。停掉期间也可以看检查任务自己的状态：

```bash
sudo cat /var/lib/oss911mon/alertpath/heartbeat_check.json
```

**三、每日汇总：**

```bash
sudo -u oss911mon /opt/oss911/venv-ingest/bin/oss911-alertpath digest --print
```

这条命令只打印，不发送。明天 08:00 后，`outbox.jsonl` 里应有一条 `"kind": "digest"`，可用 `systemctl list-timers 'oss911-*'`
看下次运行时间。

**四、定时任务被盯住了：**

```bash
curl -s '127.0.0.1:9090/api/v1/query?query=oss911_alertpath_last_run_ok' | python3 -m json.tool | grep -E '"task"|"1"|"0"'
```

应看到 `heartbeat_check` 和 `digest` 两项，值都是 1。导出程序缓存 60 秒，56 跑完后要等一两分钟才有。

