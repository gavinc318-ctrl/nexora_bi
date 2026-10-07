# 第 5 步 —— 连接层：pgBouncer + HAProxy

## 这一步在建什么

```
应用 ──► pgBouncer :6432 事务级   ┐
     └─► pgBouncer :6433 会话级   ├─► HAProxy :5000 ──► 当前主库
                                  ┘   （健康检查决定谁是主）
```

顺序不能颠倒。pgBouncer 的 `[databases]` 里只能写一个固定地址，它自己**没有选主能力**；
知道谁是主库的是 Patroni，HAProxy 读它的健康检查。把 HAProxy 放在 pgBouncer 后面，
pgBouncer 的后端就永远是 `127.0.0.1:5000`，主库易主与它无关。

反过来放（HAProxy 在前）的话，每个 pgBouncer 只能绑死一台库，池就跟着库走了，
备库上那个池平时全闲置——集中控制连接数的意义就没了。

## 两个池，以及一条必须一起记住的约束

| 池 | 端口 | 模式 | 给谁 |
| --- | --- | --- | --- |
| `oss911` | 6432 | transaction | 门户查询、Superset、服务层接口 |
| `oss911_etl` | 6433 | session | Airflow、dbt、人工运维 |

事务级省连接，但 `SET`、临时表、advisory lock、预处理语句**不保证落在同一个后端连接上**；
会话级保行为，但**连接在客户端断开前不归还**。

**会话池的大小就是 dbt 的并发上限。** 当前是 10，dbt 的 `threads` 就不能超过 10。
超了不会报错，会在池上排队，表现为「dbt 卡住但看不到任何错误」——
这两个数绑在一起，调一个必须同时调另一个。

## 连接数怎么算

```
Σ(每个 pgBouncer 实例 × 每个池 × 每个 (用户,库) 组合的 pool_size) + 保留名额 ≤ max_connections
```

`default_pool_size` 是**每个 (用户, 数据库) 组合**的池大小，不是全局上限——
三个用户连同一个库就是三个池。真正的全局闸是 `max_db_connections` 与 `max_user_connections`，
这两项必须显式设，否则池数一多会悄悄超过库侧的 `max_connections`，表现为随机拒连。

开发机（单机、一个 pgBouncer）：

```
事务 30 + 会话 10 + 预留池 5 = 45   （全局闸 max_db_connections = 45）
保留名额：superuser 3 + 监控 2 + 人工维护 5 = 10
合计 55，对 max_connections = 100 有近一倍余量
```

余量留这么大是故意的：开发机要能同时承受「dbt 在跑 + 人在 psql 里查 + 监控在采」。

**不占这个额度的两样**：分析副本的池连的是另一台库；流复制自 PostgreSQL 12 起
`max_wal_senders` 不再计入 `max_connections`。

## 跑

```bash
# Mac 上
rsync -av --delete infra/ nexora-bi:~/infra/

# 开发机上
cd ~/infra
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K --check --diff
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K   # changed 应为 0
```

`--check` 这一遍能验的是连接预算断言与包列表，验不了集群配置（理由同第 4 步）。

## 人工核验

剧本已断言「HAProxy 这一跳可用」与「两个池的池模式确实不同」。下面是人工补验，
验的是**行为**而不是配置。需要一个带口令的验证用户——pgBouncer 的 `auth_query`
会从 `pg_shadow` 取它的口令，不必加进 `userlist.txt`。

```bash
# 建一个只读的验证用户，验完即删
sudo -u postgres psql -c \
  "CREATE ROLE verify_pool LOGIN PASSWORD 'TempVerify#2026' IN ROLE pg_read_all_data"

# 1 会话池上，临时表必须可用。这是两个池存在的全部理由
PGPASSWORD='TempVerify#2026' psql -h 127.0.0.1 -p 6433 -U verify_pool -d oss911 <<'SQL'
CREATE TEMP TABLE t(x int);
INSERT INTO t VALUES (1);
SELECT count(*) AS should_be_1 FROM t;
SQL

# 2 事务池上能正常查询（短事务，这是它该干的事）
PGPASSWORD='TempVerify#2026' psql -h 127.0.0.1 -p 6432 -U verify_pool -d oss911 \
  -c "SELECT 1"

# 3 后端连接数确实被闸住
sudo -u postgres psql -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'"

# 删掉验证用户
sudo -u postgres psql -c "DROP ROLE verify_pool"
```

**第 1 条必须成功**，它是确定性的：会话级池把同一个客户端连接固定在同一个后端上。

**不要指望用第 1 条的反例去「证明」事务池会失败。** 单个客户端在空闲的机器上跑，
pgBouncer 多半会把同一个后端连接反复给它，临时表因此**也能成功**——
这正是事务级池化危险的地方：它不是必然失败，是**偶尔失败**。
要让它确定性地暴露，得有并发客户端把后端连接打散：

```bash
# 可选：两个客户端并发时，事务池上的临时表才会露出问题
for i in 1 2; do
  PGPASSWORD='TempVerify#2026' psql -h 127.0.0.1 -p 6432 -U verify_pool -d oss911 \
    -c "CREATE TEMP TABLE t$i(x int)" -c "SELECT count(*) FROM t$i" &
done; wait
```

看到 `relation "t1" does not exist` 之类的报错就对了——那不是故障，是事务级池化的
固有行为，也是 dbt 与 Airflow 必须走会话池的原因。**生产上这类失败会在负载高时
才出现，排查成本极高**，所以这一条要在现在、在空闲的机器上看一眼，建立直觉。

## 开发机不等于生产的两处

| | 开发机 | 生产 |
| --- | --- | --- |
| HAProxy 后端 | 一个（本机库） | 三个（主、同步备、分析副本） |
| 健康检查 | `pgsql-check` | Patroni REST `/primary` |

**生产必须用 Patroni 的 HTTP 检查，不能用 TCP 端口探活。** 备库的 5432 一样是通的，
TCP 探活会把写流量送到只读备库，症状是应用随机报
`cannot execute INSERT in a read-only transaction`——看着像应用的 bug，
实际是健康检查选错了方式。

这两项都在 `inventory/dev.yml` 里，剧本与模板两边完全一样。
**选主与切换在开发机上验不了**，那属于 LD-08 的演练规程；开发机上能验的是拓扑、
两个池的行为与连接数上限。
