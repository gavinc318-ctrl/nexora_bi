# 第 8 步 —— 数据库结构：结构变更、库用户、初始分区

## 这一步在建什么

前七步搭的是空的平台。这一步第一次往业务库 `oss911` 里放东西：

| 内容 | 来源 | 结果 |
| --- | --- | --- |
| 结构变更 0000 | `ddl/changes/0000_ddl_change.sql` | 登记表 `meta.ddl_change`：每执行一个变更记一行 |
| 结构变更 0001 | `ddl/01..05_*.sql`（LD-02，原样） | 七个 schema、50 张表、分区函数、55 条索引 |
| 结构变更 0002 | `ddl/changes/0002_roles_and_grants.sql` | 五个权限角色、属主、授权（DD-74） |
| 结构变更 0003 | `ddl/changes/0003_quarantine_and_param_log.sql` | 隔离登记表（DD-76）、热调留痕表（DD-75） |
| 库登录用户 | inventory 的 `db_logins` | `ingest`、`dbt` 两个用户，口令在 `/etc/oss911/{ingest,dbt}.env` |
| 初始分区 | `meta.ensure_monthly_partitions` | 13 张分区表按月分区（DD-77），各建上月、本月、后 3 个月 |

## 先懂三件事

### 一、「结构变更」，不叫「迁移」

行业里这东西叫 migration，本项目刻意不用这个词：**「迁移」在本项目专指 Hexagon
归档数据迁入**（CN-15 · DD-37）。两件事同名，交接时一搜就混（DD-73）。

### 二、为什么不用 `CREATE TABLE IF NOT EXISTS`

它在「表已存在但定义变了」时**静默跳过**——剧本第二遍照样 `changed=0`，库里却还是
旧结构，没有任何报错。本步的做法是：

```
ddl/changes.yml             清单：编号 → 由哪些文件组成
meta.ddl_change             库里的登记表：执行过哪些、当时文件的 SHA-256

剧本每次运行：
  已登记、校验和相同   →  跳过
  已登记、校验和变了   →  失败（已执行的变更不许改，要改就追加新变更）
  未登记               →  按编号执行，每个变更连同登记行放在一个事务里
```

所以「能不能重跑」看的是**这个变更执行过没有**，而不是对象在不在。
回退也不是撤销，而是追加一个反向变更（LD-08 7.5）。

### 三、五个权限角色，和两个登录用户

角色是「权限包」，**不能登录**；登录用户本身不持有任何授权，只是加入某个权限包：

```
权限角色（NOLOGIN，变更 0002 建）       登录用户（本剧本建）     谁用
oss911_owner      持有全部对象           —                       没有程序以它运行
oss911_ingest     raw 只能 SELECT/INSERT  ingest                  接入（Airflow 调起）
oss911_transform  dbt                     dbt                     dbt（Airflow 调起）
oss911_query_svc  服务层查询路径          —（随服务层建）
oss911_admin_svc  服务层管理路径          —（随服务层建）
```

raw「只追加」（DD-02）从此**由数据库执行**：`ingest` 对 raw 执行 UPDATE / DELETE /
TRUNCATE 会被直接拒绝，而不是靠写代码的人记得。

**新用户不需要改 pgBouncer 的 `userlist.txt`**——那里只放 pgbouncer 自己，其余用户
经 `auth_query` 现查（第 5 步）。

## 跑

### 0  前置检查（只读，什么都不改）

```bash
cd ~/src/nexora_bi && git status --short && git log --oneline -1   # 工作区干净、是最新提交

# 库在、连接层在
sudo -u postgres psql -d oss911 -tAc "SELECT current_database(), version()"
systemctl is-active pgbouncer haproxy postgresql@16-main

# 库里此刻还是空的：下面应输出 0（只有 public 与系统 schema）
sudo -u postgres psql -d oss911 -tAc \
  "SELECT count(*) FROM pg_namespace WHERE nspname IN ('raw','staging','core','mart','ai','meta','audit')"
```

