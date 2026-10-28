# 第 14 步 —— ICP 参照数据：坐席、技能队列、小码表（if03d）

## 这一步在做什么

ICP 的配置同步接口（synwas，接口文档 p.907–933）给出坐席、坐席组、技能队列、技能组、休息原因、业务类型、被叫配置。
这一步把它们接进来，建成维度（DD-94）：

| 层 | 对象 | 做什么 |
| --- | --- | --- |
| raw | `raw.icp_reference` | 版本号变了，就把该数据集整份快照写入，每数据集一批（结构变更 0007） |
| staging | `stg_icp_reference` | 每份快照的每条记录一行，带版本与同步时刻 |
| core | `dim_agent`、`dim_queue` | SCD2：变了就关旧版、插新版，行不改写、不删除 |
| meta | `meta.reference_value` | 坐席组、技能组、休息原因、业务类型：源端原文进 `src_label`，译名归 M-10 |

被叫配置（接入码路由）只落 raw，不进任何维度（DD-94 第十条）。

| DAG | 何时 | 做什么 |
| --- | --- | --- |
| `ingest_icp_reference` | 每 30 分钟 | 先取版本号；版本没变就什么都不做（run_log 记 success、0 行） |
| `build_icp_reference` | 每 30 分钟 | dbt 构建上面四个模型；顺带把 core 里已有话单的坐席、队列关联补上 |

两个 DAG 分别与话单的接入、构建共用 pool（`icp_cms`、`dbt_build`），不会同时跑。

## 先懂六件事

### 一、版本号按 VDN 计，不按数据集

一个 VDN 的版本号覆盖它的全部七个数据集；数据集 `vdns` 用 CTI 级的版本号。版本相等不取；更大就取；**更小不应用**，
在待澄清队列开一条（可能是平台回滚，也可能读到了滞后的节点，须由人判断）。只在同步状态为「成功」时取数。

### 二、GET 读的是网关的副本

网关的副本要靠网关自己的同步刷新；本方**不调**同步接口（那是写动作，删 VDN 时会连带删除）。网关会不会自动同步待 Q33
答复。副本的同步时刻超过 `version_stale_days`（默认 7 天，可热调）没变，运行日志的 message 以 `STALE:` 开头。

### 三、整份快照，所以能看出「消失」

快照里不再有的坐席或队列：维度里置为非当前（关版），不删行——历史话单还指着它。快照突然变空而上一份不空，或同一快照
里工号重复，整批隔离，不应用。

### 四、版本从什么时候生效

- 第一次见到的坐席或队列：从哨兵 `1970-01-01 00:00 UTC` 起——第一版覆盖观测之前的全部历史，否则第一份快照之前的
  话单都关联不到坐席。这是推断，DD-94 第五条接受了它
- 之后的变化：从网关那次同步的 `endTime` 起
- M-10 的主管标记：从标记的 `valid_from` 起，到 `valid_to` 止（撤销 = 关 `valid_to`，不删行）

版本按「源端快照的同步时刻 ∪ 标记的起止时刻」逐点切分，每个变化点一版（DD-94 十六）：08:00 同步的改名和 09:00 起的
主管标记，即使在同一次构建里应用，也是两版。一次构建会应用所有待应用的快照，不再一次一份。

维度只增不改，所以**不能切到已有最新版本之前**：起始时刻早于该坐席最新版本的标记（补登过去的事），从最新版本之后
一秒起生效，留痕的 reason 里写明「mark valid from …, versions before are not rewritten」。起始时刻在将来的标记，
到点之后的那次构建才生效。

### 五、角色：默认接警员，主管由 M-10 标

ICP 的 `roleId` 是 CMS 的权限角色，不是业务角色。ICP 工号只有接警员与其主管（AS-36），所以一律 `call_taker`，主管
由 M-10 写进 `meta.agent_role_override`（带生效区间，结构变更 0008），下一次构建生成新版本。M-10 还没做，现在用 SQL
写（见文末）。

### 七、只用本中心的 VDN

契约 `endpoint.site_vdn` 写明本中心的 ccId 与 vdnId（开发机是假接口的 1 / 1，真实值待 Q31）。版本号里出现的其他 VDN
照常接入 raw、进待澄清，不进维度；`site_vdn` 不在版本号里，也进待澄清。staging 不登记其他 VDN 的批次，所以改正
`site_vdn` 之后，下一次构建会把它们补进来。

快照里有任何一条记录缺自然键（如工号为空），该数据集整批隔离（DD-94 十五）：缺键的快照不完整，不能据以判断谁消失了。

