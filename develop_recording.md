# 开发日志

> 每一步一节，**新的在上**。每节写：做了什么、结果与数据、没验到的、遗留与下一步。
>
> 本日志只记进展与判断，不抄清单——表、索引、字段以 `ddl/` 为准，设计以 LD-* 为准，
> 待登记的决策先进 `NOTES.md`。抄一份就多一份会和源头失同步的副本（CLAUDE.md 第 5 节）。

---

## 2026-10-13　第 1 批 · 收尾：落库实测、秘密处理与口令轮换、「只在纸面上钉死」三处

**目标**：40-schema 在开发库上实际执行；处理 Mac 侧移交的秘密问题并轮换口令；落实
DD-82 · DD-83。第 1 批至此完成。

### 落库（40-schema 首次在开发库执行，10-12 晚）

- 第一遍停在「ingest：断言登录与权限角色均生效」：实际 `ingest|true|true`，期望写的是
  `|t|t`。登录、继承、SET ROLE 三项其实都成立——`pg_has_role()` 的布尔值经 `||` 拼进文本后
  是 `true` 而非 psql 单独显示时的 `t`。修正期望值（9ac6b56）后重跑通过，第二遍 `changed=0`
- 此后第四、五节的全部 SQL 均按渲染后的样子在临时实例上逐条执行过，输出与断言期望逐一吻合
- 现状：结构变更 0000–0003 已执行并登记；`ingest`、`dbt` 两个登录用户与环境文件已建
  （`/etc/oss911/{ingest,dbt}.env`，10-12 21:31 / 21:34）；dbt profile 的 `role:` 已生效

### 秘密处理（Mac 侧移交第 1 至 3 项，7841bbb）

- 新增 `tasks/role_password.yml`，21 / 22 / 30 / 40 建库用户、改口令一律经它：口令只走标准输入；
  同一事务内 SET LOCAL 关掉 `log_min_error_statement`、`log_min_duration_statement`、
  `log_statement`。临时实例实测：日志记录全部语句时，成功与出错两种情况日志中均无口令；
  不加 SET LOCAL 的对照组两种情况口令均以原文入日志
- `pgb_probe` 改为每次独立随机口令（原为 pgbouncer 口令前 12 位派生，且在命令行上）
- 三个旧剧本补齐「删存放文件重跑即轮换」，并补两处重启缺口（环境文件变化时 Keycloak、
  Airflow 原不重启）。Keycloak 引导管理员只在首次安装时生成，此后不再写回
- 执行指南 `runbook/91-credential-rotation.md`

### 口令轮换（Gavin 执行，10-13 下午）

| 步 | 内容 | 结果 |
| --- | --- | --- |
| 0 | 四个剧本以新写法各跑一遍 | 21 / 40 无变化；30 改写 `profiles.yml`（第三步加的 `role:` 首次落地，预期内）；**22 改写了三个 Quadlet 单元**（见下「纸面钉死」一） |
| 1 | 历史 PG 日志中是否有口令 | 据 Gavin 报告完成 |
| 2 | Keycloak 控制台建正式管理员、删引导管理员、去掉环境文件四行 | 据 Gavin 报告完成；`keycloak.env` 现为 72 字节、只剩两行（开发机可见） |
| 3 | 轮换 pgbouncer | `userlist.txt` 16:24 重写；剧本在 restart handler 停了 90 秒（见下） |
| 4 | 轮换 keycloak 库口令 | `keycloak.env` 16:27 重写；健康检查 200 |
| 5 | 轮换 airflow 库口令 | `airflow.env` 16:28 重写；四个组件 active |
| 6 | `usr` 的 sudo 口令 | 据 Gavin 报告完成 |

开发机侧核对（10-13 收尾时）：pgbouncer、haproxy、keycloak、seaweedfs、prometheus 与 Airflow
四个组件共 9 个服务全部 active。

### 「只在纸面上钉死」三处（troubleshooting 第 28 条 · DD-83）

1. **镜像 digest**：10-07 把 digest 填进 inventory 与 MANIFEST 后没有重跑 22，容器一直按 tag 运行
   （journal：keycloak:26.0 共 763 次，含早期崩溃循环；另两个各 4 次），10-13 第 0 步才第一次
   按 digest 建。Mac 侧确认六天里 tag 与 digest 是同一镜像
