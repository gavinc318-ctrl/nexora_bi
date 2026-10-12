# 开发机笔记

> 开发机上发现、需要登记进 HD-03 / HD-04 的事，先记在这里，由 Gavin 在 Mac 上
> 登记进原件（CLAUDE.md 第 4 节）。登记完的条目注明编号，不删。

---

## 2026-10-08  DDL 落库：试建结果

**做法**：用本机 PG 16 二进制以 `usr` 身份 `initdb` 一个临时实例（locale=C、
UTF8，只走 unix socket，用后删除），按 01→05 顺序灌入 `ddl/*.sql`。
不碰开发集群、不需要 sudo。

**限制**：临时实例里执行者是超级用户，所以本次**只验了 SQL 本身**（语法、
引用顺序、函数行为），没有验属主与授权。属主与授权须在真集群里、以真角色验
（CLAUDE.md 第 5 节第二条）。

**结果**：

- 五个文件零报错。50 张表（core 24 · meta 20 · audit 6，其中分区表 13）、
  55 条 CREATE INDEX 全部建出，与 DDL 中的条数逐一对上
- `raw / staging / mart / ai` 四个 schema 是空的——符合预期，由接入与 dbt 建
- `meta.ensure_daily_partitions` 对 13 张分区表各建 3 个分区，重跑返回 0，幂等
- `core.nk` 转义正确（`'A|B'` 与 `'A','B'` 得到不同的键），拒绝 NULL
- `name_ar` / `name_en` 列上 ICU 排序规则生效；PostGIS 3.4.2
- **五个文件可作为单个事务执行**；中途失败时整体回滚，连 `CREATE EXTENSION`
  一起撤掉，不留半套结构。→ 初始结构可以做成**一个**结构变更（0001），不必按文件拆

---

## 已登记 → DD-73：DDL 的执行与版本登记机制

> 2026-10-11 登记进 HD-04；正文见 LD-08 7.5。

**用词**：叫「结构变更」（DDL change），**不叫「迁移」**。本项目中「迁移」专指
Hexagon 归档数据迁入（CN-15 · CN-16 · REQ-MIG-* · DD-37 · DD-57 至 DD-60）；
行业惯例的 migration 一词若沿用，`meta.schema_migration` 会被误读为归档迁移的
登记表，交接时搜「迁移」也会搜出两类毫不相干的东西。

**决定**：幂等性来自「这个变更是否已经执行过」，不来自「对象是否存在」。

- 清单 `ddl/changes.yml`：编号 → 文件列表。`ddl/01..05` 原样作为变更 0001
  （LD-02 按文件名引用它们，不挪不改名不复制）；之后的变更放 `ddl/changes/`
- 登记表 `meta.ddl_change`（变更 0000）：一个变更一行，记文件列表与拼接后的
  SHA-256。与 `meta.release_registry`（DD-49，一次发布一行）并列，一对多
- 每个变更的全部文件连同登记行在**同一个事务**中执行
- **已执行变更的文件校验和一旦变了，执行器必须失败**。结构只能新增变更来改，
  不得改已执行的文件——与登记册「不改写、只新增」同一条规矩
- 执行器用 Ansible + psql，不写 Python 工具：两样都已在生产链上；多一个工具
  就多一份要送进离网环境的依赖
- 0000 至 0002 以超级用户执行（0001 建扩展，0002 建角色）；0003 起事务开头
  `SET LOCAL ROLE oss911_owner`，新对象归属主

**被拒绝的方案与理由**：

- **给 CREATE TABLE / CREATE INDEX 加 `IF NOT EXISTS`**。表已存在但定义变了的
  时候，它会静默跳过：第二遍 `changed=0`，而库里还是旧结构，且不报错——正是
  本项目最警惕的「安静地错」。
  例外：`CREATE SCHEMA IF NOT EXISTS`、`CREATE EXTENSION IF NOT EXISTS`、
  `CREATE OR REPLACE FUNCTION` 可以保留。它们要么没有「定义」可言，要么真的
  会替换。`01_schemas_and_helpers.sql` 里全是这一类
- **把变更记录直接写进 `meta.release_registry`**。两者粒度不同：一次发布可以
  包含多个变更，也可以一个都没有（只改了 dbt 模型或指标）