### 八、话单里的工号在配置里不存在

话单的坐席或队列在通话开始时没有有效的维度版本（例如已从 ICP 删除，仍有话单），关联为空、不造「未知坐席」；每个工号
开一条待澄清（`src_column` 以 `of call legs, not in the configuration at their start (DD-95)` 结尾），未处理前不重复开。
mart 将按「未关联」单独成桶（DD-95）。

### 六、话单的坐席、队列关联会被补上

`fact_call_leg` 在维度到之前写入的腿，`agent_sk` / `queue_sk` 是空的。维度模型每次有变化，就把涉及的那些坐席、
队列的腿重新关联到「通话开始时有效的那一版」，只写关联真变了的行。第一次构建会把现有的全部腿补上。

## 执行

本轮有结构变更 0007、接入程序改动、契约 if03d v2、dbt 新模型与新 DAG。（2026-10-28 起另有 0008、契约 v3，见文末「0008 与契约 v3」。）假 ICP 接口也加了 synwas 接口，**要重启**：
到它的终端按 Ctrl+C，`git pull` 之后再 `test/fake_icp/start.sh`。

> 假接口的配置副本只在内存里，每次重启都从版本 13 开始。已经取过更高版本之后再重启，接入程序会看到「版本倒退」，
> 开一条待澄清、不应用——这正是对真实平台归零（Q30）的设计行为。开发中要继续往前，先查已取到哪一版，再把假接口的
> 版本设成比它大的**具体数字**（查出来为空，说明还没取过，不用设）：
>
> ```bash
> sudo -u postgres psql -d oss911 -At -c "SELECT max(snapshot_version) FROM meta.ingest_batch WHERE source_id = 'icp_reference' AND snapshot_scope LIKE 'agents@%'"
> curl -s -X POST localhost:18080/_fake/control -d '{"ref": {"set_version": 15}}'    # 例：上一条查出 14
> ```

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
| 40 | 执行 0007；`Structural changes 0000 to 0007 executed`；权限验证全过（141 项） |
| 30 | 重装接入程序 |
| 45 | 新发布目录；`icp_reference\|inserted\|2`；清单 `written`，多出 `icp_reference` |
| 50 | 安装 dbt 新模型；新增 `ingest_icp_reference`、`build_icp_reference` 两个 DAG（暂停） |

**每个剧本第二遍都应 `changed=0`。**

### 第一次取数与构建（手工，看清楚输出）

```bash
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest run --source icp_reference; echo "exit=$?"
```

预期：8 批（`vdns@1`、`agents@1/1` …），每批一行 `batch N <数据集>@… vN (was None): M record(s)`，`exit=0`。
再跑一次：`0 batch(es)`——版本没变。

```bash
sudo -u airflow bash -c 'set -a; . /etc/oss911/dbt.env; set +a; cd /tmp && DBT_SEND_ANONYMOUS_USAGE_STATS=false \
  /opt/oss911/venv-dbt/bin/dbt build --select stg_icp_reference dim_agent dim_queue reference_value \
  --project-dir /opt/oss911/dbt --profiles-dir /var/lib/airflow \
  --target-path /var/lib/airflow/dbt/target --log-path /var/lib/airflow/dbt/logs'
```

预期：四个模型 `OK created`，测试全部 `PASS`，`ERROR=0`。再跑一次：四个模型都是 `INSERT 0 0`。

### 看结果

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT count(*) AS agents, count(*) FILTER (WHERE is_current) AS current, min(valid_from) FROM core.dim_agent"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT src_natural_key, name_en, media_type_code FROM core.dim_queue ORDER BY 1"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT domain, count(*) FROM meta.reference_value GROUP BY 1"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT device_type_code, count(*) AS legs, count(agent_sk) AS with_agent, count(queue_sk) AS with_queue FROM core.fact_call_leg WHERE device_type_code IN (1, 2) GROUP BY 1"
```

最后一条：坐席腿（2）的 `with_agent`、队列腿（1）的 `with_queue` 应与 `legs` 相等——历史话单都关联上了。

### 开启

```bash
af dags unpause ingest_icp_reference
af dags unpause build_icp_reference
af dags list -o plain | grep -E 'icp_|maintenance'      # 六个 DAG，都应是 False
```

## 在开发机上验一次变化（可选）

假接口的控制端点能模拟网关同步了一次（`test/fake_icp/README.md`）：

```bash
curl -s -X POST localhost:18080/_fake/control -d '{"ref": {"rename": {"agentNo": "101", "agentName": "Renamed"}}}'
curl -s -X POST localhost:18080/_fake/control -d '{"ref": {"remove": "102"}}'
```

等两个 DAG 各跑一次（或手工跑上面两条命令），然后：

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT src_natural_key, name_en, valid_from, valid_to, is_current FROM core.dim_agent WHERE src_natural_key IN ('101','102') ORDER BY 1, valid_from"
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT target_key, field_name, old_value, new_value FROM audit.reference_data_change_log ORDER BY change_id DESC LIMIT 5"
```

