# 开发日志

> 每一步一节，**新的在上**。每节写：做了什么、结果与数据、没验到的、遗留与下一步。
>
> 本日志只记进展与判断，不抄清单——表、索引、字段以 `ddl/` 为准，设计以 LD-* 为准，
> 待登记的决策先进 `NOTES.md`。抄一份就多一份会和源头失同步的副本（CLAUDE.md 第 5 节）。

---

## 2026-10-11　第 1 批 · 第二步：结构变更机制与库角色

**目标**：让 DDL 可以重复执行、可以记录版本（取代 `IF NOT EXISTS`），并补上属主与授权。
做法与被拒方案见 NOTES.md 的两条「待登记」。

### 做了什么

1. **改名**：不叫「迁移」，改叫「结构变更」（DDL change）。本项目中「迁移」专指
   Hexagon 归档数据迁入，同名会在交接时混淆
2. 新增 `ddl/changes.yml`（清单与执行规则）、`ddl/changes/0000_ddl_change.sql`（登记表
   `meta.ddl_change`）、`ddl/changes/0002_roles_and_grants.sql`（五个权限角色、属主、
   授权、默认权限、分区函数改为 SECURITY DEFINER、结尾自检）。`ddl/01..05` 不改动，
   作为变更 0001
3. 新增 `test/db_roles_check.sql`：以四个登录用户的身份逐项验证，共 45 项；整体在一个
   事务中执行，结尾回滚
4. 在 scratchpad 写执行器的模拟脚本（不入库，正式版在第三步写进剧本），起临时实例验证

### 结果

| 验证 | 结果 |
| --- | --- |
| 执行器第一遍 | 0000 → 0001 → 0002 依次执行，0002 自检通过 |
| 执行器第二遍 | 三个变更全部跳过 |
| 权限验证 45 项 | 全部通过；回滚后无残留表、无残留行 |
| 反向：多授 ingest 对 raw 的 UPDATE | 验证脚本报 FAIL，退出码非零 |
| 反向：改动已执行的 0002 | 执行器拒绝执行，报出新旧校验和 |
| 同集群第二个库（角色已存在、属性正确） | 0002 正常执行 |
| 同名角色被改为可登录 | 0002 报错并整体回滚，未登记 |

### 过程中修正的判断

- 起初说「不配 `ALTER DEFAULT PRIVILEGES`，dbt 新建的表属主是 dbt 角色」——混淆了属主与
  授权。默认权限只管授权、不管属主。改为按层分属主：raw · core · meta · audit 归
  `oss911_owner`，staging · mart · ai 归 `oss911_transform`
- 角色由四类改为五类：管理界面经服务层写权限矩阵与参照配置（LD-06 2.1），服务层拆为
  查询（`query_svc`）与管理（`admin_svc`）两个角色
- 分区函数原样不可用：建分区要求调用者是父表属主，而编排调用时不是属主。改为
  SECURITY DEFINER，并收窄到 core 与 audit 的分区表

### 没验到的

- 仍在临时实例上验证，开发库 `oss911` 尚未建任何对象
- 执行器是模拟脚本；正式执行器（剧本）未写
- 登录用户能否经 pgBouncer 认证，不在本步范围（属 21-connection）

### 遗留与下一步

- **待登记（Mac 上进 HD-04）**：结构变更机制、五类角色——见 NOTES.md
- **设计缺口**：LD-01 7.2 的隔离区在 DDL 中尚无对应表
- **6 项写权限未授**（依据不明），代码写到时以新增变更补，清单见 NOTES.md
- **第三步**：剧本 `infra/playbooks/40-schema.yml`——按 `changes.yml` 执行结构变更，
  建四个登录用户（口令写入 `/etc/oss911/*.env`，模式 0600）并加入权限角色；
  `dbt_profiles.yml.j2` 加 `role: oss911_transform`；调用分区函数建初始窗口；
  跑两遍确认 `changed=0`；在真集群上跑 `test/db_roles_check.sql`

