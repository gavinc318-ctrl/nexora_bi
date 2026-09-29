# 04 PostgreSQL 与 PostGIS

## 本步为什么单独成步

集群创建是**一次性不可逆**的：locale 在 `initdb` 时确定，事后无法在线更改，
改要重建整个集群。所以先把它验对，再往上叠 pgBouncer 与 HAProxy（第 05 步）。

## 执行

```bash
rsync -av --delete infra/ nexora-bi:~/infra/          # Mac 上

cd ~/infra                                            # 开发机上
ansible-playbook playbooks/20-database.yml -e ansible_connection=local -K --check --diff
ansible-playbook playbooks/20-database.yml -e ansible_connection=local -K
ansible-playbook playbooks/20-database.yml -e ansible_connection=local -K   # changed 应为 0
```

## 顺序有一处不能颠倒

**必须在 `apt install postgresql` 之前**写好 `/etc/postgresql-common/createcluster.conf`
（`create_main_cluster = false`）。

Ubuntu 的 postgresql 包会在 postinst 里自动建一个 main 集群，并**沿用系统 locale**
（`en_US.UTF-8`）——那正是 DD-63 要避免的绑定 glibc 的情形。让它先建出来再删掉重建，
既费时又容易留残留。剧本里这两个任务的先后是刻意的。

若已经装过 postgresql 且集群是自动建的，先删干净：

```bash
sudo pg_lsclusters                                   # 看现状
sudo pg_dropcluster 16 main --stop                   # 删（有数据先备份）
sudo ansible-playbook playbooks/20-database.yml -e ansible_connection=local -K
```

## 验证 locale（DD-63 的核心）

剧本会断言，但自己也看一眼：

```bash
sudo -u postgres psql -c "\l"
```

`oss911` 与 `postgres` 两行的 Collate / Ctype 都应是 **C**，Encoding 是 **UTF8**。

再验 ICU 真的在起作用——**这一条才说明列级 collation 有意义**：

```bash
sudo -u postgres psql -d oss911 <<'SQL'
CREATE TEMP TABLE t(ar text COLLATE "ar-x-icu");
INSERT INTO t VALUES ('أحمد'),('احمد'),('إبراهيم'),('ابراهيم');
SELECT 'ICU  ar: ' || string_agg(ar,'  ' ORDER BY ar) FROM t;
SELECT '字节序 C: ' || string_agg(ar,'  ' ORDER BY ar COLLATE "C") FROM t;
SQL
```

两行结果**必须不同**。ICU 会把 همزة 变体归到一起（أحمد 与 احمد 相邻），
字节序则把同一个名字的两种拼法拆到列表两端。911 的单位名与地名里 أ/ا/إ 混用是常态，
这个差别在报表上是肉眼可见的错。

若两行相同，说明列级 collation 没生效，往下做没有意义。

## ICU 版本漂移检查

ICU 升级会改变排序规则，PostgreSQL 会记录 collation 版本并在不符时告警——
这正是选 ICU 而非 glibc 的原因：**它会告诉你**，而 glibc 是静默失效。

定期跑（也应纳入 M-08 运行监控）：

```bash
sudo -u postgres psql -d oss911 -c "
SELECT collname, collversion AS 记录版本,
       pg_collation_actual_version(oid) AS 实际版本
FROM pg_collation
WHERE collprovider = 'i'
  AND collversion IS NOT NULL
  AND collversion <> pg_collation_actual_version(oid);"
```

**有输出即需处置**：找出依赖该排序规则的索引并 `REINDEX`，然后
`ALTER COLLATION <名> REFRESH VERSION`。顺序不能反——先 REINDEX 再刷版本号，
否则就是把「已过期」的标记抹掉而索引仍是旧的。

## 参数：开发与生产不是一套

`files/postgresql-oss911.conf.j2` 头部写明了，这里再说一次：
`shared_buffers` 取 8 GB 而非惯用的内存 25%（16 GB），因为**这台不是专用库机**，
余量要留给 Superset、Airflow 与前端构建。生产的库虚机按 RS-01 另行配置。

`idle_in_transaction_session_timeout = 300000`（5 分钟）不是可选项：
一条忘记结束的事务会阻塞清理、累积 WAL、撑大磁盘——一个人的疏忽通过复制链路
反噬写入侧。靠规范防不住，只能靠硬超时。

## 下一步

第 05 步 `21-connection.yml`：pgBouncer（事务级池）与 HAProxy（单后端）。
两者在开发环境都不是性能需要，而是**让连接层的行为与生产一致**——
生产的服务层是经它们连库的，不装则事务池语义与重连行为的差异会留到上线后才暴露。