101 应有两版（旧名关于同步时刻、新名从同步时刻起）；102 只有一版且已关闭；留痕表里有对应的逐字段记录。

## M-10 之前：手工标记主管

```bash
# 标记：从某个时刻起是主管
sudo -u postgres psql -d oss911 -c "
INSERT INTO meta.agent_role_override (site_code, src_system, agent_no, role_code, valid_from, changed_by, reason)
VALUES ('MKK', 'icp', '<工号>', 'supervisor', '<起始时刻，如 2026-10-28 09:00+03>', '<你的名字>', '<依据>')"

# 撤销：关区间，不删行
sudo -u postgres psql -d oss911 -c "
UPDATE meta.agent_role_override SET valid_to = '<结束时刻>'
 WHERE site_code = 'MKK' AND agent_no = '<工号>' AND valid_to = 'infinity'"
```

同一坐席的两段标记不能重叠（排他约束 `ex_agent_role_override_overlap`）。下一次 `build_icp_reference` 在起止时刻各切
一版。M-10 做好后这一步走界面，并写留痕。

## 若测试 `fact_call_leg_dimension_links` 失败

说明有话单的坐席或队列关联与维度不一致。话单模型与两个维度模型以事务级咨询锁串行（`lock_call_leg_links`），手工构建
也不会与 Airflow 的构建交错；万一仍出现（例如加锁之前留下的），重新关联全部：

```bash
sudo -u airflow bash -c 'set -a; . /etc/oss911/dbt.env; set +a; cd /tmp && DBT_SEND_ANONYMOUS_USAGE_STATS=false \
  /opt/oss911/venv-dbt/bin/dbt run-operation relink_all_call_legs \
  --project-dir /opt/oss911/dbt --profiles-dir /var/lib/airflow \
  --target-path /var/lib/airflow/dbt/target --log-path /var/lib/airflow/dbt/logs'
```

## 若测试 `reference_version_without_change` 报 WARN

最新版本与上一版内容完全相同：版本号可能在每次同步时都递增，而不是「配置变了才递增」。数据没有问题，但这条假设要
向 ICP 核实（Q34）。下一次真正有变化的版本到来后，警告自然消失。

## 若测试 `fact_call_leg_without_dimension` 报 WARN

有坐席腿或队列腿找不到「通话开始时有效的那一版」：工号在那个时刻不在配置里。常见于坐席或队列已从 ICP 删除，话单里仍有它
（在开发机上：假接口删了某个坐席之后，话单生成器仍派话单给它）。关联为空是对的，不要手工改关联；结果按工号列出条数与首末
时刻，与 `core.dim_agent` / `core.dim_queue` 里该工号的 `valid_to` 对照即可。这类腿在指标里怎么算尚未定（NOTES 2026-10-28）。

## 开发机上验不到的

- 网关会不会自动同步、多久一次（Q33）；版本号会不会归零（Q30）；内容不变的同步会不会升版本（Q34）
- 真实的类型与字段（文档的类型与样例不一致，DD-94 〇-i）
- 多 VDN（AS-30 / Q31）：程序会照常接入并开待澄清，但事实表没有 VDN 维

## 0008 与契约 v3（2026-10-28，DD-94 十五至十七 · DD-95）

结构变更 0008 用到扩展 `btree_gist`（PostgreSQL 自带的 contrib），它由 20-database 按 inventory 的 `pg_extensions`
创建——owner 角色不能建扩展，所以**先跑 20，再跑 40**：

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/20-database.yml  -e ansible_connection=local -K
ansible-playbook playbooks/40-schema.yml    -e ansible_connection=local -K
ansible-playbook playbooks/30-python.yml    -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 |
| --- | --- |
| 20 | `Enable extensions` 一项 changed（btree_gist），其余不变 |
| 40 | 执行 0008；`Structural changes 0000 to 0008 executed`；权限验证全过（147 项） |
| 30 | 重装接入程序 |
| 45 | 新发布目录；`icp_reference\|updated\|3` |
| 50 | 安装新的 dbt 项目 |

已有的主管标记（0007 的表）原样搬进新表：`valid_from = set_at`，区间开放。没有标记过的，新表是空的。

