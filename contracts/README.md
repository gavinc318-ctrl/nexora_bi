# contracts —— 源契约

`meta.source_contract` 是库内的只读副本，**本目录是权威**（HLD 9.2）。
改契约走发布流程：改文件、递增 `contract_version`、随发布制品导入。
库里改不了，M-01 界面上也只能看。

## 纪律

1. **没有契约的数据一律不接。** 源端已经准备好了也不接——契约同时承载幂等键声明、
   目标 raw 表指向与版本号，缺了它，重跑的幂等性与结构漂移检测都无从谈起（LD-01 2.1）。
2. **每一列都要有一行登记。** 状态取 `verified` / `mapped_unverified` / `unknown`。
   结构漂移发现的新列默认落 `unknown`。空白会被遗忘，未知会被统计并排期（LD-01 2.3）。
3. **凭据不入契约。** 只写引用名 `credential_ref`，实际值由部署时注入（LD-01 2.4）。
   `source_contract` 是业务人员在 M-01 上看得到的表。
4. **枚举取值不写死在模型里。** 取值集合登记在契约的 `enums` 段，
   值表本身在 `3rd API/*.csv`，由 `tools/icp_enum.py` 从接口文档生成。
   换版本接口文档就重跑脚本并逐行比对——取值集合在大版本之间会变。
5. **raw 只追加有变化的观测，不覆盖**（DD-82 · `raw_write` 段）。写入前按（幂等键, 内容哈希）
   去重，已存在于正常或已放行批次中的不写；被扣下与已丢弃批次中的行不参与去重。坏数据也
   照常写入 raw，并在 `meta.quarantine_batch` 登记隔离（DD-76）。core 按业务键只接受版本更大
   且内容不同的观测，版本取源端变更时间，源端没有就取观测时间（`core_version` 段）。
   相应地，每张 raw 表都要带 `run_id`、`batch_seq`、`content_hash` 三列，并在（幂等键,
   `content_hash`）上建**普通索引、不建唯一约束**——唯一约束不认批次状态，好数据第一次落在
   被隔离的批次里，之后的观测就再也写不进来（DD-82 被拒绝的方案三）。

## 文件

| 文件 | source_id | 取什么 |
| --- | --- | --- |
| `if03a_icp_cdr.yaml` | `icp_cdr_voice` | ICP 话单（CSV 下载），话务的唯一明细来源 |
| `if03b_icp_agent_session.yaml` | `icp_agent_session` | 坐席签入签出区间（明细） |
| `if03c_icp_agent_day.yaml` | `icp_agent_day` | 坐席状态时长日累计（平台历史指标） |
| `if03d_icp_reference.yaml` | `icp_reference` | 技能队列、坐席、坐席组等参照数据，按配置版本号变更检测 |

实时通道（`rindex` / `real`）**不在本目录**：它不落 raw，由实时组件直接取用推送看板，
不参与封账也不作服务水平举证（LD-01 4.5 · DD-64）。没有落库就没有契约——
这一条是刻意的，免得日后有人把看板快照当成历史数据来用。
