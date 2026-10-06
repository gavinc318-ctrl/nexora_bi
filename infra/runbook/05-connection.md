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

剧本已断言「HAProxy 这一跳可用」与「两个池的池模式确实不同」。下面三条是人工补验，
验的是**行为**而不是配置：

```bash
# 1 经事务池能查，且会话状态不保证保留
psql -h 127.0.0.1 -p 6432 -U <业务用户> -d oss911 -c "SELECT 1"

# 2 经会话池能用临时表——这一条在事务级池上会随机失败，正是两个池存在的理由
psql -h 127.0.0.1 -p 6433 -U <业务用户> -d oss911 <<'SQL'
CREATE TEMP TABLE t(x int);
INSERT INTO t VALUES (1);
SELECT count(*) FROM t;
SQL

# 3 后端连接数真的被闸住了
sudo -u postgres psql -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'"
```

第 2 条值得多跑两次。临时表在事务级池上**不是必然失败**——客户端恰好拿到同一个后端
连接时会成功。这类「有时候好使」的缺陷最难排查，所以要在此刻、在空闲的机器上、
用能重复的方式确认它走的是会话池。

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