---

## 2026-10-08　第 1 批 · 第一步：DDL 试建

**目标**：在落库之前，先确认 `ddl/01..05`（1242 行，从未真正执行过）能原样跑通，
并据此决定迁移粒度。

### 做了什么

1. 读 CLAUDE.md、HD-00、LD-02 / LD-07 / LD-08 相关章节，在 HD-04 中检索 DDL 上线机制
   与库角色 —— **均无既有决策**。LD-08 只写「DDL 变更随制品发布，必须可前滚可回退」；
   1242 行 DDL 中 OWNER、GRANT、CREATE ROLE 一行都没有
2. 摸本机条件：sudo 需口令；库需凭据；dbt profile 引用的 `DBT_USER` 在库中尚不存在
3. 用本机 PG 16 二进制以 `usr` 身份 `initdb` 临时实例（locale=C、UTF8，仅 unix socket，
   位于 scratchpad），按 01→05 顺序灌入 DDL。**不碰开发集群、不用 sudo**
4. 反查库内对象、调用函数、做事务测试；完成后停库并删除

### 结果

| 项 | 结果 |
| --- | --- |
| 执行 | 五个文件零报错 |
| 表 | 50 张，与 DDL 中 CREATE TABLE 条数一致：core 24（维度 14 · 事实 10）· meta 20 · audit 6；其中分区表 13 |
| 显式索引 | 55 条，与 DDL 中 CREATE INDEX 条数一致 |
| 空 schema | raw / staging / mart / ai —— 符合设计，由接入与 dbt 建 |
| `meta.ensure_daily_partitions` | 13 张分区表各建 3 个分区；重跑返回 0，幂等 |
| `core.nk` | `'A\|B'` 与 `'A','B'` 得到不同的键；含 NULL 时报错 |
| 排序与扩展 | `name_ar` / `name_en` 列上 ICU 排序生效；PostGIS 3.4.2 |
| 事务性 | 五个文件可作为**单个事务**执行；中途故意失败时整体回滚，连 `CREATE EXTENSION` 一并撤销 |

**结论**：SQL 本身无需修改；初始结构可作为**一个**迁移整体执行，不必按文件拆。

### 没验到的

- 临时实例中执行者是超级用户，**只验了 SQL 本身**（语法、引用顺序、函数行为），
  没有验属主与授权。须在真集群里、以真角色验（CLAUDE.md 第 5 节第二条）
- 开发库 `oss911` 中**尚未建任何对象**

### 过程中修正的判断

- 起初把「DDL 不能重跑」诊断为缺 `IF NOT EXISTS`。经评审否定：`CREATE TABLE IF NOT EXISTS`
  在定义已变时静默跳过，第二遍 `changed=0` 而库里仍是旧结构。幂等性应来自
  「迁移是否已执行」，而非「对象是否存在」。详见 NOTES.md
- 角色起初划为三类，评审补为四类：接入与 dbt 必须拆开，让 raw 只追加（DD-02）由权限
  执行；服务层需对 audit 有 INSERT。详见 NOTES.md

### 遗留与下一步

- **待登记（Mac 上进 HD-04）**：DDL 迁移机制（`meta.schema_migration`）、四类库角色——
  均已写入 NOTES.md，含被拒方案与理由；以及 `meta.schema_version_registry` 的名字陷阱
- **第二步**：`ddl/01..05` 原样作为迁移 0001（LD-02 按文件名引用它们，不挪不改名）；
  新写 0002：`meta.schema_migration`、四类角色、属主与 `ALTER DEFAULT PRIVILEGES`、授权
- **第三步**：剧本 `infra/playbooks/40-schema.yml`，逐个执行未执行过的迁移、每个迁移一个
  事务，调用分区函数建初始窗口；跑两遍确认 `changed=0`，并在真集群以真角色验证授权