- **借用 `meta.schema_version_registry`**。⚠ **名字陷阱**：这张表记的是归档
  **数据源**在时间轴上的多版本结构（RFP 2.7.7.9 · DD-37，`source_id + version_no`，
  例如「2016 年那套表结构和 2019 年不一样」），与我方自己的 DDL 版本是两件
  完全不同的事。因为名字像，混用了很可能几个月后才被发现

---

## 已登记 → DD-74：数据库角色划分

> 2026-10-11 登记进 HD-04；正文见 LD-02 第 13 章、LD-07 第 2 章。
> 表中 `meta.run_log`（管理路径）一项已另行决定：热调改写 `audit.runtime_param_change_log`（DD-75，LD-06 2.2 已更正）。
> 「隔离区」一项已定为登记而非搬移：`meta.quarantine_batch`（DD-76，LD-02 第 11 章、LD-01 7.2）。两张表待开发机以新的结构变更建立。

现有 DDL 里没有一行 OWNER、GRANT 或 CREATE ROLE，属主与授权全部新写于变更 0002。
**五个 NOLOGIN 权限角色**；可登录用户与口令按环境由剧本建立并加入相应角色——
0002 随发布制品进生产，不得含口令或环境相关的用户名。

| 角色 | 权限 | 挂在哪条纪律上 |
| --- | --- | --- |
| `oss911_owner` | 持有 raw · core · meta · audit 全部对象与七个 schema | — |
| `oss911_ingest` | 对 raw 只有 SELECT、INSERT；写 meta 水位、运行日志、值剖析、映射登记 | DD-02：raw 只追加——**由权限执行纪律，不靠人记得** |
| `oss911_transform`（dbt） | 对 raw 只读；对 core 增删改但不能 DROP；staging · mart · ai 由它建 | DD-02 · DD-03 |
| `oss911_query_svc` | 读 core · mart · ai · meta；对 audit 只 INSERT 访问与投递留痕 | DD-17：每次查询留痕；只增不改 |
| `oss911_admin_svc` | 继承 query_svc；另写权限与参照配置表、变更留痕，可查 audit（M-07） | LD-06 2.1：管理界面也走服务层 |

**被拒绝的方案**：

- **接入与 dbt 共用一个写入角色**——这样 DD-02 只靠约定维持
- **服务层只设一个角色（四类方案）**。管理界面经服务层写权限矩阵与参照配置
  （LD-06 2.1），若与查询路径同一角色，调用量最大、暴露面最大的查询路径一旦
  被打穿，攻击者即可改字段可见性矩阵
- **dbt 以 `SET ROLE oss911_owner` 运行，使全部对象归属主**。那样 dbt 拥有属主的
  全部权力，包括删改 raw，DD-02 又退回靠约定

**按层分属主**：raw · core · meta · audit 归 `oss911_owner`（由结构变更建）；
staging · mart · ai 归 `oss911_transform`（dbt 运行时建）。`ALTER DEFAULT PRIVILEGES`
管授权不管属主——它保证新对象自动授权给其他角色，但不改变属主是谁。

**由此产生的硬约束（写代码时要守）**：

1. **dbt 的 profile 必须设 `role: oss911_transform`**。否则新表属主是 dbt 的登录
   用户，`FOR ROLE oss911_transform` 的默认权限不生效，服务层读不到 mart。
   第三步改 `dbt_profiles.yml.j2`
2. **core 模型只能用增量写入既有表**。core 表由 DDL 定义且为分区表，dbt 不是
   属主、不能 DROP——`--full-refresh` 会失败，这是期望的行为
3. **raw 表必须由结构变更建，接入程序不得自建**。属主天然拥有 DELETE 与
   TRUNCATE；接入若在运行时自建 raw 表即成属主，只追加随之失效。
   每份契约的 `target_raw` 都对应一个结构变更（ICP 话单将是 0003）
   （后注：0003 给了隔离登记表与热调留痕表，ICP 话单的 raw 表顺延为 0004）
4. **按时间范围访问分区表的查询，必须同时带 `date_key` 条件**（DD-78 · LD-02 12.2 规则一）。
   分区按 `date_key` 切分，只按 `created_at` 一类时间列过滤，规划器裁不掉任何分区。
   服务层与 dbt 都要把时间范围同时翻译成 `date_key` 范围（裁分区）与时间列范围（走分区内
   索引），**两者的归日一律用 LD-02 第 6 章那一套时区规则**——零点附近两边一旦不一致，
   结果是静默漏行，不报错。第 2 批写 dbt 与服务层时用