2. **dbt lock**：仓库一份、wheelhouse 一份，安装读 wheelhouse 那份。按 DD-83 改为只读仓库那份，
   清掉 wheelhouse 副本；重新解析改由 `-e dbt_relock=true` 显式触发。落实前核对：仓库 lock 与 dbt
   venv 实装 59 个包逐行一致
3. **Airflow constraints 的 SHA-256**：剧本原先只打印、从不比对。改为 inventory 钉值、安装前断言。
   落实前核对：上游文件与 MANIFEST 一致

教训（第 28 条）：改了 inventory 必须重跑并看到 changed；改了值重跑却 `changed=0`，说明该值没被
用上。**钉死一个值，必须有一个环节在它不符时失败。**

### 其他发现与修正

- **一条永远通过的断言**：30-python「两个 venv 各自装对了」的 dbt 一半取的是 `dbt --version` 的
  第一行「Core:」，只查输出里有没有「dbt=」——echo 本身就打出这几个字。改为与 lock 逐包比对，
  本机验过一致 / 不符 / lock 缺失三种情况（cf065c2）
- **pgBouncer restart 等 90 秒**：发行版单元以 SIGINT 安全关闭，要等客户端全部断开，而会话池连接
  从不主动断开，只能等超时被 SIGKILL。改口令只需 reload（5d64494，troubleshooting 第 29 条）
- **Gathering Facts 有时卡几分钟**：以 root 收集要扫 LVM 与全部块设备（本机有虚拟光驱 sr0，疑似
  卡点，未能复现确认）。逐个核对后，10 / 20 / 21 / 22 / 30 / 40 六个剧本不用任何 fact，已关掉
  收集；只有 00-base 与 99-verify 保留（ce9c0c7）
- **runbook 03–07 的「跑」一节**仍写着 rsync 到已退役的 `~/infra` 再执行，照做即回到两份 infra。
  已统一改为从仓库执行
- **预期 changed=0 的那一遍一律加 `--diff`**：22 那次「改了但不知道改了什么」，旧内容已被覆盖
  无从查起。已核对写口令的任务均为 `no_log`，`--diff` 不会打出口令

### DD-82 的契约落实

if03a–d 删去「按幂等键覆盖写」「坏数据不进 raw」「允许回补重写」，各加 `raw_write` 与
`core_version` 两段；if03d 的版本改用 `config_version`（更大应用、相同不动、更小进待澄清并告警），
并新增 `integration_checks` 段登记「config_version 是否单调、重启后是否归零」。ingest 读
`meta.quarantine_batch` 的权限已由 0002 的默认权限生效，未建空变更，改由权限验证加一条检查守住。

### 没验到的

- 第 1、2、6 步开发机看不到，以 Gavin 报告为准
- Gathering Facts 卡顿的根因未确认（复现不出）；00-base / 99-verify 仍可能遇到，卡住时以
  `ps -ef --forest | grep -A5 "[A]nsiballZ_setup"` 看停在哪个子命令
- 传输加密（DD-68）在开发机上验不到（同机回环），须在跨虚机环境验证（NOTES 移交第 6 项）

### 遗留与下一步

- **第 1 批完成**。待 Mac 侧：CLAUDE.md 第 9 节进展表与第 5 节条数（troubleshooting 现为 29 条）
- 移交第 5 项（userlist 存明文，评估改存 SCRAM，须实测）未处理
- **第 2 批**：ICP 话单从头到尾打通一条链路（模拟接口 → 接入 → raw → staging → core → Airflow）。
  开发机连不上真实 ICP、手上无真实话单样本，须先登记「开发期以合成话单代替真实接口」的假设

---

## 2026-10-12　第 1 批 · 第三步：剧本 40-schema（待执行）· 按月分区 · 时间列索引

**目标**：写出把 0000 至 0003 落到开发库的剧本与执行指南；并在落库前吸收 Mac 侧
新登记的四条决策（DD-75 · DD-76 · DD-77 · DD-78）。

### 做了什么

1. **变更 0003**（第一个 `run_as=owner` 的变更）：`meta.quarantine_batch`（DD-76，键
   `(run_id, batch_seq)`，处理结论只能从 pending 写一次、列级授权、理由必填）与
   `audit.runtime_param_change_log`（DD-75）
