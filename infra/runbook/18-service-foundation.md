# 第 18 步 —— 服务层的地基：读取规则、服务登录用户、Keycloak realm（DD-99 · LD-04 3.5 · LD-07）

## 这一步在做什么

第 9 步服务层（查询 API）的第一块。服务本身还没装（下一块写代码，再下一块部署），这一步先把它要用的三样东西备好：

| 东西 | 在哪 | 给服务层做什么 |
| --- | --- | --- |
| mart 读取规则 | `meta.mart_rule`（结构变更 0013），由 46 从 `metrics/marts.yaml` 同步 | 哪些角色能读哪张表、按什么维度、哪些列敏感、接警员只见本人；**表里没有的 mart 表，查询 API 不读** |
| 服务的库登录用户 `svc` | `/etc/oss911/svc.env`（0600，属系统账户 `oss911svc`，家目录 `/var/lib/oss911svc`） | 属 `oss911_query_svc`：读 core · mart · meta，对审计表只能 INSERT；**只走事务池 `oss911_txn`** |
| Keycloak realm `oss911` | 剧本 23 | 四个角色、两个客户端、两个用户属性（工号 `agent_no`、部门 `agency_type_code`）、七个开发测试用户 |

同时部署第 8 步第二段收尾的改动（Mac 侧答复）：第五张 mart `calls__day__repeat_failed`（M-C15）、46 核对参数与 dbt 变量、
被丢弃的回扫批次视同定稿、封账落后告警（runbook 17）。

## 先懂三件事

### 一、服务层的库用户只走事务池，dbt 和入库只走会话池

pgBouncer 有两个池（DD-67）：会话池 `oss911` 给 dbt 与入库（它们要临时表、SET ROLE、咨询锁），事务池 `oss911_txn` 给服务层
（一个请求一个事务，连接用完即还）。连错池不会立刻报错，只会出怪事，所以 pgBouncer 的 HBA 在登录时就拒绝（DD-93）：
40 的验证同时做两件事——`svc` 经事务池登录成功、经会话池被拒（`login rejected`）。HBA 由 21 按 inventory 的 `db_logins` 生成，
所以**先跑 21，再跑 40**。

### 二、令牌里的工号与部门，用户自己改不了

接警员只能看本人的坐席行，靠的是令牌里的 `agent_no`。如果用户能在 Keycloak 的个人账户页改它，就能冒充别的接警员。
所以这两个属性在 realm 的用户配置里声明为「只有管理员能改」——已实测：用户自己改工号，Keycloak 返回 `error-user-attribute-read-only`。

### 三、管理员口令从哪里来

剧本 23 要以 Keycloak 管理员身份建 realm。`keycloak.env` 里还有引导管理员（22 首次安装时生成）就用它；已经按 runbook 91
换成正式管理员的，剧本会停下来问管理员的用户名和口令（口令不回显）。测试用户的口令在第一次运行时生成，只存
`/etc/oss911/keycloak-test-users.env`（root，0600），之后照用。

## 执行

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/21-connection.yml   -e ansible_connection=local -K   # HBA 多一行：svc 不许用会话池
ansible-playbook playbooks/40-schema.yml       -e ansible_connection=local -K   # 0013；建 svc；权限验证
ansible-playbook playbooks/45-contracts.yml    -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml         -e ansible_connection=local -K   # 第五张 mart、两个新检查

af() { sudo -u airflow bash -c "set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow $*"; }
af dags trigger publish_marts_intraday            # 建出 mart.calls__day__repeat_failed；等它成功
ansible-playbook playbooks/46-metrics.yml      -e ansible_connection=local -K   # M-C15 已实现；读取规则进 meta.mart_rule
ansible-playbook playbooks/23-keycloak-realm.yml -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 | 第二遍 |
| --- | --- | --- |
| 21 | HBA 文件 changed，pgBouncer reload | `changed=0` |
| 40 | 执行 0013；`oss911svc` 系统账户与 `svc` 用户建出；`svc: logged in through pgBouncer`（经 `oss911_txn`）与 `rejected on oss911`；权限验证全过 | `changed=0` |
| 50 | DAG 与 dbt 项目更新 | `changed=0` |
| intraday | 成功；日志里 `mart.calls__day__repeat_failed` 是 `OK created` | — |
| 46 | `M-C15|redefined|2|implemented`（上线前原地改口径）；五张表 `rule_inserted`；`mart tables the query API may read: 5` | `every metric and mart rule unchanged` |
| 23 | realm、角色、客户端、映射、七个用户建出；末尾七行 `verified: … sub ok`，ct101 的 `agent_no=101`（`sub ok`：令牌的 sub 就是该用户在 Keycloak 里的 id，服务按它认人） | `changed=0`，仍七行 `verified` |

之后下一次 nightly（或手动触发一次）把 `calls__day__repeat_failed` 的历史日子封账。

**假接口要重启一次**才会生成「同一主叫反复打不通」的新通话（已有通话不变）：在开着它的终端里 Ctrl-C，再用原来的命令启动。
补数靠回扫：之后每天 02:00 的回扫会把新通话带进来；想马上有，对最近几天各补跑一次回扫（runbook 15）。

## 核验

```bash
# 读取规则：五张表，坐席处理表是 self、按 agent_no
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT mart_table, roles, row_filter, self_column, required_dimensions FROM meta.mart_rule ORDER BY 1"

# svc 走事务池能读 mart、不能写；走会话池被拒（以 oss911svc 账户读它自己的 env 文件）
sudo -u oss911svc bash -c 'set -a; . /etc/oss911/svc.env; set +a
  PGPASSWORD="$SVC_DB_PASSWORD" psql -X -tA -h 127.0.0.1 -p 6432 -U "$SVC_DB_USER" -d oss911_txn \
    -c "SELECT count(*) FROM mart.calls__day__volume" \
    -c "DELETE FROM mart.calls__day__volume WHERE false" 2>&1
  PGPASSWORD="$SVC_DB_PASSWORD" psql -X -tA -h 127.0.0.1 -p 6432 -U "$SVC_DB_USER" -d oss911 -c "SELECT 1" 2>&1'
```

预期：五行规则；第二条依次输出一个行数、`permission denied for table calls__day__volume`、`login rejected`。

Keycloak 管理控制台（http://127.0.0.1:8080/admin，realm 选 oss911）里能看到 Users 下七个测试用户、Realm roles 下四个角色；
点开 ct101 → Attributes，`agent_no` 是 101。