5. **增量模型先比内容哈希，只写真正变化的行**（DD-78 · LD-02 12.2 规则二）。UPDATE 一行
   完全相同的数据照样生成新的行版本：话单每日回扫若整天重写，BRIN 被打散、表膨胀、
   备库复制量翻倍。第 2 批写 dbt 增量模型时用

**分区函数**：`CREATE TABLE ... PARTITION OF` 要求调用者是父表属主，编排每日
调用 `meta.ensure_daily_partitions` 时不是属主，不改必然失败。0002 将其改为
`SECURITY DEFINER`（属主身份执行，`search_path` 钉死），收窄到 core 与 audit 的
分区表，EXECUTE 只授 `oss911_transform`；core 与 meta 的函数一律收回 PUBLIC 的
EXECUTE。

**写权限只授有依据的**。依据不明、本次**未授**的，接入或服务层代码写到时报错，
届时以新增变更补，并在提交说明里写依据：

| 表 | 未授原因 |
| --- | --- |
| `meta.clarification_queue` | LD-01 7.2 未写明由接入还是 dbt 写入 |
| `meta.reference_value`（新码入表） | LD-01 9.2「新出现的码默认入表」未写明在接入还是 dbt 环节 |
| `meta.field_availability` · `metric_availability` | 迁移期剖面作业，执行身份未定 |
| `meta.run_log`（管理路径） | LD-06 2.2「热调写 run_log 的关联记录」，但 run_log 的 `source_id`、`outcome` 为必填，结构上对不上热调记录 |
| `audit.sla_probe` · `sla_daily` | 探测由谁执行未定（IR-21） |
| 隔离区 | LD-01 7.2 的隔离区在 DDL 中尚无对应表，M-03 的查看权限随它一并设计 |

**附注**：新角色**不需要**改 pgbouncer 的 `userlist.txt`。那里只放 pgbouncer
自己，其余用户经 `auth_query` 现查（第 5 步换来的）。不写明的话，下一个人会以为
每加一个库用户都要改 pgbouncer，然后把口令抄进那个文件。

---

## 分区：不需要新机制

`meta.ensure_daily_partitions` 已存在且幂等（`to_regclass` 判空再建）。
剧本 `40-schema.yml` 只需调它建一个初始窗口；「谁定期调它」是编排层的事，
已写在该函数的 COMMENT 里（分区缺失使当日写入失败，属必须监控项）。

---

## 待登记：「批次」的粒度 = 一次运行中的一个取数窗口（建议补入 DD-76 或新增一条）

> 已登记（2026-10-12）：LD-02 第 9 章写入「raw 表须带 run_id 与 batch_seq」（ff8d02b）。

DD-76 与 LD-01 7.2 把「批次标识」与「运行标识」并列，LD-01 3.3 又写「任一批次可以
按 run_id 重跑」——两处对批次与运行的关系说法不一，而 `meta.quarantine_batch` 的
主键取决于它。

**决定**（变更 0003 已按此建表）：批次 = 一次运行中的一个取数窗口，键为
`(run_id, batch_seq)`。一次补数运行可含多个窗口（ICP 窗口 ≤24h），其中一个不合格
只隔离那一个，不连累整次运行。

**由此产生的约束**：raw 表须带 `run_id` 与 `batch_seq` 两列，staging 才能按批次
过滤隔离状态。ICP 话单的 raw 表（下一个结构变更）照此建。

**被拒绝的方案**：批次 = 运行（键只用 `run_id`）。补数运行一拉就是多天，一个窗口
校验失败就要整次隔离，好的窗口也被扣下；且与契约中「每批回看一段」的窗口语义对不上。

**依据缺口**：LD-01 3.3「任一批次可以按 run_id 重跑」的表述需相应改为「任一运行」。
> 已改（2026-10-12，Mac 侧）。

---

## 开发机侧的两处旧账（infra，开发机自己处理，此处只备忘）