2. **剧本** `infra/playbooks/40-schema.yml` + `tasks/db_login.yml`：按 `ddl/changes.yml` 执行
   结构变更（校验和变了即失败、只许末尾追加、逐个事务执行、第一个失败即停）；建 `ingest`、
   `dbt` 两个登录用户，口令在 `/etc/oss911/{ingest,dbt}.env`，以 airflow 身份经 pgBouncer
   登录验证；建初始分区；末尾跑权限验证
3. `dbt_profiles.yml.j2` 加 `role: oss911_transform`；`test/db_roles_check.sql` 改为事务内
   自建临时登录用户
4. **DD-77**：分区由按天改为按月，函数改名 `meta.ensure_monthly_partitions`
5. **DD-78**：时间列索引按写入模式逐表调整（BRIN 6 个，全部 autosummarize）
6. 执行指南 `infra/runbook/08-schema.md`

### 结果（均在临时实例上）

| 验证 | 结果 |
| --- | --- |
| 剧本副本（去掉 sudo、跳过要 pgBouncer 的第四、五节）连跑两遍 | 第一遍执行 0000–0003；第二遍 `changed=0` |
| 权限验证 | 65 项全部通过 |
| 反向：篡改已执行的 0003 | 剧本在校验和断言处停下，报出新旧两个值 |
| 校验和：剧本 vs 第二步的模拟执行器 | 完全一致 |
| 按月分区 | 跨年、2 月、月末月初的行各落其月；同窗口重复调用返回 0；新分区归 `oss911_owner` |
| 时间列索引（pg_indexes） | BRIN 6 个均带 autosummarize，月分区子索引同样继承；fact_intake、fact_dispatch_order 无 BRIN；库中显式索引 62 条，名称集合与 DDL 一致 |
| 取口令逻辑（单独抽出测试） | 有环境文件沿用、无则生成 32 位新口令，两支都对 |

### 过程中发现与修正

- **0003 的 CHECK 有 NULL 陷阱**：理由为 NULL 时 `length(btrim(NULL)) > 0` 得 NULL，CHECK
  视 NULL 为通过——「不允许无理由放行」恰在最需要时失效且不报错。由权限验证抓到，已显式
  加 `IS NOT NULL`
- **执行指南核验查询数错分区**：`relispartition` 对分区上的索引也为真，原写法会数出
  core 300 而非 50。改为只数 `relkind = 'r'`
- **设计措辞未经核对**：DD-77「月内按日定位依靠已有的 BRIN」与 DDL 不符（13 张里只有 4 张
  有 BRIN），由开发机报告后 Mac 侧登记 DD-78 更正。DD-78 的落实中又发现两处同类：
  fact_agent_day 要「新增」的索引已存在、fact_turnout 说「已有」的索引实际没有——见 NOTES.md
- **两侧分叉**：Mac 侧在开发机提交第三步的同时提交了 DD-77。文件不重叠，rebase 即可；但内容
  上 DD-77 推翻了刚写好的按天分区。核实 40-schema 从未运行（`/opt/oss911/ddl` 不存在）后，
  按 DD-77「落库前改，代价为零」直接改 0001 / 0002
- **更正第二步的记录**：第二步写的「权限验证 45 项」是目测估数，按当时脚本实数为 50 项
  （47 条 expect + 3 个 DO 块检查）

### 没验到的

- **剧本的第四、五节**（建登录用户、经 pgBouncer 登录、建分区）需要 sudo 与真实 pgBouncer，
  临时实例上测不了——首次执行若出问题，大概率在这里
- 开发库 `oss911` 仍未建任何对象：剧本由 Gavin 按 `08-schema.md` 执行

### 遗留与下一步

- **Gavin 执行 40-schema**（两遍，第二遍 `changed=0`），再重跑 30-python 使 dbt 的 `role:` 生效
- 待 Mac 侧：批次粒度 `(run_id, batch_seq)` 的登记；DD-78 两处措辞的更正
- 开发机侧旧账：inventory 中 pgcrypto 的注释有误；`site.yml` 只引入 00 / 10 / 20
- 第 2 批写 dbt 与服务层时守 NOTES.md「写代码时要守」第 4、5 条（带 `date_key`、只写变化的行）

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
