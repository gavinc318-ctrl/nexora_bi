# 第 13 步 —— dbt：ICP 话单从 raw 进 staging，再进 core.fact_call_leg

## 这一步在做什么

接入程序已经把话单原样放进 `raw.icp_cdr_voice`。这一步让 dbt 把它变成能算指标的形状（DD-91）：

| 层 | 模型 | 做什么 |
| --- | --- | --- |
| staging | `stg_icp_cdr_voice` | 类型化（空串当空值、时间按契约时区解释）、语义映射（呼叫方向、挂断方）；每个原始观测一行 |
| core | `fact_call_leg` | 每张话单（CALLID + CALLIDNUM）一行；取最新观测，**只写内容真的变了的行** |

代码在 `src/dbt/`，随发布目录部署到 `/opt/oss911/dbt`；Airflow 的 `build_icp_cdr_voice` 每 5 分钟跑一次，`maintenance_partitions`
每天 01:30 UTC 预建分区。

## 先懂五件事

### 一、按批次消费，不按时间

staging 只取「可处理、本模型还没登记」的批次，处理完在同一事务里登记到 `meta.staging_batch_state`（DD-89）。被隔离的批次
等放行后的下一次构建自动补进来——不需要任何人记得去补。core 对 staging 也是同样的登记方式。

### 二、只写变了的行

core 每次只写：新出现的话单；比现有行更新的观测、且派生内容不同；同一观测、派生内容变了（归类表更正、维度补齐）。
**更旧的观测不会覆盖更新的**——周一的批次周四才放行，不会把周二的数据改回去（DD-82）。没变的行一行都不动（DD-78）。

### 三、24 小时防线

同一张话单两次观测的 WAITBEGIN 相差超过 24 小时，不当作新版本写入 core，进待澄清队列（`meta.clarification_queue`）。
CALLID 是否会跨天重复还没问清（Q20），这条防止两通不同的电话被静默合并成一张。

### 四、未映射就是未映射

呼叫方向：值表外的 CALLTYPE 记 `unmapped`，不归入「内部」；挂断方只看末腿，归类表里没有的码记 `unmapped`（DD-06）。
坐席、技能队列维度还是空的（参照数据未接入），`agent_sk` / `queue_sk` 先留空，维度接入后重算填上。

### 五、core 不能被 dbt 重建

core 表的属主是 `oss911_owner`，dbt 只能往里写。`--full-refresh` 对 core 模型被禁止（项目配置 `full_refresh: false`），
这是期望的行为。要整体重算，清掉 core 模型的登记让它重新处理——见文末。

## 执行

这一轮有两个新结构变更（0005、0006）、接入程序改动、契约 v4、dbt 项目与两个新 DAG，按顺序：

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/40-schema.yml    -e ansible_connection=local -K
ansible-playbook playbooks/30-python.yml    -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 |
| --- | --- |
| 40 | 执行 0005、0006；`Structural changes 0000 to 0006 executed`；权限验证全过 |
| 30 | 重装接入程序（回扫批次改记 `resweep`） |
| 45 | 新发布目录（含 `dbt/`）；`icp_cdr_voice\|updated\|4`；清单 `written` |
| 50 | 安装 dbt 项目到 `/opt/oss911/dbt`；新增 `build_icp_cdr_voice`、`maintenance_partitions` 两个 DAG（暂停） |

**每个剧本第二遍都应 `changed=0`。**

### 第一次构建（手工，看清楚输出）

```bash
sudo -u airflow bash -c 'set -a; . /etc/oss911/dbt.env; set +a; cd /tmp && DBT_SEND_ANONYMOUS_USAGE_STATS=false \
  /opt/oss911/venv-dbt/bin/dbt build --select stg_icp_cdr_voice fact_call_leg \
  --project-dir /opt/oss911/dbt --profiles-dir /var/lib/airflow \
  --target-path /var/lib/airflow/dbt/target --log-path /var/lib/airflow/dbt/logs'
```

预期：两个模型 `OK created`，十几项测试全部 `PASS`，最后 `Done. PASS=... ERROR=0`。第一次会把 raw 里已有的全部话单写进 core。

再跑一次：两个模型都是 `INSERT 0 0`——没有新数据就一行不写。

### 看结果

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT count(*) AS raw FROM raw.icp_cdr_voice"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT count(*) AS legs, count(DISTINCT call_id) AS calls, min(date_key), max(date_key) FROM core.fact_call_leg"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT direction, count(*) FROM core.fact_call_leg GROUP BY 1"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT release_source, count(*) FROM core.fact_call_leg WHERE is_last_leg GROUP BY 1"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT model, count(*) FROM meta.staging_batch_state GROUP BY 1"
```

core 的行数会**少于** raw：raw 里同一张话单的多次观测在 core 只留一行。

### 开启自动构建

```bash
af dags unpause build_icp_cdr_voice
af dags unpause maintenance_partitions
```

增量每 5 分钟进 raw，构建每 5 分钟把它带进 core；两者各自独立，不互相等。

## 整体重算 core（例如归类表更正之后）

```bash
sudo -u postgres psql -d oss911 -c "DELETE FROM meta.staging_batch_state WHERE model = 'fact_call_leg'"
```

下一次构建会把 staging 里的全部观测重新算一遍，**只写派生内容变了的行**。staging 本身可用
`dbt build --full-refresh --select stg_icp_cdr_voice` 从 raw 重建（它的登记在同一事务里清掉）。

## 开发机上验不到的

- 真实话单的口径（合成数据只验代码路径，AS-32）
- 维度到达后 `agent_sk` / `queue_sk` 的填充（参照数据 if03d 未接入）