- `inventory/dev.yml` 的 `pg_extensions` 注释写「pgcrypto：core.sk 的 UUID v5 需要
  digest()」，但 `core.sk` 实际用的是 `uuid-ossp` 的 `uuid_generate_v5`（由 0001 自建），
  全仓库没有任何地方用 pgcrypto。pgcrypto 去留待定
- `playbooks/site.yml` 只引入了 00 / 10 / 20，没有 21、22、30、40——「离网重建时跑
  这一个」目前跑不出完整环境

---

## DD-77 的落实（2026-10-12，开发机）

按 DD-77 把分区粒度改为按月：`ddl/05` 与变更 0002 中的分区函数改名为
`meta.ensure_monthly_partitions`，分区名 `<表>_pYYYYMM`、边界 [当月 1 日, 次月 1 日)；
剧本初始窗口改为上月至后 3 个月（13 × 5 = 65 个分区）。

**直接改 0001 / 0002 而不新增变更的依据**：两者均未在任何真实环境执行过——
开发机上 40-schema 从未运行（`/opt/oss911/ddl` 与 `/etc/oss911/{ingest,dbt}.env`
都不存在），只在临时实例中跑过。这正是 DD-77「在 40-schema 落库前修改代价为零」
的前提。**40-schema 一旦在开发库执行，此后再改分区只能以新的结构变更进行。**

临时实例验证：跨年（12 月 → 1 月）、2 月边界、月末与月初的行各落其月分区；
同一窗口重复调用返回 0；新分区属主为 `oss911_owner`；65 项权限验证通过。

本文件前面各节提到的 `meta.ensure_daily_partitions` 均为当时的名称，保留不改。

## 待 Mac 侧判断：DD-77「月内按日定位依靠已有的 BRIN 索引」与 DDL 不符

> 已由 DD-78 处理（2026-10-12）：时间列索引按写入模式选 BRIN 或 B-tree，见 LD-02 12.1。开发机落实见本文件末节。

13 张分区表中只有 4 张有 BRIN：`fact_call_leg (wait_begin)`、`fact_call (offered_at)`、
`fact_intake (created_at)`、`fact_agent_session (signin_at)`。其余 9 张没有：
`bridge_call_intake`、`fact_dispatch_order`、`fact_turnout`、`fact_feedback`、
`fact_agent_day`、`fact_ai_inference`、`audit.access_log`、`audit.delivery_log`、`audit.sla_probe`。

另外**没有任何索引建在 `date_key` 本身上**。按天分区时「按某天查」由分区裁剪直接
定位；按月后同一查询要扫整月分区——结果不变、不报错，只是变慢，这类退化不会自己
冒出来。

需要决定的是：这 9 张是否补 BRIN、补在 `date_key` 还是各自的业务时间列。开发机
未擅自加；若决定补，在 40-schema 落库前可直接并入 05，落库后则为一个新的结构变更。

---

## DD-78 的落实（2026-10-12，开发机）

按 LD-02 12.1 逐表改 `ddl/05`（0001 仍未进入真实环境，直接改不违反 DD-73）：
新增 BRIN `ix_audit_occurred` · `ix_delivery_handed` · `ix_probe_probed` · `ix_fb_reported`；
`ix_intake_created` 由 BRIN 改为 B-tree；新增 B-tree `ix_disp_issued` · `ix_turnout_assigned`；
删去 `ix_agentsess_t`；全部 BRIN 加 `autosummarize = on`。05 的 CREATE INDEX 由 49 条变为 54 条，
全部 DDL 合计 60 条（加 0003 的 2 条，库中显式索引 62 条）。

临时实例上 pg_indexes 核对：BRIN 6 个且均带 autosummarize（月分区上的子索引同样继承）；
fact_intake、fact_dispatch_order 上无 BRIN；库中显式索引名集合与 DDL 完全一致。

**与 Mac 侧指令不一致的两处（请据此更正 DD-78 或 LD-02 12.1 的措辞）**：

1. **fact_agent_day「新增 (agent_sk, date_key)」未加**：05 中本已有完全同列的
   `ux_agentday` 唯一索引，再建一条是纯重复，只增写入成本