### 1  第一遍

```bash
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/40-schema.yml -e ansible_connection=local -K
```

`-K` 会问一次 sudo 口令。预期看到这些关键行（顺序如下）：

```
TASK [断言变更清单的编号连续、run_as 合规]      变更清单合规：4 个变更，0000 至 0003
TASK [断言待执行的变更都排在已执行的之后]        待执行：0000 0001 0002 0003
TASK [显示执行输出]                              ==> 结构变更 0000 … 完成（共四段）
TASK [断言全部变更已执行且校验和一致]            结构变更 0000 至 0003 均已执行，校验和一致
TASK [ingest：断言登录与权限角色均生效]          ingest：经 pgBouncer 登录成功 …
TASK [dbt：断言登录与权限角色均生效]             dbt：经 pgBouncer 登录成功 …
TASK [显示新建分区数]                            本次新建分区 65 个
TASK [断言库角色与授权验证全部通过]              库角色与授权验证：N 项全部通过（已回滚；N 随检查增加而变）
PLAY RECAP   … failed=0
```

65 = 13 张分区表 × 5 个月（上月 + 本月 + 后 3 个月）。分区按月不按天（DD-77）：
按天的话八年加上归档约 8.5 万个分区，查询会撞上锁数上限，而单日数据量只有十余万行，
按天切分换不来任何查询收益。

### 2  第二遍：必须 `changed=0`

```bash
ansible-playbook playbooks/40-schema.yml -e ansible_connection=local -K
```

这一遍应看到：`待执行：无`、四个变更各一行「已执行，文件未改动」、新建分区 0 个、
**`changed=0`**。不是 0 就说明有一步每次都在改东西，把输出贴回来。

### 3  dbt 的 profile 加 `role:`（重跑第 7 步）

本步改了 `infra/files/dbt_profiles.yml.j2`，加了一行 `role: oss911_transform`。
这个文件归 30-python 管，所以要重跑 30-python 让它生效：

```bash
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K   # 预期 changed=1：profiles.yml
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K   # changed=0
```

wheelhouse 已有内容，30-python 不会去联网取料。

**为什么这行重要**：dbt 建的表属主是「建表时的角色」。设了 `role:`，dbt 连上后
`SET ROLE oss911_transform`，mart 里的新表就归它，0002 里预设的默认权限随之生效，
服务层能读到；不设，属主是登录用户 `dbt`，默认权限不生效，服务层读不到 mart——
而且不报错，要到服务层查询时才发现（DD-74 · LD-02 13.2）。

## 人工核验

剧本里已有断言，下面几条是让你**亲眼看见**这一步建出了什么：

