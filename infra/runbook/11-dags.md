# 第 11 步 —— Airflow DAG：每 5 分钟一次增量，每天一次回扫

## 这一步在做什么

把 `src/dags/` 部署进 Airflow 的 DAG 目录（`/var/lib/airflow/dags`）。`oss911_ingest.py` **只读 DAG 发布清单**
`/opt/oss911/release/dags.json`，不读契约 YAML（LD-01 2.1）。清单由 45-contracts 在契约同步进库之后、作为最后一步
从数据库生成（`oss911-ingest dag-manifest`）：列出库内全部受管的源（停用的也在），各自的 schedule、pool（来自
`contracts/pools.yml`）与契约版本。每个源生成：

| DAG | 何时 | 做什么 |
| --- | --- | --- |
| `ingest_icp_cdr_voice` | 每 5 分钟（契约 `window.schedule`） | `oss911-ingest run --source icp_cdr_voice`——第 10 步手工跑的那条 |
| `resweep_icp_cdr_voice` | 每天 02:00（源端时区，ICP 为 UTC） | `… --mode resweep --day <前一天>`：那一整天再往前多一个回看长度，补长于回看的通话（LD-01 4.4）；不动水位；跑完报告两个数——没收到末腿的、没有**可用**末腿的（末腿在待处理或已丢弃批次里不算），记在 run_log 的 message 里 |

回扫的那一天来自这次运行的**逻辑日期**，不来自「现在」：失败重试扫的还是同一天；DAG 开着补跑（catchup），停了几天再开，
漏掉的每一天按顺序补上。从哪一天开始补，写在 `contracts/resweep.yml`——**首次上线时由人填**；没填的源没有回扫 DAG。

两个 DAG 走同一个 pool，不会同时下载。**顺序上 45-contracts 在前、50-dags 在后**：没有清单，DAG 文件报导入错误。

## 先懂四件事

### 一、Airflow 只编排，不取数

取数、校验、写 raw、推进水位都在接入程序里（DD-84）。DAG 只管：几点跑、进哪个 pool、失败重试几次。

### 二、并发由两道闸管住

| 闸 | 管什么 |
| --- | --- |
| 全局 pool `icp_cms`（1 槽） | Airflow 内部：整个集群同一时刻只有一个 ICP 下载任务——pool 是元数据库里的一行，两台 ETL 共用（DD-85） |
| 接入程序的咨询锁 | Airflow 之外：手工补数绕过了 pool，锁把它挡住。拿不到锁退出码 1 |

### 三、任务的几个固定设置

`skip_on_exit_code=None`（否则退出码 99 会被当成「跳过」）；重试 2 次、间隔 2 分钟，不分退出码；30 分钟超时——
超时时 Airflow 发 SIGTERM，接入程序回滚正在下载的那一批、写 run_log、以 1 退出，已提交的批不受影响；
同一源同一时刻只跑一个；**不补跑**（catchup=False）——停了一天再开，下一次运行自己从水位追到现在（第 10 步
见过：三天的缺口切成 4 批）。任务的环境变量只给 `PATH` 与 `LANG`：Airflow 进程的环境里有元数据库的连接串，
不该传给接入程序；接入程序自己读 `/etc/oss911`（LD-01 2.5）。

### 四、新 DAG 是暂停的，开启是首次上线的验收步骤

Airflow 新建的 DAG 默认暂停，50-dags **重复部署不改变暂停状态**。一个源第一次上线按这个顺序，缺一步都不算上线：

1. 人工执行一次增量，带 `--start-at` 建立水位（runbook/10 第二步）。没有水位时增量以退出码 2 结束，不会自己起算
2. 开启 `ingest_<源>` 与 `resweep_<源>`
3. 确认第一个自动任务 `success`，run_log 里它的 `watermark_from` 接着第 1 步的 `watermark_to`
4. 在 `contracts/resweep.yml` 填上这个源的回扫起始日（通常就是上线那天），重新部署 45 与 50，再开启 `resweep_<源>`

手工触发一次回扫要带逻辑日期，否则没有「哪一天」可扫：

```bash
af dags trigger resweep_icp_cdr_voice -l 2026-10-26T02:00:00+00:00     # 扫 2026-10-25
```

**源的启停**是 `meta.source_contract.enabled`（M-01），DAG 的暂停是运维用的，不是第二个开关。

## 执行

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra && ansible-playbook playbooks/50-dags.yml -e ansible_connection=local -K
```

前提：本轮的 45-contracts 已跑过（`/opt/oss911/release/dags.json` 已写出）。

第一遍：「Copy the DAG files」changed；最后一步显示 `DAG ingest_icp_cdr_voice present`。`resweep_icp_cdr_voice`
是新 DAG，处于暂停；`ingest_icp_cdr_voice` 保持你上次留下的状态。第二遍 `changed=0`。

以下 Airflow 命令都以 airflow 账号、带它的环境文件执行，先定义一个便于粘贴的函数：

```bash
af() { sudo -u airflow bash -c 'set -a; . /etc/oss911/airflow.env; set +a; exec /opt/oss911/venv-airflow/bin/airflow "$@"' _ "$@"; }
```

**一、起假接口**（另一个终端）：`~/src/nexora_bi/test/fake_icp/start.sh`

**二、开 DAG，等两三个周期**

```bash
af dags unpause ingest_icp_cdr_voice
# 等 10 分钟左右
af dags list-runs ingest_icp_cdr_voice -o table | head
sudo -u postgres psql -d oss911 -P pager=off -c "SELECT outcome, row_count, watermark_from, watermark_to, finished_at FROM meta.run_log ORDER BY finished_at DESC LIMIT 5"
```

预期：每 5 分钟一行 `success`；run_log 每 5 分钟一行，`watermark_from` 接着上一行的 `watermark_to`。

任务日志在 `/var/lib/airflow/logs/dag_id=ingest_icp_cdr_voice/` 下，内容就是接入程序的标准输出：

```bash
sudo find /var/lib/airflow/logs/dag_id=ingest_icp_cdr_voice -name '*.log' | sort | tail -1 | xargs sudo tail -5
```

**三、并发测试：DAG 在跑时手工再跑一次**（DD-85）

```bash
# 让假接口每次下载慢 40 秒，制造一个「DAG 任务正在下载」的窗口
curl -s -X POST localhost:18080/_fake/control -d '{"delay_seconds": 40}'; echo
af dags trigger ingest_icp_cdr_voice
sleep 15
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest run --source icp_cdr_voice; echo "exit=$?"
curl -s -X POST localhost:18080/_fake/control -d '{"delay_seconds": 0}'; echo
```

预期：手工那次 `run failed: another run of icp_cdr_voice holds the advisory lock (DD-85)`，`exit=1`；DAG 那次
40 秒后 `success`。run_log 里多一行 failed，原因写明撞锁。

**四、测完停 DAG**

```bash
af dags pause ingest_icp_cdr_voice
```

## 开发机上验不到的

- 两台 ETL 双活下 pool 是否真的全局生效、任务是否会在两台之间漂移（DD-85），须在测试环境双节点验证
- 每日回扫还没有 DAG：时刻与覆盖范围待 Mac 侧定（NOTES 2026-10-21）