2. **fact_turnout「B-tree (assigned_at)，已有」实际没有**：现有含 assigned_at 的索引
   只有两种——复合索引的第二列（如 `(agency_type_sk, assigned_at)`），以及只覆盖未结案
   单据的部分索引 `ix_turnout_open`，都撑不起不带行过滤的时间范围访问。开发机按 LD-02 12.1
   的意图补了 `ix_turnout_assigned`。若 Mac 侧另有考虑，在 40-schema 落库前删掉即可

---

## 2026-10-12  Mac 侧移交：尚未处理的秘密问题与其他遗留

> 由 Mac 侧在审 40-schema 及回顾此前各步时汇总。**本节不写任何口令值。**
> 开发机处理完一项，就在该项后注明提交号，不删。

### 一、秘密（按优先级）

1. **pgBouncer 的探测用户口令由另一口令派生**（`21-connection.yml`「建立 auth_query 的
   验证用户」）。口令写成 `probe_` 加 pgBouncer 口令的前 12 位，且经 `psql -tAc` 写在
   命令行上——执行期间对本机任何用户的 `ps` 可见，等于暴露了 pgBouncer 口令的大半截。
   **处置**：探测用户改用独立随机口令（`lookup('ansible.builtin.password', '/dev/null', …)`），
   经标准输入传入，并套上下面第 2 条的 `SET LOCAL`；**并轮换 pgBouncer 口令**——
   此前的运行已经暴露过它的前 12 位
2. **21 / 22 / 30 三个剧本建角色时，口令写在命令行上**（`psql -tAc "CREATE ROLE … PASSWORD '…'"`），
   涉及 pgbouncer、keycloak、airflow 三个库用户。**处置**：改为 40-schema 的写法——
   口令走标准输入，同一事务内先 `SET LOCAL log_min_error_statement = panic` 与
   `SET LOCAL log_min_duration_statement = -1`（否则出错或慢的语句原文连同口令进服务端日志）。
   改完后三个库用户的口令各轮换一次：剧本沿用环境文件中的旧口令，删掉对应环境文件
   重跑即生成新口令（40-schema 的 db_login 已支持这一流程，三个旧剧本需照此补齐）
3. **Keycloak 引导管理员仍在用**（`22-containers.yml`，`KC_BOOTSTRAP_ADMIN_*`）。
   Keycloak 26 的引导管理员设计上是**临时的**。**处置**：在管理控制台建一个正式管理员，
   删除引导管理员，再从环境文件与 Quadlet 中去掉 `KC_BOOTSTRAP_ADMIN_*` 两项。
   另：该口令曾以明文出现在 Mac 侧的对话记录中——**无论上述做没做，都要换**
4. **`usr` 的 sudo 口令曾以明文出现在 Mac 侧的对话记录中**。由 Gavin 自行更换（`passwd`），
   不经剧本
5. **pgBouncer 的 `userlist.txt` 存的是明文口令**（0600，属主 postgres）。评估能否改存
   SCRAM 校验值（取自 `pg_authid.rolpassword`）。注意：auth_user 自己连库时 pgBouncer
   可能仍需明文，**须实测**，不要按文档推断后直接改

### 二、其他遗留

6. **传输加密（DD-68 · LD-07 第 7 章）在开发机上验不到**：库、pgBouncer、HAProxy 同机，
   连接走回环、不出网卡。`sslmode = verify-full` 与 CRL/OCSP 内网可达，须在跨虚机的
   测试环境验证，不能以开发机通过为准
7. **身份未接**：Keycloak 对 AD FS 的身份代理（DD-72）待 IR-49 答复；Airflow 的认证
   尚未接 Keycloak（runbook/07 已注明刻意留空，API 只绑回环）
8. **服务层的两个库登录用户**（query_svc、admin_svc）待服务层写出后再建，按 db_logins 加
9. **ICP 四份契约与将来的 raw 表须带 `run_id` 与 `batch_seq`**（LD-02 第 9 章 · DD-76）
10. **离网介质**：`90-bundle.yml` 尚未写；`22-containers.yml` 仍直接从仓库拉镜像，
    介质路径（skopeo + OCI 目录，DD-71）还没有进剧本。等 IR-47 / IR-48 答复后做

### 三、本文件的写法（两边共用）

两边都可以往本文件**追加新节**，各自在节标题里写明日期与来源；**不改对方写的节**，
只在其中注明处理结果与提交号。动笔前先 `git pull --rebase`，写完马上提交推送。