```bash
# 1 登记表：四个变更、谁执行的、校验和
sudo -u postgres psql -d oss911 -c \
  "SELECT change_id, applied_at, applied_by, left(content_sha256, 12) AS sha, summary FROM meta.ddl_change ORDER BY 1"

# 2 七个 schema 与属主：raw core meta audit 归 oss911_owner；staging mart ai 也归它（dbt 往里建表）
sudo -u postgres psql -d oss911 -c '\dn'

# 3 角色与成员关系：五个 NOLOGIN 权限角色；ingest、dbt 分别是谁的成员
sudo -u postgres psql -d oss911 -c '\du oss911_*' -c '\drg ingest|dbt'

# 4 每层多少张表、多少个分区
sudo -u postgres psql -d oss911 -c "
  SELECT n.nspname,
         count(*) FILTER (WHERE c.relkind IN ('r','p') AND NOT c.relispartition) AS 表,
         count(*) FILTER (WHERE c.relkind = 'r' AND c.relispartition)            AS 分区
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname IN ('raw','staging','core','mart','ai','meta','audit')
  GROUP BY 1 ORDER BY 1"
#   预期：audit 7 张表 · core 24 · meta 22；分区 core 50、audit 15（分区名形如 fact_call_p202610）
#   只数 relkind = 'r'：分区表上的索引也是「分区」（relispartition 为真），不加这条会数出 core 300

# 5 亲手试一次「raw 只追加」：以 ingest 身份删 raw 里的东西，应被拒绝
#   raw 现在还没有表，借 meta.run_log 演示同一件事——ingest 能写、不能删：
sudo -u airflow bash -c 'set -a; . /etc/oss911/ingest.env; set +a;
  PGPASSWORD=$INGEST_DB_PASSWORD psql -h 127.0.0.1 -p 6432 -U $INGEST_DB_USER -d oss911_etl \
    -c "DELETE FROM meta.run_log WHERE false"'
#   预期：ERROR:  permission denied for table run_log

# 6 dbt 以新用户 + role: 连库（走会话池，一次走完 dbt → pgBouncer → HAProxy → PostgreSQL）
sudo -u airflow bash -c 'cd /tmp; set -a; . /etc/oss911/dbt.env; set +a;
  /opt/oss911/venv-dbt/bin/dbt debug --connection --profile oss911 --profiles-dir /var/lib/airflow'
#   预期最后一行：All checks passed!

# 7 权限验证可以随时单独再跑（只读，事务结尾回滚）
sudo -u postgres psql -d oss911 -X < ~/src/nexora_bi/test/db_roles_check.sql 2>&1 | tail -1
#   预期：db_roles_check: 全部通过（已回滚，未留痕）
```

第 5 条用的是 `sudo -u airflow`：环境文件属主是 airflow、模式 0600，只有它读得到——
用你自己的账号或 root 去读，验的就不是真实使用方那条路径了。

## 常见失败

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `变更 000X 已在库中执行，但它的文件被改过` | 已执行变更的文件被改了 | 用 `git log -p ddl/` 找到改动，把文件恢复原样；要改的内容写成新变更追加到 `changes.yml` 末尾。**不要**去改 `meta.ddl_change` 里的校验和 |
| `待执行的变更 … 中有编号小于已执行的` | 在清单中间插了变更 | 新变更只能追加在末尾，改编号 |
| 执行变更时报 SQL 错误 | 变更本身有错 | 该变更已整体回滚、未登记，库停在上一个变更。修好文件重跑即可——它从未执行过，所以可以改 |
| `角色 oss911_xxx 已存在但属性不符` | 同名角色被人改成了可登录或带特权 | 查 `\du oss911_*`，弄清是谁改的再决定；不要直接删 |
| `库用户 xxx 已存在但属性不符` | 同名登录用户不是本剧本建的 | 同上 |
| `password authentication failed` | 环境文件与库中口令不一致 | 删掉 `/etc/oss911/<用户>.env` 重跑剧本：生成新口令并 ALTER ROLE（一次轮换） |
| 新建分区数报错 `permission denied for function ensure_monthly_partitions` | 0002 的 EXECUTE 授权没生效 | 把输出贴回来，这是变更本身的问题 |
| `库角色与授权验证` 失败 | 某个权限与设计不符 | FAIL 行写明了「哪个身份、哪条语句、期望允许还是拒绝」；按上面第 7 条单独跑看完整输出 |

## 开发机不等于生产的三处

| | 开发机 | 生产 |
| --- | --- | --- |
| DDL 来源 | 从仓库复制到 `/opt/oss911/ddl` | 发布制品解包到同一位置，`meta.ddl_change.release_version` 填制品版本（DD-49） |
| 分区前推 | 编排未就位，3 个月后重跑本剧本前推 | Airflow 定期调 `meta.ensure_monthly_partitions` 提前建出未来月份；分区缺失使当日写入失败，属必须监控项 |
| 服务层登录用户 | 未建 | 随服务层建，加入 `oss911_query_svc` / `oss911_admin_svc` |
