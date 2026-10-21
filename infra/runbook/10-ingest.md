# 第 10 步 —— 接入程序：ICP 话单从假接口落进 raw

## 这一步在做什么

`oss911-ingest run` 第一次真正取数：从 ICP 话单接口（开发机上是假接口，AS-32）按窗口取 CSV，校验，写进
`raw.icp_cdr_voice`，登记批次、隔离与待澄清项，推进水位，写运行日志（LD-01 2.5 · DD-88 · DD-89）。

还没有 Airflow DAG：这一步手工执行。DAG 是下一步，它只是按 5 分钟调同一条命令。

## 先懂四件事

### 一、一次运行 = 若干批，一批一个事务

窗口按接口单次上限（24 小时）切成若干批，串行取。每批在一个事务里写完：批次登记、raw 行、隔离登记、
待澄清项、水位。某一批失败只回滚这一批，**后面的批不再取**——水位不会越过缺口。运行结束写一行
`meta.run_log`：`success`（全部提交，含被隔离的批）、`partial`（提交了一部分）、`failed`（一批都没提交）。

### 二、两种模式

| 模式 | 窗口 | 水位 |
| --- | --- | --- |
| 增量（默认，DAG 用） | 水位减回看 → 源端时区的「现在」 | 每批提交后推进到该批终点，只前进 |
| 补数 `--mode backfill --window 起/止` | 显式给出 | **不动**（DD-89） |

一个源第一次增量运行时还没有水位，必须给一次 `--start-at`；之后再给会被拒绝（要取更早的时段用补数）。

### 三、坏数据也进 raw

批次校验（契约 if03a 的 batch_checks，写成代码在 `src/ingest/src/oss911_ingest/cdr.py`）不通过时，**整批照常写进
raw，并登记 `meta.quarantine_batch`**，退出码仍是 0——隔离是设计好的结果，不是失败。CALLTYPE 不在值表内的
不隔离，进 `meta.clarification_queue`（同一个值只开一条）。表头与契约不一致也整批隔离，新列以 `unknown`
登记 `meta.mapping_registry`。

### 四、去重

同一（CALLID, CALLIDNUM, 内容哈希）已在 raw 的正常或已放行批次里，就不再写。所以每 5 分钟回看重取的
那 15 分钟大部分不落地，只有变了的话单作为新观测追加。待处理与已丢弃批次里的行不参与去重：好话单第一次
恰好落进被隔离的批，下一次取到会照常写入正常批（DD-82）。

## 执行（开发机）

前提：30-python（新版接入程序）与 45-contracts（契约 v2）已跑过。

**一、起假接口**（另开一个终端，一直开着；账号与 `icp.env` 一致，见 runbook/09）：

```bash
cd ~/src/nexora_bi
eval "$(sudo grep -E '^OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT_(USER|PASSWORD)=' /etc/oss911/icp.env)"
python3 test/fake_icp/server.py --port 18080 --seed 1 \
  --account "$OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT_USER:$OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT_PASSWORD"
```

**二、第一次增量**，以接入程序真实运行的身份（airflow）执行，水位从 2 小时前开始（时间按 UTC，契约
`window.source_timezone`）：

```bash
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest run --source icp_cdr_voice \
  --start-at "$(date -u -d '-2 hours' +%Y-%m-%dT%H:%M:00)"
echo "exit=$?"
```

预期：1 批，received 与 written 相等，`success`，exit=0。

**三、立刻再跑一次**（不带 `--start-at`）：received 是回看那 15 分钟的行数，written 只有个位数——去重生效。

**四、注入坏数据再跑**：

```bash
curl -s -X POST localhost:18080/_fake/control -d '{"inject": ["inversion", "dup_callidnum", "unknown_calltype"]}'
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest run --source icp_cdr_voice; echo "exit=$?"
```

预期：该批 `QUARANTINED: …`，exit=0。

**五、补数一天**（水位不动）：

```bash
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest run --source icp_cdr_voice \
  --mode backfill --window 2026-10-18T00:00:00/2026-10-19T00:00:00; echo "exit=$?"
```

**六、看结果**：

```bash
sudo -u postgres psql -d oss911 -c "SELECT outcome, row_count, watermark_from, watermark_to FROM meta.run_log ORDER BY finished_at"
sudo -u postgres psql -d oss911 -c "SELECT batch_seq, mode, window_from, window_to, row_count, rows_written FROM meta.ingest_batch ORDER BY committed_at"
sudo -u postgres psql -d oss911 -c "SELECT state, failure_reason FROM meta.quarantine_batch"
sudo -u postgres psql -d oss911 -c "SELECT src_column, src_value, impacted_rows, status FROM meta.clarification_queue"
sudo -u postgres psql -d oss911 -c "SELECT * FROM meta.watermark_state"
```

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功，含有批次被隔离；或源已停用（什么都不做） |
| 1 | 运行失败或部分失败（看 run_log.message）；或同一源另有实例持锁（DD-85） |
| 2 | 用法或配置错误：缺配置文件、契约不在库里、首次运行没给 `--start-at`、水位在未来…… |

## 开发机上验不到的

- 真实接口的时区、窗口边界、静默截断等（假接口的 19 条猜测，IR-45）
- 原文件归档（DD-87）还没做：无法解析的文件在开发机上只留下批次登记与隔离原因，原文没有留存。上线前必须就位
- 双节点下锁与 pool 的行为（DD-85）
