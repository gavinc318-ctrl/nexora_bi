# 第 12 步 —— CAD 中间库 cad_mirror：DS 往里写，我们从里读

## 这一步在建什么

DS 的同步程序把 CAD 的 74 张表原样写进本方集群上的一个**独立数据库** `cad_mirror`；本方接入程序以 `oss911_ingest`
只读它（DD-90）。这一步建这个库，以及它的属主、DS 的写入账号、pgBouncer 单独池。

| 内容 | 在哪 |
| --- | --- |
| 结构变更（独立序列，自己的登记表 `ddl.ddl_change`） | `ddl/cad_mirror/changes.yml`、`ddl/cad_mirror/changes/` |
| 0001 的生成器（开发工具，不交付） | `tools/cad_mirror_ddl.py`；列目录 `contracts/cad_mirror_catalog.csv` |
| 剧本 | `infra/playbooks/41-cad-mirror.yml` |
| 结构变更执行器（与 oss911 共用） | `infra/playbooks/tasks/ddl_changes.yml` |
| DS 写入账号的口令 | `/etc/oss911/cad_mirror_writer.env`（root 0600，交给 DS，本方不用） |

## 先懂四件事

### 一、两个角色，只有一个能登录

| 角色 | 能做什么 |
| --- | --- |
| `cad_mirror_owner` | 不能登录。库和库里一切对象的属主 |
| `cad_mirror_writer` | DS 用。对 `cad` 下的表只能查、插、改，对 `cad_meta` 只能插（心跳与每日计数）；不能删、清空、建表、动触发器；语句超时 120 秒、事务空闲超时 60 秒（规范第 4 节） |

**它连不上任何别的库。** PostgreSQL 默认让所有登录都能连所有库；这一轮起，`oss911`、`airflow`、`keycloak`、`postgres`、
`template1` 的 CONNECT 都不再对所有人开放，只给真正用它的角色（`tasks/db_connect.yml`，由 20 / 22 / 30 / 40 各自执行）。
41 最后以写入账号的权限逐项核对 19 条。

### 二、两列时间戳归我们

每张表多两列 `mirror_inserted_at`、`mirror_updated_at`，由触发器维护，DS 写什么都会被覆盖。`mirror_updated_at` 是接入的
水位列：**只有内容真的变了它才动**——DS 把一行原样再 UPSERT 一次，触发器让这次更新变成「什么都没发生」，不产生新的行版本、
也不推时间戳。DS 重发一整天、甚至整表重写，都不会让我们把没变的行当成新数据再取一遍（DD-78）。

### 三、开发阶段 DDL 还会改

DDL 由本方生成、DS 核对会签（IR-51），会签前后都可能再改。约定（Gavin 2026-10-26）：**正式软件发布之前**，已执行的变更文件
可以改写，改了之后把开发库整个重建；**发布之后**（inventory `cad_mirror_ddl_frozen: true`），和 oss911 一样，执行过的变更
永不修改，改动只能追加新变更。

### 四、列名不用加引号

翻译列名里有 21 个是 PostgreSQL 保留字（`order` 19 处、`primary`、`collation`），已改名：排序 / 序号 → `sort_order`，
是否首要 → `is_primary`，排序规则 → `sort_rule`；`t_dispatchplan.yxj`（优先级）原译 `order` 是错的，改为 `priority`。
生成器遇到新的保留字会直接失败，不会生成要加引号的列名。

## 执行

这一轮动到了 20、21、22、30、40 五个已有剧本（收回 CONNECT、pgBouncer 加池、执行器抽出共用），所以按顺序全部重跑一遍：

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/20-database.yml   -e ansible_connection=local -K
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K
ansible-playbook playbooks/22-containers.yml -e ansible_connection=local -K
ansible-playbook playbooks/30-python.yml     -e ansible_connection=local -K
ansible-playbook playbooks/40-schema.yml     -e ansible_connection=local -K
ansible-playbook playbooks/41-cad-mirror.yml -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 |
| --- | --- |
| 20 | 「Restrict CONNECT on the maintenance databases」两项 changed |
| 21 | pgbouncer.ini 加了 `cad_mirror` 池，pgBouncer **重启**，等约 90 秒（troubleshooting 第 29 条）；auth_query 探测照常通过 |
| 22 / 30 | 「Restrict CONNECT on keycloak / airflow」changed |
| 40 | 「Restrict CONNECT on oss911」changed；结构变更显示 `Structural changes 0000 to 0004 executed, checksums match`——执行器抽出共用后行为不变 |
| 41 | 建两个角色与库，执行 cad_mirror 的 0000、0001，最后 `cad_mirror: all 19 privilege checks pass` |

**每个剧本第二遍都应 `changed=0`。**

看一眼库里有什么：

```bash
sudo -u postgres psql -d cad_mirror -P pager=off -c "SELECT change_id, summary FROM ddl.ddl_change"
sudo -u postgres psql -d cad_mirror -P pager=off -c "SELECT count(*) AS tables FROM pg_tables WHERE schemaname = 'cad'"
```

## 开发阶段重建

改了 `tools/cad_mirror_ddl.py` 或 `ddl/cad_mirror/changes/` 里已执行的文件后，41 会在校验和那一步失败并提示重建：

```bash
python3 ~/src/nexora_bi/tools/cad_mirror_ddl.py > ~/src/nexora_bi/ddl/cad_mirror/changes/0001_mirror_tables.sql
ansible-playbook playbooks/41-cad-mirror.yml -e ansible_connection=local -K -e cad_mirror_rebuild=true
```

`cad_mirror_rebuild=true` 会**删掉整个 cad_mirror 库**再建（库里 DS 写入的数据一并没了，开发库可以接受）。
`cad_mirror_ddl_frozen: true` 之后剧本拒绝这个开关。

## 开发机上验不到的

- DS 真正写入：需要 DS 的同步程序、入向网络通道（IR-52）与 TLS
- 心跳围栏下的取数上界（DD-90），等 IF-02 契约与 pg_read 连接器
