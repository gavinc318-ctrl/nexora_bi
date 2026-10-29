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
   > **开发机已处理（7841bbb）**：`pgb_probe` 改为每次独立随机口令，经共用的
   > `tasks/role_password.yml`（标准输入 + SET LOCAL）建立，验完即删。pgbouncer 口令的
   > 轮换步骤见 `runbook/91-credential-rotation.md` 第 3 步，**待 Gavin 执行**
   > **已执行（2026-10-13，Gavin）**：见 develop_recording.md「第 1 批 · 收尾」
2. **21 / 22 / 30 三个剧本建角色时，口令写在命令行上**（`psql -tAc "CREATE ROLE … PASSWORD '…'"`），
   涉及 pgbouncer、keycloak、airflow 三个库用户。**处置**：改为 40-schema 的写法——
   口令走标准输入，同一事务内先 `SET LOCAL log_min_error_statement = panic` 与
   `SET LOCAL log_min_duration_statement = -1`（否则出错或慢的语句原文连同口令进服务端日志）。
   改完后三个库用户的口令各轮换一次：剧本沿用环境文件中的旧口令，删掉对应环境文件
   重跑即生成新口令（40-schema 的 db_login 已支持这一流程，三个旧剧本需照此补齐）
   > **开发机已处理（7841bbb）**：四个剧本（含 40-schema 自己——它原先只走了标准输入，
   > 没加 SET LOCAL）建用户、改口令一律经 `tasks/role_password.yml`。除指令中的两项外
   > 另加了 `SET LOCAL log_statement = 'none'`，防将来有人打开 DDL 日志。三个旧剧本补齐
   > 「删存放文件重跑即轮换」，并补了两处重启缺口（环境文件变了 Keycloak、Airflow 不重启）。
   > 临时实例实测：日志记录全部语句时，成功与出错两种情况日志中均无口令；不加 SET LOCAL
   > 的对照组两种情况口令均入日志。三个库用户的轮换**待 Gavin 按 91 第 3 至 5 步执行**
   > **已执行（2026-10-13，Gavin）**：见 develop_recording.md「第 1 批 · 收尾」
3. **Keycloak 引导管理员仍在用**（`22-containers.yml`，`KC_BOOTSTRAP_ADMIN_*`）。
   Keycloak 26 的引导管理员设计上是**临时的**。**处置**：在管理控制台建一个正式管理员，
   删除引导管理员，再从环境文件与 Quadlet 中去掉 `KC_BOOTSTRAP_ADMIN_*` 两项。
   另：该口令曾以明文出现在 Mac 侧的对话记录中——**无论上述做没做，都要换**
   > **开发机已写成步骤（7841bbb）**：`runbook/91-credential-rotation.md` 第三节（控制台
   > 操作 + 从环境文件去掉四行）。22 已改为引导管理员只在首次安装时生成、此后不再写回。
   > 须在轮换 keycloak 库口令之前做——删 keycloak.env 会连引导管理员口令一起丢掉。
   > **待 Gavin 执行**
   > **已执行（2026-10-13，Gavin）**：见 develop_recording.md「第 1 批 · 收尾」
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

---

## 2026-10-13  开发机：执行轮换第 0 步时发现的三件事

### 一、镜像 digest「钉死」从 10-07 到 10-13 只在纸面上成立

第 0 步跑 22 时三个 Quadlet 单元被改写、三个组件重启。podman 的 journal 显示，10-07 以来
容器一直按 **tag** 建（keycloak:26.0 共 763 次——早期崩溃循环反复重建；seaweedfs、
prometheus 各 4 次），按 digest 建的只有 10-13 这一次。

原因：`fad78c5`（10-07 18:46「三个镜像 digest 钉死并登记」）把 digest 填进了 inventory 与
MANIFEST，但**此后没有重跑 22**，磁盘上的单元仍是 tag 写法。MANIFEST 登记的 digest 与
实际运行的镜像在这六天里没有对应关系。现已一致，22 第二遍 `changed=0`。

**这与 CLAUDE.md 第 5 节第四条同类，但方向相反**：那条是「换了做法，旧产物不会自己消失」；
这条是「改了配置的记录，不等于改了运行中的东西」。建议补进 troubleshooting：改 inventory
之后必须重跑用到它的剧本，且那一遍应当看到 changed——看不到，说明改的不是它用的那份。

tag 与 digest 是否同一镜像（应是：digest 当初就从该 tag 解析而来），待 Gavin 用
`podman image inspect` 比对两者的 Id 确认。

> 已确认（2026-10-13，Gavin 在开发机上比对）：三个镜像各只有一个本地镜像，按 tag 与按 digest
> 指向同一 Id；三个运行中容器的镜像 Id 与之一致；digest 与 MANIFEST 逐字相同。
> 即这六天里实际运行的就是登记的那个镜像——缺口在流程上（改了 inventory 没重跑），
> 没有造成实际偏差。

### 二、30-python「断言两个 venv 各自装对了」的 dbt 一半永远通过（已修）

原写法取 `dbt --version | head -1`（那一行是「Core:」），断言只查输出中有没有「dbt=」——
`echo` 本身就会打出这几个字。dbt 装错版本甚至没装都照样通过。已改为以 lock 为准逐包
比对 dbt-core 与 dbt-postgres 的实际安装版本，并在本机验过三种情况（一致 / 版本不符 /
lock 缺失）。提交见下。

### 三、待 Mac 侧判断：dbt 的 lock 有两份，没有任何东西保证一致

`infra/dbt-requirements.lock`（仓库，进 MANIFEST，DD-69「冻结入库」）与
`/var/cache/build/wheelhouse/dbt-requirements.lock`（30-python 用 `pip freeze` 生成，
**安装实际按它**）。仓库那份靠人手复制提交（runbook/07），剧本从不读它。两份一旦不同，
评审看到的与机器上装的就是两回事，且不报错。

可选做法：一、剧本以仓库那份为权威，安装前复制进 wheelhouse；二、剧本断言两份一致，
不一致即失败。本机未擅自定。

> 已由 DD-83 处理（仓库那份为唯一权威，安装直接读它）；开发机落实见下一节。

---

## 2026-10-13  开发机：按 Mac 侧三条指令处理（troubleshooting · DD-83 · DD-82）

**一、troubleshooting 第 28 条**：「inventory 里钉了镜像 digest，实际运行的容器却不是按它拉的」，
含两句教训与「只在纸面上钉死」的归类。**CLAUDE.md 第 5 节写的「27 条」现为 28 条**（CLAUDE.md 由
Mac 侧改）。

**二、30-python（DD-83）**：
- `dbt_lock` 指向仓库的 `infra/dbt-requirements.lock`；wheelhouse 里的旧 lock 由剧本清掉
- 重新解析只在 `-e dbt_relock=true` 时进行，以调用者身份（不以 root）写回仓库路径，不自动提交
- inventory 加 `airflow_constraints_sha256`，安装前断言，不符即失败
- **落实前核对过两件事**：上游 constraints 文件的 SHA-256 与 MANIFEST 一致；仓库 lock 与 dbt venv
  实装的 59 个包逐行一致——故切换后不会重装任何包，第一遍的 changed 只来自清掉旧 lock
- 顺带：runbook 03 至 07 的「跑」一节还写着 rsync 到 `~/infra` 再执行（已退役的副本，CLAUDE.md
  第 1 节），照做就回到两份 infra。已统一改为从 `~/src/nexora_bi/infra` 执行

**三、ICP 契约（DD-82）**：
- if03a 至 d 删去「按幂等键覆盖写」「时序倒置进隔离区，不进 raw」「必须允许回补重写」，各加
  `raw_write` 与 `core_version` 两段；contracts/README.md 纪律补第 5 条（含 raw 表三列与普通索引）
- `contract_version` 未递增：四份契约尚未导入任何环境（`meta.source_contract` 为空），与 0001
  落库前可直接改同理
- **未建新的结构变更**：`GRANT SELECT ON meta.quarantine_batch TO oss911_ingest` 已经成立——0002 对
  meta 的默认权限在 0003 以属主建表时生效，ACL 中为 `oss911_ingest=ar`（临时实例实查）。再建
  一个变更只是空操作；改为在 `test/db_roles_check.sql` 加一条检查守住它（以 ingest 身份读待处理
  与已丢弃批次），以后谁把它改丢了验证即失败
- raw 表的三列与索引随 ICP 话单的 raw 表一起建（第 2 批，结构变更 0004 起）

**待 Mac 侧判断**：
- **if03d 的 core 版本**：四份契约的源端都没有行级变更时间，按 DD-82 一律取观测时间。但 if03d 有
  源端给的数据集级 `config_version`——它是源端版本、不是时间。是否改用它作 core 的版本，契约中
  已注明待定
  > 已处理（727f8d1）：按 Mac 侧答复（6ebad84）改用 config_version，三种情况的规则写入契约 `core_version.rules`；
  > 新增 `integration_checks` 段，登记「是否单调递增、重启或升级后是否归零」为联调核实项
- **DD-81 的数据发布序号表**写明「由开发机以结构变更建立」，本次指令未提及，开发机未建。
  需要时告知，它会是一个 run_as=owner 的新变更
  > 按 Mac 侧答复（6ebad84）暂不建：随 mart 发布那一步一起建，结构见 LD-02 第 11 章。开发机本次无改动

---

## 2026-10-13  开发机：第 1 批完成，请 Mac 侧更新 CLAUDE.md

- 第 9 节「当前进展」：`ddl/*.sql` 已落到开发库（结构变更 0000–0003，40-schema）；库角色、
  登录用户、口令轮换完成。「尚未开始：业务代码」一句可改为第 2 批起步
- 第 5 节：troubleshooting 现为 **29 条**（第 28 条「只在纸面上钉死」、第 29 条 pgBouncer restart 等 90 秒）
- 第 9 节步骤表可补第 8 步「数据库结构（40-schema）」与口令轮换（runbook/91）

---

## 2026-10-13  开发机：假设登记草稿「开发期以合成话单代替真实接口」（请 Mac 侧登记进 HD-03，建议编号 AS-32）

> 依据核对：接口文档 `3rd API/CloudICP V100R023C00 Interface Reference.pdf` 第 903 页（CMS 接口使用须知）、
> 第 905 页（鉴权）、第 1019–1020 页（话单下载）、第 1028 页（错误码表）；契约 `contracts/if03a_icp_cdr.yaml`；
> LD-01 4.2–4.4、2.5。下文页码均指该 PDF。

### 假设本身

| 项 | 内容 |
| --- | --- |
| 假设 | 开发与测试期间，ICP 的鉴权与话单下载接口由本方编写的**假 ICP 接口服务**替代。它按接口文档实现与真实接口相同的请求与响应，并生成合成话单。接入程序按正常路径调用它，与调用真实接口的唯一差别是部署时注入的地址与账号（契约中的 `host_ref`、`credential_ref`） |
| 依据 | 开发机与 MOI 专网不通；手上没有任何真实话单样本（`3rd API/` 只有接口文档与三张枚举值表） |
| 推翻后果 | 假接口中凡「照文档猜」的行为（下文第三节），只要与真实平台不同，联调时都会暴露，接入程序须相应修改。最危险的几条（时区、窗口边界、静默截断）**不报错、只是少数据或错位**，联调时必须逐条主动核实，不能以「跑通了」为准 |
| 状态 | 待确认——逐条随联调核实，核实结论回写到契约的 `integration_checks` 段 |

### 一、假接口服务的约定

1. **两个端点，照文档**：
   - `POST /cmsgateway/resource/userauth/auth`：JSON 请求 `{agentId, password}`，JSON 响应 `{message, retcode, result: {token}}`（第 905 页）
   - `POST /cmsgateway/resource/reports/vdnvoicecalldetail`：JSON 请求 `{ccId, vdnId, startTime, endTime}`，请求头带
     `cmsgateway-token`；成功时返回 CSV 附件——`Content-Disposition: attachment; filename=…`、
     `Content-Type: application/csv;charset=UTF-8`，且 retcode 放在**响应头** `result` 中，格式为
     `{message=get report scuccess, retcode=0}`（Java Map 的文本形式，不是 JSON；第 1019 页原样如此）
2. **约束照文档实现，违反即报错**：窗口 > 24 小时或 < 5 秒拒绝；同一时刻只服务一个下载，第二个并发
   请求拒绝；token 不存在或过期拒绝；非 POST 拒绝
3. **故障可注入**：token 提前过期、并发拒绝、失败响应、返回边界情况数据——用来让接入程序里鉴权重取、
   失败处理、隔离登记这些路径在开发期真的被执行到
4. **故意打乱返回顺序**：文档说按 CALLEND 降序，契约已写「排序不可用于增量判断」。假接口乱序返回，
   确保没有代码暗中依赖顺序
5. **合成数据由确定性种子生成**，同一种子同一窗口返回相同内容，便于复现与回放
6. **只用 Python 标准库**（`http.server`），不引入依赖。它是测试工具，不跟系统进生产网，故不进
   `artifacts/MANIFEST.md`（CLAUDE.md 第 1 节的判据）。放在 `test/fake_icp/`
7. **接入程序里没有任何「合成模式」分支**。开发机上注入的地址指向假接口——这是唯一差别

> **与 Mac 侧指令不一致一处：「分页」**。话单下载接口**没有分页**：请求参数只有 ccId、vdnId、startTime、
> endTime，一次返回一个完整的 CSV 附件（第 1019 页），全文找不到分页参数。假接口照此不分页。随之而来的
> 风险不是「分页写错」，而是「单次行数是否有上限、超出时是否静默截断」——见第三节第 7 条。

### 二、合成数据必须覆盖的边界情况

| # | 情况 | 出处 | 合成方式 | 接入程序应有的表现 |
| --- | --- | --- | --- | --- |
| 1 | 通话进行中，尚无 CALLIDNUM = -1 的腿 | 契约 batch_checks 第 1 条 · LD-01 4.4 | 窗口末尾进入的呼叫，只返回已产生的腿，末腿的 CALLEND 为空 | 不报错；后续回看取到变化后的腿时，作为新观测追加（DD-82） |
| 2 | 时序倒置（WAITEND < WAITBEGIN、CALLEND < CALLBEGIN） | 契约 batch_checks 第 4 条 · DD-82 第五条 | 指定批次中混入一行倒置 | 整批照常写入 raw，登记 `meta.quarantine_batch`，**退出码 0**（LD-01 2.5） |
| 3 | 回看窗口内重复取到 | 契约 `lookback_minutes: 15` · DD-82 | 相邻两次取数的回看区间重叠；其中未变化的腿内容逐字相同，变化的腿 CALLEND 已补上 | 未变化的不写；变化的追加一行 |
| 4 | 超过回看长度的长通话 | LD-01 4.4 | 通话时长 > 15 分钟，开始于本次回看区间之前、结束于其后 | 5 分钟增量永远取不到它的结束；只有每日回扫补齐。须能统计「至今没有 -1 腿的 CALLID」 |
| 5 | WAITBEGIN 为空 | 契约 batch_checks 第 3 条 | 指定批次中混入一行 WAITBEGIN 为空 | **见第四节第 1 条：契约与 DD-82 的说法冲突，须先定** |
| 6 | CALLTYPE 不在值表内 | 契约 batch_checks 第 5 条 · `3rd API/icp_call_type.csv` | 混入一个值表之外的取值 | 批次正常写入；该值进待澄清队列、计入 unknown 列数（**权限见第四节第 2 条**） |
| 7 | 同一 CALLID 内 CALLIDNUM 重复 | 契约 batch_checks 第 2 条 | 指定批次中制造重复序号 | 按契约的批次级校验处理 |
| 8 | 窗口边界恰在某条腿的 WAITBEGIN 上 | 第三节第 6 条 | 让若干腿的 WAITBEGIN 恰等于 startTime / endTime | 不漏、不重（重的由去重吸收） |
| 9 | 空结果 | 第三节第 9 条 | 无呼叫的时段 | 正常结束，`run_log` 记 0 行 |

### 三、照文档猜的行为（每一条都要成为联调核实项）

| # | 行为 | 文档怎么说 | 假接口怎么做 | 若猜错，后果 |
| --- | --- | --- | --- | --- |
| 1 | **请求参数 startTime / endTime 按什么时区解释** | 第 903 页：「接口返回的所有时间都是 UTC」；请求时间的时区未说 | 按 UTC 解释 | 若实为本地时间（+03），每个窗口错位 3 小时：水位与回看全部错位，**不报错**。最先核实 |
| 2 | 返回的时间字段按什么时区 | 第 903 页称 UTC，但字段本身不带时区标记 | 输出 UTC 时间、不带标记 | core 写 timestamptz 时整体偏 3 小时，所有按日归属的指标错日 |
| 3 | 并发下载时的表现 | 第 1019 页只说「不支持」，错误码表里没有对应的码 | 第二个请求返回 retcode 非 0（借用 000-002） | 真实平台也可能排队、超时，或返回不完整的数据却报成功——后者最坏。本方靠「并发度恒为 1」规避，不靠识别它的报错 |
| 4 | 窗口越界（> 24 小时或 < 5 秒）的表现 | 无专门错误码 | 返回 000-002（参数不合法） | 若真实平台静默截取前 24 小时，补数会漏 |
| 5 | 时间格式是否接受不补零 | 声明格式 `yyyy-MM-dd HH:mm:ss`，示例却是 `2018-1-1 00:00:00` | 只接受补零格式 | 无影响：接入程序一律发补零格式 |
| 6 | **窗口边界是闭区间还是半开区间** | 未写 | 按 [startTime, endTime) | 若两端闭合，相邻窗口边界上的腿取两次（去重吸收，无害）；**若两端都开，边界上的腿永远取不到**，且 WAITBEGIN 只到秒、同一秒内常有多条 |
| 7 | **单次返回行数是否有上限、超出是否静默截断** | 没有分页参数，也没写上限 | 不截断 | 若真实平台静默截断且 retcode 仍为 0，高峰窗口会漏行而不报错——只有对账（LD-01 8.3）能发现 |
| 8 | 失败时的响应形态 | 只写了成功时 retcode 在响应头 `result` 里 | 失败时 HTTP 200，`result` 头与 JSON 正文都带 `{message, retcode}`，不带 CSV | 若失败时 HTTP 状态非 200，或只在正文里给错误，接入程序的判定要改 |
| 9 | 空结果的形态 | 未写 | 只有表头行 | 若为空正文或不返回附件，「空」与「失败」可能被混淆 |
| 10 | CSV 细节：列数与列序、引号、空值写法、换行、BOM | 表头列了 18 列，但第 1019 页的示例行**只有 16 个值**；CALLERNO 可为邮件或网页地址（可能含逗号） | 固定 18 列、按文档列序；含逗号的字段加双引号（RFC 4180）；空值为空串；`\n` 换行；无 BOM | 列错位会把一列的值整列写进另一列，raw 原样落地，到 staging 才暴露 |
| 11 | token 的有效期、失效时的错误码、重复鉴权是否使旧 token 失效 | 均未写（即 IR-45） | 有效期可配置，开发期设短（如 10 分钟），以便常走重取路径；过期返回 300-008；重复鉴权不使旧 token 失效 | 若重复鉴权会踢掉旧会话，多个源共用一个服务工号时会互相挤掉 |
| 12 | **下载话单要什么账号** | 第 1019 页：「须系统管理员鉴权」（错误码 300-013）；第 903 页：「只有具质检权限的 VDN 用户才能鉴权成功」；而工号 1–100 是系统管理员、101–59999 是 VDN 用户——字面上两条互斥 | 任一合法账号都能下载 | 真实环境可能哪种账号都拿不到话单。联调前就该问清（并入 IR-45） |
| 13 | 调用频率限制 | 第 903 页「建议周期调用间隔 5 秒」 | 不限流 | 若真实平台限流，5 分钟一次的增量加上补数切片可能触发 |
| 14 | 返回顺序中 CALLEND 为空的行排在哪 | 文档写按「CALLEDN」降序（应是 CALLEND 的笔误） | 乱序（见第一节第 4 条） | 无影响：不依赖顺序 |
| 15 | **腿在什么时刻出现在接口里** | 未写。LD-01 4.4 写「话单在通话结束时才写入源端」，契约又写「窗口末尾进入的呼叫 CALLEND 可能为空，由回看补齐」——两种说法隐含的时刻不同 | 腿在进入设备时就出现，WAITEND、CALLEND 等随进展补上 | 决定「同一张话单会变几次」，也就决定 raw 的增长与第二节第 1、3 条是否真会出现 |
| 16 | CALLIDNUM 的编号规律 | 只说 1 为首、-1 为末 | 转接、会议时序号连续；-1 只出现一次且最后出现 | 若会出现两个 -1，或 -1 先于中间的腿出现，「通话是否结束」的判断要改 |
| 17 | 非末腿的 RELEASECAUSE 取什么值 | 「仅当 CALLIDNUM = -1 时有意义，其余情况无意义」 | 非末腿填 0 | 若为随机值，任何对它的统计都要先过滤末腿 |
| 18 | DEVICETYPE、CALLTYPE 等的实际分布 | 无 | 比例由本方编造 | **合成数据不得用于性能结论或指标口径结论**，只用于验证代码路径 |
| 19 | CALLID 的格式与唯一范围 | 示例 `1533893930-10`（像「秒级时间戳-序号」） | 照此格式生成 | 若跨天或平台重启后会重复，`(CALLID, CALLIDNUM)` 作为幂等键就不够，须加日期 |

### 四、开发机发现、须 Mac 侧先定的三处

1. **WAITBEGIN 为空的批次，契约与 DD-82 说法冲突**。契约 if03a batch_checks 写「为空即整批拒绝（LD-01 7.1）」；
   DD-82 第五条写「坏数据写入 raw 并登记隔离」。「拒绝」是不进 raw，还是进 raw 后登记隔离？建议按 DD-82
   统一为后者，契约这一句随之改。另：接口按 WAITBEGIN 过滤，WAITBEGIN 为空的行在真实平台上可能根本不会
   返回——若是，这一情况只能来自格式异常，同样按隔离处理
2. **CALLTYPE 不在值表内要写待澄清队列，但接入没有权限**。0002 时 `meta.clarification_queue` 因「由谁写入
   依据不明」未授（见本文件「数据库角色划分」一节的未授表）。现在契约写明由接入的批次校验产生，依据已明确。
   建议在 0004 中一并 `GRANT INSERT ON meta.clarification_queue TO oss911_ingest`——请确认
3. **第三节第 12 条（下载话单要什么账号）**建议并入 IR-45 一起问：两处文档字面互斥，联调前不问清，
   可能整条链路在第一步就走不通

> **Mac 侧处理结果（2026-10-13）**
> - 已登记为 **AS-32**（HD-03），状态「待确认」
> - 「分页」：接受更正，话单下载接口没有分页，是 Mac 侧指令写错了
> - 第四节第 1 条：**按 DD-82 统一为「写入 raw 并登记隔离」**。LD-01 7.1 标题由「整批拒绝」改为
>   「整批隔离」，正文补明隔离不等于不进 raw；契约中「整批拒绝（LD-01 7.1）」一句请随之改
> - 第四节第 2 条：**同意**，0004 中 `GRANT INSERT ON meta.clarification_queue TO oss911_ingest`；
>   LD-02 13.4 的未授清单已删去该表
> - 第四节第 3 条：**同意**，并入 IR-45。另把第三节第 1（请求时间的时区）、6（窗口边界）、
>   7（是否静默截断）、19（CALLID 唯一范围）四条一并写进 IR-45——这四条猜错都不报错
> - 第三节第 19 条另需一道防线，不等答复：staging 中同一 `(CALLID, CALLIDNUM)` 出现
>   WAITBEGIN 相差超过 24 小时的两次观测，即视为「键可能重复」，进待澄清队列，不当作同一张
>   话单的新版本写入 core。否则若真会重复，不同的通话会被静默合并成一张

---

## 2026-10-13  开发机：接入程序的数据库驱动受 LD-09 4.2 约束，请 Mac 侧定（建议登记 DD）

LD-09 4.2：「自研代码只 import 或链接宽松许可的库（MIT、BSD、Apache 2.0、ISC、PSF）」。接入程序是
自研代码，要连 PostgreSQL：

- **psycopg2 与 psycopg 3 都是 LGPL**，不在白名单内。dbt 用 psycopg2 不受影响——dbt 是第三方程序、
  以独立进程使用（LD-09 4.3）；但接入程序 `import psycopg2` 按 4.2 字面不允许
- **建议 pg8000**（BSD-3，纯 Python，不依赖 libpq；支持 SCRAM、可经 pgBouncer、咨询锁可用）。
  被拒方案：psycopg2（LGPL，违反 4.2）；asyncpg（Apache 2.0，可用，但接入是串行取数，异步接口只增加复杂度）。
  代价：接入与 dbt 用两个不同的驱动
- 其余依赖：HTTP 用标准库（PSF），YAML 用 PyYAML（MIT）
- **旁证一处不一致**：DD-52 接受 pymssql（LGPL）作迁移期驱动，写的理由是「符合 LD-09 4.2」，但 4.2 的
  白名单里没有 LGPL。归档迁移工具若是自研代码 import pymssql，与本条同一问题

开发机在此之前不冻结 venv-ingest 的 lock。

另：契约 if03a「整批拒绝」已按 Mac 侧处理结果改为「整批隔离」（本次提交）。

> **Mac 侧处理结果（2026-10-14）**
> - 判断正确：按 4.2 原文，`import psycopg` 不允许；DD-52 的旁证也成立——FreeTDS、pymssql 与此同类。
> - 但问题在规则不在驱动：4.2 把 LGPL 与 GPL 一并划到「只能独立进程」，严于法律实际。LGPL 专为让任何许可的
>   程序与之组合而设，边界是「用户能否替换该库」，import / 动态链接即满足；只有冻结进单文件可执行才越线。
>   维持原文则 DD-52 同样违规，而连 SQL Server 没有非 LGPL 的开源驱动链，规则本身不可执行。
> - **已登记 DD-86**：LD-09 4.2 增补 LGPL（三个条件：不修改、只 import / 动态链接且不冻结进单文件、清单标明并附许可文本）；
>   4.3 表加一行；DD-52 所选方案栏加注指向修订后的 4.2；CLAUDE.md §6 加第八条硬约束。
> - **接入程序驱动定为 psycopg 3**（不是 pg8000）。理由按 LD-09 第 2 节「上游健康度优先于许可」：pg8000 近乎一人
>   维护，八年合同期的健康度风险高于事实标准的 psycopg；为一条写严了的规则多引入一个组件不值。asyncpg 同样不选。
>   psycopg2 继续由 dbt 独立进程使用，两者并存于 wheelhouse。
> - 开发机可据此冻结 `venv-ingest` 的 lock。用 `psycopg[binary]`，并在组件清单登记 psycopg 3 为 LGPL-3。


---

## 2026-10-14  开发机：交付代码一律英文（Gavin 定），请 Mac 侧写进 CLAUDE.md

**规则**：最终交付的代码不含中文——注释、任务名、提示信息、标识符都用英文。文档（runbook、NOTES、
开发日志、LLD、CLAUDE.md）与面向业务口径的 YAML（`contracts/`、`metrics/`）保持中文。

**范围**（`tools/check_no_cjk.py` 按此检查，退出码非零即有违规）：`infra/playbooks`、`infra/files`、
`infra/inventory`、`infra/ansible.cfg`、`ddl`、`test`、`ingest`、`recon`。

**两类例外，都是显式的**：
1. **已执行并登记了校验和的结构变更文件**（`ddl/01..05`、`ddl/changes/0000 · 0002 · 0003`）保持原样——改其中的
   中文注释会改变校验和，40-schema 按 DD-73 必然失败。名单写在检查脚本里，此后每执行一个新变更就加进去；
   **新变更一律英文**
2. **功能性中文**：必须与中文原件逐字匹配的字面量（如测试脚本里匹配 0002 / 0003 中文异常信息的两条模式），
   集中放在一处，行内带 `cjk-ok` 标记并写明理由

**不在范围内**：`tools/` 下支撑开发的脚本（文档转换、check_refs、ICP 枚举抽取）——不交付、不在目标环境运行
（Gavin 2026-10-14 明确）。

**已做**：上述范围全部改为英文。核对方法——YAML 按结构逐节点对齐比对（原含中文的字符串可变，其余键与值
必须逐字相同）；模板与 SQL 逐行比对只有注释变化；ingest 重新离线安装并测退出码；权限验证脚本在临时实例上
66 项通过、两条中文模式确实命中。runbook 08 与 91 中引用的任务名与输出改为剧本现在的英文原文；
troubleshooting 中的历史记录保留原样。

**建议 CLAUDE.md 补一条**（第 3 节「每次改完必须做的事」）：`python3 tools/check_no_cjk.py`。

---

## 2026-10-14  开发机：第 2 批第三步（接入程序）的实现方案，请 Mac 侧确认

读 LD-01 第 2、4、5、7、10 章与 LD-02 第 9 章后整理。**标「需定」的四处是真冲突或设计空白，
开发机不擅自定**；标「拟」的是开发机的实现选择，Mac 侧无异议即按此做，有新选择的请登记 DD。

### 一、需定（四处）

1. **原始文件归档与 DD-15 冲突**。LD-01 4.2 写话单「按文件接入处理（5.1 的流程）」，5.1 要求归档原文件
   至对象存储；而 DD-15 写对象存储「仅由服务层访问」。另：访问方式未定——S3 协议须引入 boto3 一类较重
   依赖或自写 SigV4 签名；SeaweedFS filer 的 HTTP 接口只用标准库即可，但不是 S3，生产若换对象存储要改。
   **开发机先不做归档**，其余照做
2. **raw 表名与 LD-02 第 9 章不符**。规定为 `raw.<源标识>__<源表名>`，契约 if03a 写的是 `raw.icp_cdr_voice`。
   开发机按契约（契约是权威）建表；若以 LD-02 为准，须改契约
3. **契约同步进库的步骤不存在**。`meta.source_contract` 是「部署时同步的只读副本」（LD-01 2.1），但没有
   任何步骤写它；`meta.watermark_state` 有外键指向它，契约不进库，水位推进不了。另一层：M-01 可热调契约中的
   运行参数（LD-06 2.4），部署同步若整份覆盖 definition，会把热调值冲掉。须定：同步由谁做（建议 40-schema
   之后的一步，以属主身份）、热调参数放在 definition 内还是另列
4. **raw 表是否分区**。LD-02 第 9 章未规定。话单 raw 约每日十余万行（按内容去重后），八年数亿行，只追加。
   开发机拟**先不分区**，以 `(run_id, batch_seq)` 与 `(CALLID, CALLIDNUM, content_hash)` 两个普通索引起步；
   日后要分区须以新结构变更重建（raw 只追加，可拷贝重建）。请确认或改定

### 二、拟（开发机的实现选择）

| # | 选择 | 理由 |
| --- | --- | --- |
| 1 | **一次运行一个事务**：写 raw、登记隔离、推进水位、写运行日志一起提交；失败整体回滚，再以一个短事务写一行 `outcome = failed` | LD-01 10.1「运行日志只增不改」——开始插一行、结束回填是「改」；且失败不推进水位 |
| 2 | **批次 = 一个 ≤24h 的取数切片**。程序把 `[窗口起点 − 回看, 窗口终点]` 按 24h 切片、串行取，每片一个 `batch_seq` | AS 草稿与 DD-76 的「批次 = 一次运行中的一个取数窗口」；接口单次上限 24h |
| 3 | **内容哈希** = 按契约列序取 18 列原值，以 JSON 数组序列化后的 SHA-256。比的是源端给的原文，不比解析后的值 | DD-82；原文比对与解析规则解耦，解析改了不影响去重 |
| 4 | **去重**：取到的行先入临时表，再一条 `INSERT … SELECT … WHERE NOT EXISTS` 写 raw；`NOT EXISTS` 排除待处理与已丢弃批次中的行 | DD-82 第一、二条；一条语句完成，无竞争（咨询锁保证同源单实例） |
| 5 | **校验**照契约 if03a 的 batch_checks 写成代码：每 CALLID 至多一条 -1；CALLIDNUM 不重复；WAITBEGIN 非空；无时序倒置——任一不满足即整批隔离；CALLTYPE 不在值表不隔离，入待澄清队列；**表头与契约列不一致**亦整批隔离，新出现的列以 `unknown` 写 `meta.mapping_registry`（DD-07 · LD-01 2.3） | 契约里的校验是文字，无法机读执行 |
| 6 | **配置与凭据**：契约中 `host_ref: X` 对应环境变量 `OSS911_HOST_<X 大写>`，`credential_ref: Y` 对应 `OSS911_CRED_<Y 大写>_USER / _PASSWORD`。程序自读 `/etc/oss911/ingest.env`（库）与 `/etc/oss911/icp.env`（ICP 地址与服务工号）；数据库地址等非秘密项放 `/etc/oss911/ingest.conf`（0644）。均由剧本生成；**生产的 ICP 服务工号口令由运维手工写入 icp.env，剧本只断言其存在**，开发机由剧本生成随机口令并让假接口以同一账号启动 | LD-01 2.4 · DD-84；生产口令是 ICP 运维给的，剧本不能凭空生成 |
| 7 | **咨询锁**：会话级 `pg_try_advisory_lock`，键由 `source_id` 派生，经 pgBouncer 会话池（`oss911_etl`）；取不到即退出码 1 | DD-85 |
| 8 | **契约读取**：从 `meta.source_contract` 读（与部署版本一致），不直接读仓库 YAML | LD-01 2.1「库内为部署时同步的副本」；依赖上面「需定」第 3 条 |
| 9 | 结构变更 **0004**：建 `raw.icp_cdr_voice`（18 列原样 text + 原始行 jsonb + `run_id`、`batch_seq`、`contract_version`、`src_watermark`、`ingested_at`、`content_hash`），两个普通索引，不建唯一约束；并 `GRANT INSERT ON meta.clarification_queue TO oss911_ingest`（Mac 侧已同意） | DD-82 · DD-76 · LD-02 第 9 章 |

假接口在开发机上先**手工起**（测试与手工补数够用）；Airflow DAG 那一步再定它要不要以服务常驻——那会是
一个只用于开发机、不进 `site.yml` 的剧本，交付边界须另说。

> **Mac 侧处理结果（2026-10-18，Gavin 已定）**
> - 「交付代码一律英文」已写进 CLAUDE.md 第 3 节（第四件事）与规则说明。
> - **需定四处**：
>   1. 归档照做，访问**只经 S3 API，用 boto3**，不用 SeaweedFS filer 等任何私有接口——可切换是前提（DD-87）。
>      DD-15 的「仅由服务层访问」是指展现层与外部系统不得直连，已加注。先传对象、后提交事务，对象键
>      `<源标识>/<run_id>/<batch_seq>`。开发期可暂缓，上线前必须就位。
>   2. 按契约建表 `raw.icp_cdr_voice`。LD-02 第 9 章已补：源无表概念的，表名即 `raw.<源标识>`。
>   3. 契约同步 = 部署步骤（40-schema 之后加 45-contracts，属主身份），只写 definition / contract_version /
>      target_raw / connector，且只在文件版本号 > 库内时写。热调参数另建 `meta.source_runtime_param`，
>      `enabled` 属热调、同步不碰；admin_svc 对 source_contract 的 UPDATE 收窄为 `UPDATE (enabled)`（DD-88）。
>   4. **raw 按月分区**，键 `ingested_at`，索引按 DD-78：ingested_at BRIN；(run_id, batch_seq) 与
>      (CALLID, CALLIDNUM, content_hash) B-tree；不建唯一约束。理由同 DD-77：现在是一行配置，日后是以周计的重建。
> - **拟九条**：第 1 条改为**一批一个事务**（raw、隔离登记、ingest_batch、水位一起提交），run_log 结束时写一行，
>   outcome 取 success / partial / failed；第 9 条 0004 增加 `meta.ingest_batch`（run_id、batch_seq、source_id、
>   窗口起止、行数、原文件 sha256 与字节数、归档对象键、提交时间）、`meta.source_runtime_param`，以及 admin_svc
>   权限收窄；第 7 条补两点：pgBouncer 对 oss911_etl 必须 session 模式，持锁连接即整个运行使用的连接。其余六条照做。
> - 全部写进 LD-01 2.5 与 LD-02 第 9 章，登记 DD-87、DD-88。

> **Mac 侧处理结果（2026-10-18 第二轮，评审五条全部采纳，Gavin 已定）**
> - 隔离表外键改指 `meta.ingest_batch (run_id, batch_seq)`，0004 含此调整；`source_id` 从 ingest_batch 取。
> - 有效配置运行开始解析一次、整运行沿用，快照写 `ingest_batch.effective_params`（不含敏感项）；白名单写进
>   `source_runtime_param` 的 CHECK；启停只认 `source_contract.enabled`；**第一版轮询间隔为发布项、不可热调**（LD-06 2.4 已改）。
> - 契约同步三支（同版同内容不动 / 同版不同内容失败 / 低版本拒绝）；新版本前校验既有热调值，**不兼容则部署失败**，不自动作废。
> - staging 以放行时间为第二水位，staging 行记 `staged_at`；接入程序分 incremental / backfill 两模式，backfill 不动水位（DD-89）。
> - 哈希覆盖实际收到的完整字段名与原值。
> - 分区去重性能 → LD-10 TC-DISC-20 待验证。CALLID 唯一范围**维持待答复**（IR-45 第五项 / ICP 问题单 Q21），未确认过；
>   CALLIDNUM 稳定登记 AS-33，A→B→A 登记 AS-34，均为源端行为假设。

## 2026-10-19  Mac 侧：DS 对 CAD 问题单的答复已到，第二轮追问已发

- 答复件归档为 `queries/CAD_Data_View_Clarification_Questions_Answered_2026-10-15.xlsx`；`3rd API/Bill同步CAD表数据结构.xlsx`
  替换为新版（内容相同，多一页 Table Lists：74 张表的英文视图名，**契约一律按英文视图名与翻译列名写**）。
- 评估已写进 HD-03 IR-50。要点：统计以 `t_AgencyEvent` 为准；结案时间只在 `t_EventClosure`、解除结案以最后一次为准；
  全部本地时间；不物理删除；14 张 `t_dm_*` 字典的取值就在结构表各页签下半部分（Mac 侧第一轮看漏）。
- **两条待定，CAD 接入仍不解禁**：DS 称展示库为 SQL Server，Gavin 定按约定要求 PostgreSQL，已追问，LD-01 第 3 章不改；
  `sjc` 存在改数据不更新时间戳的情况，LD-01 8.3 滚动窗口校验升为必需项，等 DS 列出哪些操作不更新 sjc 后登记 DD。
- 第二轮 15 条已由 Gavin 直接发出（PG、案由视图、t_bd_dw、t_cjd 位置、sjc 细节与类型、坐标系、hwhrbh、工号对应、测试环境连接信息等）。
- 业务规模维持标书口径（峰值 5 万呼叫/日），不按近期其他城市的数字改。
- 开发机：第 2 批 ICP 链路不受影响，按 0004 清单继续。

## 2026-10-19  Mac 侧：CAD 增量源改为本方托管的中间库（DD-90）

- DS 提出由本方建中间库、他们写入。**接受，条件是结构为 CAD 74 张表原样、DDL 由 DS 交付**——与 DD-04 否掉的
  「我方定义结构、DS 映射写入」差一个词，DD-04 四条理由作为边界条件保留。
- 同集群单独 database `cad_mirror`（Gavin 定），写入角色 `cad_mirror_writer` 仅为该库属主，对 `oss911` 零权限，经
  pgBouncer session 模式单独池、TLS 写入。每表由本方加 `mirror_inserted_at / mirror_updated_at` 触发器列，作水位列；
  `sjc` 不可靠的问题由此解决，LD-01 8.3 回扫降回加固措施。
- DS 按主键 UPSERT，不 TRUNCATE、不整表重载；每周期写 `cad_meta.sync_heartbeat`，每日写 `cad_meta.source_daily_count` 供对账。
- 规范 `queries/CAD_Mirror_Database_Specification.docx` 已起草，空白项即 IR-51；入向网络通道为 IR-52 / 资源表 D-09。
- **对开发机的影响（暂不动手）**：IF-02 连接器仍为 `pg_read`，读本地集群另一个 database；`cad_mirror` 的建库、角色、
  pgBouncer 池、触发器函数将是一个新的结构变更加剧本步骤，等 DS 交付 DDL 后再排；`oss911_ingest` 对 `cad_mirror` 只 SELECT。

> **Mac 侧处理结果（2026-10-19 评审第三轮，四条全部采纳）**
> - staging 消费 raw 改为**按批次登记**：`meta.staging_batch_state (model, run_id, batch_seq, staged_at)`，取「可处理且本模型未登记」，
>   登记与模型写入同一事务；时间水位只缩小扫描。0004 之后的结构变更里加这张表（ingest 不写它，transform 角色写）。
> - 触发器时间戳不是提交时间：`cad_mirror` 取数上界 = 最新心跳 `started_at`（规范要求 DS 周期串行、心跳后写）；writer 角色设
>   `statement_timeout` / `idle_in_transaction_session_timeout`；**回扫维持必需项**；LD-10 加 TC-DISC-21。
> - `cad_mirror` 属主为 NOLOGIN `cad_mirror_owner`；`cad_mirror_writer` 只 CONNECT / USAGE / cad.* SELECT+INSERT+UPDATE / cad_meta.* INSERT。
> - 哈希段措辞改准：原批次已放行才去重命中；pending / discarded 不阻挡重新落地。
> - 规范升 v0.2（账号权限、周期串行、事务时长、心跳后写）。


## 2026-10-19  开发机：中间库 DDL 由 SQL Server 转 PostgreSQL 的十处差异，请 Mac 侧并入规范 v0.3 或 IR-51

起因：Gavin 问「DS 的 DDL 原先在 SQL Server 上用，切到 PG 要不要改」。要改，而且语法是最容易的部分——
语法转错建表就报错；难的是**建表成功之后的比较语义与取值差异，它们不报错，只让数据或关联悄悄对不上**。
对照规范 v0.2：3.2 已写基本类型对应（nvarchar→text、datetime→timestamp 等），另已要求 UPSERT、UTF-8、本地时间；
下列各条尚未覆盖。

### 一、比较语义（风险最大，看 DDL 看不出来）

1. **大小写**：SQL Server 默认排序规则大小写不敏感（`'A01' = 'a01'` 为真），PG 敏感。CAD 应用里大小写不一致的代码值，
   在原库能关联上，到中间库就关联不上；本方 staging 的映射同样受影响（码表里的代码与单据里的同一代码大小写不同）
2. **尾随空格**：SQL Server 比较时忽略尾随空格（`'abc' = 'abc  '` 为真），PG 的 text / varchar 不忽略。源端若有带填充
   空格的值，UPSERT 按主键匹配、关联与对账计数都可能不一致
3. **去重与关联的方向**：原库大小写不敏感，原库里不会有只差大小写的两个主键值，搬到 PG 不会冲突；但反过来，
   本方日后在 PG 里按这些键做的去重与关联，结果会与 CAD 原库不同

### 二、取值能否写进去

4. **NUL 字符**：SQL Server 的字符串可以含 `\0`，PG 的 text 不允许。遇到这种行 DS 的同步程序会写入失败，
   且每个周期都卡在同一行上
5. **datetime 精度**：SQL Server `datetime` 精度约 3.33 毫秒，`datetime2` 为 100 纳秒，PG `timestamp` 为微秒。
   直接搬问题不大，但取值由 DS 的程序转换，须写明舍入方式
6. **3.2 未列出的类型**：bit→boolean、uniqueidentifier→uuid、money / float→numeric 或 double precision、
   varbinary / image→bytea、tinyint→smallint。74 张表里有没有这些，要等 DDL 到了才知道
7. **空串与 NULL**：两边都把二者看成不同的值，本身不用改；但 DS 的同步程序若中间转过一道（经 CSV 或 ORM），
   可能把空串变成 NULL——要求原样保持

### 三、DDL 里不该带过来的东西

8. **自增与默认值**：IDENTITY、`DEFAULT getdate()`、计算列都不带。中间库是镜像，值一律照原库写入，不由中间库生成
9. **T-SQL 写的部分**：触发器、视图、`dbo` schema、方括号标识符、`GO`、聚集索引都不要。中间库里只有本方自己的
   触发器（`mirror_*` 两列）
10. **列名大小写**：SQL Server 标识符不分大小写；PG 里带引号的混合大小写名字（如 `"EventID"`）以后每条 SQL 都得加引号。
    规范已要求英文名，应进一步要求**全部小写、不加引号**

### 建议

- 请 DS **同时交两份 DDL**：PG 版（DS 负责转换），加上 CAD 原库现用的 SQL Server DDL。本方以原库 DDL 逐表核对转换，
  第 6、8、10 条在审查阶段即可查出
- 第 1、2、4 条写进规范，由 DS 回答：原库的排序规则是什么；同步时是否原样保留大小写与尾随空格；含 NUL 的值如何处理
- 第 1、2 条还会影响本方 staging 的写法（要不要统一转小写或去掉尾随空格）。这是一个设计选择，DS 答复后须登记 DD；
  在此之前 staging 不对 CAD 代码值做任何规范化

## 2026-10-19  开发机：结构变更 0004 已写好（未执行），附两处请 Mac 侧登记、七处开发机的实现选择

`ddl/changes/0004_ingest_batch_and_icp_cdr_raw.sql`，按 DD-88 · LD-01 2.5 · LD-02 第 9 章的 0004 清单写。在一次性的
PG 16 实例上从 0000 执行到 0004，`test/db_roles_check.sql` 全部通过（检查项由 66 项增至约 100 项）；新加的检查做过
反向验证：恢复 admin_svc 对契约表的 UPDATE、恢复 ingest 对 run_log 的 UPDATE、分区边界去掉时区偏移、去掉白名单 CHECK、
去掉 raw 指向批次的外键，五种改坏各自都被测出。真机由 Gavin 执行 40-schema。

### 一、请 Mac 侧登记（扩大或细化了已有决策）

1. **分区函数扩大到 raw（DD-77 的范围）**。0002 把 SECURITY DEFINER 的 `meta.ensure_monthly_partitions` 收窄到 core 与 audit，
   而 raw 按月分区（DD-88 第四条）后，没有它就没有人能以属主身份给 raw 建分区——接入角色不得自建（LD-02 第 9 章）。
   0004 以 CREATE OR REPLACE 重定义：允许 core · audit · raw，按分区键类型生成边界（integer 的 date_key 照旧；
   timestamptz 的 ingested_at 用 **UTC 月初零点**，带显式偏移，不受调用会话 TimeZone 影响——ingested_at 是系统时间，
   没有业务日含义）。40-schema 建初始分区那一步同步加上 raw。日常预建由编排负责，与 core 相同（DD-77）
2. **run_log 的结果取值**。CHECK 允许 success · failed · quarantined · partial 四种，DD-88 只用三种。开发机拟：批次被隔离
   也是成功提交，运行记 success、退出码 0（与 DD-84 一致）；`quarantined` 不再使用，但不为此改约束（改了没有收益，
   只多一个变更）。若 Mac 侧认为「整次运行的批次全部被隔离」应记 quarantined 以便 M-01 一眼看出，请定

### 二、开发机的实现选择（无异议即按此）

| # | 选择 | 理由 |
| --- | --- | --- |
| 1 | 热调白名单第一版**只有 `lookback_minutes`，取值 1–1440 的整数** | 契约里只有回看长度这一个可调参数；批量与限流（LD-06 2.4）尚无任何契约定义，名字与范围都无依据，等第一份用到它们的契约随结构变更加入。范围是开发机拟的：下限 1 防止关掉收口，上限 24 小时与接口单次上限一致 |
| 2 | admin_svc 对 `meta.source_runtime_param` 有 INSERT · UPDATE · DELETE | DELETE 即「恢复契约默认值」；DD-88 第七条「由运维在 M-01 清理」需要它。每次变更照旧写 `audit.runtime_param_change_log` |
| 3 | **收回 ingest 对 `meta.run_log` 的 UPDATE** | 0002 授它是为了「开始插入、结束回填」；改为结束时只写一行后不再需要，且 run_log 只增不改（LD-01 10.1）。依据不明的权限不留 |
| 4 | `meta.ingest_batch` 在清单之外多两列：`rows_written`（去重后实际写入 raw 的行数，≤ row_count）、`received_header`（收到的列名按原顺序） | 前者让「取到多少、新增多少」不必回查 raw；后者是因为 jsonb 不保留键序，而内容哈希按收到的顺序算（LD-01 2.5），有了它可从 payload 重算哈希 |
| 5 | raw 每行带指向 `meta.ingest_batch (run_id, batch_seq)` 的外键 | staging 按批次消费（DD-89），每行都必须属于一个已登记的批次；数据库保证比代码约定可靠，代价是每行一次主键查找 |
| 6 | raw 的 18 列一律 text、列名小写、不设 NOT NULL；`payload` 存完整收到的记录（含契约未登记的列）；`src_tz_offset` 留空 | raw 照收原文，坏数据也要能进（DD-76）；源端不给偏移，列按 LD-02 第 9 章的版式保留 |
| 7 | 命令行：增量 `oss911-ingest run --source X`（起点由水位算）；补数 `oss911-ingest run --source X --mode backfill --window 起/止` | DD-89 两种模式；接入程序第三步实现，DAG 只调增量 |

> **Mac 侧处理结果（2026-10-19，对以上两节）**
> - 十处差异：采纳。已并入规范 v0.3 第 3.6 节（八条要求）与答复事项 10、11；IR-51 补四项。staging 对 CAD 代码值
>   在 DS 答复排序规则前**不做任何规范化**，届时登记 DD。
> - 0004 两处登记：均同意，记入 DD-88 注（十、十一）。run_log outcome 只表达提交状态，`quarantined` 不用、CHECK 不改。
> - 七处实现选择：**全部无异议**，记入 DD-88 注（十二、十三）。
> - 契约现状：DS 侧（IF-02、IF-01）一份未写，等 DS 交 PG 版 DDL；Mac 侧拟先写一份样板 `if02a_cad_agency_event.yaml`
>   定 IF-02 契约形状（pg_read · host_ref cad_mirror · 水位 mirror_updated_at · 上界取心跳 · 回扫段），待 Gavin 定。

> **Mac 侧更正（2026-10-19 晚）**
> - Gavin 告知：**CAD 自身的库就是 PostgreSQL**，SQL Server 是上一代 BI（Power BI）附带的中间层，换成本方 BI 后不再存在。
>   因此「SQL Server→PG 十处差异」一节在中间库场景下不适用：规范 v0.4 撤回 3.6，改为要 CAD 原库 PG DDL 原样
>   （`pg_dump --schema-only`），本方只改表名列名、不改类型。那十条分析本身没错，留作归档库（IF-01，真 SQL Server）迁移时的参考。
> - 由此提出过 PG 逻辑复制（CAD PUBLICATION → cad_mirror SUBSCRIPTION）：**Gavin 明确不采用**，理由是 wal_level 与复制槽
>   对 CAD 生产库的风险。记入 DD-90 被拒方案五。中间库由 DS 程序 UPSERT 写入的方案与心跳围栏不变。

## 2026-10-19  Mac 侧：评审第四轮（契约发布与热调）四条全部采纳，请开发机实现

1. **校验只留一套**：规则放接入程序 `oss911-ingest validate-contract <yaml> [--with-overrides --source X]`，
   45-contracts 调它（部署机有 venv-ingest），M-01 写 runtime_param 前也调它。`contracts_to_json.py` 只做转换不做校验。
   最小集：endpoint、credential_ref、idempotent_key、columns 齐全；contract_version 正整数；window / lookback 类型与范围；
   idempotent_key 引用的列在 columns 里；**YAML 重复键拒绝**（自定义 loader，PyYAML 默认静默取后一个）。
2. 热调兼容性 = 用同一命令带现有覆盖值校验「新基线 + 覆盖」；`lookback_minutes: null` 之类在 Python 里按类型查，
   不靠 `contract_sync.sql` 的 `IS NULL`（jsonb null ≠ SQL NULL）。
3. `contract_sync.sql` 事务开头 `SELECT pg_advisory_xact_lock(hashtext('contract_sync'))`。
4. **回看只能调大**：覆盖值 < 契约默认值即校验失败。0004 的 CHECK 1–1440 不动（已执行，且只是类型护栏）。
已记 DD-88 注十四至十六、LD-01 2.5。


## 2026-10-20  开发机：评审第四轮四条已实现；另补一处竞态；校验器查出 if03b/c/d 三处契约问题

### 一、四条的落实

| 条 | 落在哪 |
| --- | --- |
| 1 单一校验 | `ingest/src/oss911_ingest/contract.py`，命令 `oss911-ingest validate-contract`。最小集照单实现，另加：列 `state` 取值、`host_ref` / `credential_ref` 须为小写标识符（它们要变成环境变量名）、疑似口令的键（由 contracts_to_json 挪过来）。YAML 重复键用自定义 loader 拒绝。`contracts_to_json.py` 只剩转换。单元测试 `ingest/tests/test_contract.py`（31 项，三处改坏均被测出） |
| 2 热调兼容 | `--with-overrides` 读库里现值，`--overrides '<json>'` 校验候选值（给 M-01 用：写库之前值还不在库里）。`lookback_minutes: null` 在 Python 里按类型判为非法；`contract_sync.sql` 里 `IS NULL` 那段已删 |
| 3 同步加锁 | 事务开头 `pg_advisory_xact_lock(hashtext('oss911.contract_sync'))`；实测第二个同步等到第一个提交才进 |
| 4 回看只能调大 | 覆盖值小于契约默认值即失败；新版本把默认值调到高于现有覆盖值时同样失败（覆盖值此时等于调小） |

两处与原文的差别：`--with-overrides` 不带 `--source` 时取契约自己的 source_id（剧本在解析前不知道 source_id；
带了就必须一致，防止拿 A 的热调值校验 B 的文件）；校验在**部署机**上以 airflow 账号、ingest 库用户执行，转换也在
部署机上用 venv-ingest 的 Python，校验与转换读的是同一份文件。

### 二、补的一处竞态（开发机的实现选择）

校验在同步事务之外跑。若校验之后、同步之前有人在 M-01 改了热调值，新值只按**旧**契约校验过，同步却会把新契约
写进去。做法：校验输出它看到的热调值，作为快照传进同步事务；事务内库里的值与快照不同即失败，提示重跑。规则仍只
在 Python 一处，SQL 只比两组值是否相同。

**对 M-01 的要求（服务层实现时登记）**：反方向的竞态在 M-01 一侧——它读契约、校验候选值、写库，若这期间部署提交了
新契约，候选值就只按旧契约校验过。M-01 应在同一事务里先 `SELECT … FROM meta.source_contract WHERE source_id = … FOR SHARE`
锁住该源的契约行，再校验与写入；部署的 UPDATE 会等它，它也会等部署。请在 LD-06 2.4 或 DD-88 注里记一笔。

### 三、校验器查出的契约问题（if03b/c/d，均未发布，不阻塞）

| 契约 | 问题 | 需要定的 |
| --- | --- | --- |
| if03b_icp_agent_session | `idempotent_key: [agentId, inTime]`，列定义里叫 `id`，没有 `agentId` | 接口字段名到底是哪个；改键还是改列 |
| if03c_icp_agent_day | `idempotent_key: [agentId, date_key]`，列定义只有 `IDX_*` 指标列，两个键都不在 | 坐席标识与日期是请求参数还是返回列；若是请求参数，幂等键的规则要扩展（键可来自请求参数），须登记 |
| if03d_icp_reference | 没有 `columns`：多数据集结构（vdn、agent、skill……），列定义应在每个数据集下 | 契约形状要改，校验规则随之扩展到按数据集查 |

### 四、icp.env 与 ingest.conf

30-python 生成 `/etc/oss911/ingest.conf`（0644：库地址、pgBouncer 端口、session 池库名 `oss911_etl`）；`icp.env` 在
inventory `icp_env.generate: true`（开发机）时生成假接口账号并保留，`false`（生产）时只断言三个变量齐全、只报缺的变量名。
变量名照 if03a 的引用：`OSS911_HOST_ICP_CMS_GATEWAY`、`OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT_USER / _PASSWORD`。

## 2026-10-20  开发机：接入程序本体已实现（ICP 话单 → raw），契约 if03a 升 v2；请 Mac 侧确认契约两处新增与九处实现选择

`ingest/src/oss911_ingest/`：`runner.py`（运行与批次）、`icp.py`（鉴权与下载，只用标准库）、`parse.py`（CSV 与内容哈希）、
`cdr.py`（if03a batch_checks 的代码形式）。单元测试 `ingest/tests/` 共 53 项，另在临时库加假接口上端到端实测：首次运行、
回看去重、注入坏数据整批隔离且退出码 0、隔离批次中的好行下次照常落地（DD-82）、待澄清同值只开一条、批次之间 token
失效自动重取、第二个实例拿不到咨询锁退出 1、接口拒绝并发下载、补数多批且不动水位、第 2 批失败记 partial、on_enter 模式。

### 一、契约 if03a v2 的两处新增（请确认）

1. **`window.source_timezone: UTC`**。增量窗口的终点是「源端时区的现在」，契约原来没有写时区。按 UTC 是假接口猜 1、2，
   联调最先核实。校验器要求有时间窗口的契约必须写它
2. **`enums.call_type.values`：完整 34 个值**。批次校验要判「不在值表内」，原契约只有呼入、呼出两个集合（20 个），值表本身在
   `3rd API/icp_call_type.csv`——那是开发材料、不随系统部署，运行时读不到。于是值表在契约与 CSV 两处，**须一致**；已核对
   逐个相同。若 Mac 侧认为应由 `tools/icp_enum.py` 直接生成契约里的这一段、或由 check_refs 交叉校验，请定

### 二、实现选择（无异议即按此）

| # | 选择 | 理由 |
| --- | --- | --- |
| 1 | 第一次增量须给 `--start-at`（只在没有水位时接受）；要取更早的时段用补数 | 水位的起点是一个判断，不能由程序默认（「从现在开始」会静默丢掉之前的数据）；补数不动水位（DD-89），所以要有一个显式的起点入口 |
| 2 | 源已停用（enabled = false）：什么都不做，退出码 0，不写 run_log | DAG 照常触发时不该报失败；run_log 的 outcome 没有「跳过」 |
| 3 | 某批失败即停，后面的批不取；退出码 1，run_log 记 partial 或 failed，`watermark_to` 为最后一个已提交批次的终点 | 增量水位按批推进，跳过失败批继续会越过缺口 |
| 4 | 拿不到咨询锁也写一行 run_log（failed，原因写明） | 每次连接器运行都写 run_log（LD-01 10.1）；撞锁是要看得见的事 |
| 5 | 被隔离的批次照样推进水位 | 隔离是已提交的结果，数据已在 raw，staging 等放行；不推进则下一次重复取同一段、永远卡住 |
| 6 | 待澄清：同一（源, 列, 值）已有 open / asked 的项就不再新增；`impacted_rows` 是首次提出时的行数 | 每 5 分钟一条重复项会淹没队列；ingest 无 UPDATE 权，不能累加 |
| 7 | 无法解析的文件（非 UTF-8、引号不闭合、列数不齐、重复列名）一行都不写，只登记批次与隔离 | 猜哪个值属于哪列会把值写进错列且不报错（猜 10）。**原文此时只有归档能留住（DD-87），开发机上尚未实现** |
| 8 | 时间格式按补零的 `YYYY-MM-DD HH:MM:SS` 严格检查，不符即隔离 | Python 的 strptime 也接受 `8:03:00`（单元测试抓到）；staging 依赖文本按时间排序 |
| 9 | 每个源的批次校验在代码里登记（`runner.CHECKS`），没有登记的源不能运行 | 没有「什么都收」的通用路径：每个源的校验都是它自己的 |

### 三、没做的（不阻塞，记下）

- **原文件归档**（DD-87，boto3）：上线前必须
- **`meta.value_profile`**（LD-01 7.1「值域外的取值写入 value_profile」）：没写，等 staging 时一并定由谁写
- **契约列在 `meta.mapping_registry` 的逐列登记**（LD-01 2.3「每一列都要有一行」）：接入只登记新出现的 unknown 列；契约里
  已有的 18 列由谁、何时写进去（契约同步时？）设计没写，请定
- **调用间隔**：猜 13「建议周期调用间隔 5 秒」——补数多批时连续下载、不等待。若真实平台限流，须加
- 假接口的「时序倒置」注入曾静默失效（IVR 腿 CALLBEGIN = CALLEND，对调后不变），接入联调时发现；已修，并补了逐窗口的自测

## 2026-10-21  开发机：自研代码统一放 `src/`（Gavin 定），接入程序移到 `src/ingest/`；请 Mac 侧改 CLAUDE.md

Gavin 定：本方自己写的代码一律放仓库根下的 `src/` 子目录，免得根目录越来越乱。第一步把 `ingest/` 整体移到
`src/ingest/`（git mv，历史可追）。包内仍是 Python 标准的 src 布局，所以源码路径是 `src/ingest/src/oss911_ingest/`。

已随之改的：30-python 的源码路径（部署位置 `/opt/oss911/ingest-src`、`venv-ingest` 不变）、`tools/check_no_cjk.py` 的
扫描范围（`ingest` → `src`，已验证 `src/` 下放一行中文会被查出）、两个测试文件的路径、runbook 07 与 10。

**请 Mac 侧改 CLAUDE.md**：第 3 节「交付代码一律英文」的范围清单里 `ingest` 改为 `src`；第 2 节写入点表格与第 11 节
文档地图若提到 `ingest/`，同样改为 `src/ingest/`。

没有动的、请 Mac 侧一并判断是否也要归入 `src/`：
- `recon/recon_hash_pg.sql`（对账 SQL，交付件）
- `test/db_roles_check.sql`（随部署运行的权限验证）与 `test/fake_icp/`（不交付的开发工具）
- `ddl/`、`contracts/`、`metrics/`：结构变更与业务口径，开发机认为不属于「代码」，建议留在根下

此后新写的代码（Airflow DAG、dbt 项目）直接放 `src/` 下，拟为 `src/dags/`、`src/dbt/`。

## 2026-10-21  Mac 侧：评审第五轮三条全部采纳，请开发机实现

1. `contract_sync.sql`：对该源的 `meta.source_contract` 行 `SELECT … FOR UPDATE` 之后再读 runtime_param 做兼容校验，持有到提交。
   首次进库无行可锁，沿用 advisory lock。M-01 侧的 `FOR SHARE` 约定已写进 LD-06 2.4，M-01 开发时照做。
2. `runner.py`：契约、contract_version、enabled、runtime_param 一条查询（或一个 REPEATABLE READ 事务）取出，读完固定；下载期间不持锁。
3. `contract.py`：补**必填层**。通用层之外，按 connector + 源类型声明必填字段；ICP 话单至少 `endpoint.path`、`auth.credential_ref`、
   `window.lookback_minutes`、`idempotent_key`、`columns`、`enums.call_type.values`。缺失 → 校验失败；运行时启动即退出码 2，
   不进数据路径。`enums` 缺失按配置错误处理，不隔离、不进待澄清队列。
4. 文字：`if03a_icp_cdr.yaml` 与 `cdr.py` 开头「进行中通话留给回看窗口」改为「进行中通话的收尾靠每日回扫（LD-01 4.4），回看收的是迟写入的话单」。
已记 DD-88 注十七至十九、LD-01 2.5、LD-06 2.4。


## 2026-10-21  开发机：评审第五轮已实现，并回复两点（措辞建议、每日回扫尚无人执行）

> 对上节「Mac 侧：评审第五轮三条全部采纳」的处理结果。

| 条 | 落实 |
| --- | --- |
| 1 行锁协议 | `contract_sync.sql`：每份契约先 `SELECT … FOR UPDATE` 锁该源的契约行，**之后**才比对热调值快照，持有到提交。实测：模拟 M-01 持 `FOR SHARE` 3 秒并在其间写入热调值，部署等待约 2.5 秒后以「热调值在校验后被改」失败；重跑部署通过。**反向验证**：拿 HEAD 上的旧版（先比对、后锁）跑同一场景，部署成功提交了 v3，而那个热调值从未按 v3 校验过——漏洞真实存在 |
| 2 同一快照 | `runner.py`：契约正文、版本号、enabled、热调值一条语句读出（热调值以子查询 `jsonb_object_agg`），读完整个运行沿用；不加行锁，下载期间不持锁 |
| 3 必填层 | `contract.py` 的 `REQUIRED`，按（connector, source_id）声明。ICP 话单在 Mac 侧所列 6 项之外，加上运行时实际读的 8 项：`endpoint.host_ref`、`endpoint.request_body`、`auth.header`、`auth.obtain.path`、`window.max_span_hours`、`window.min_span_seconds`、`window.source_timezone`、`watermark.column`。null、空串、空列表都按缺失算。**没有登记的源不能发布**（没有接入代码的契约发布了也跑不了）。实测：库内副本删掉值表后运行，退出码 2，未下载、未写批次、未写 run_log。`cdr.py` 里「缺值表即隔离」的分支已删 |
| 4 措辞 | 见下面第一点 |

**改的过程中出过一个真缺陷，已修**：把比对挪到 `SELECT … FOR UPDATE` 之后，中间的 `SELECT … INTO` 会重置 plpgsql 的 `FOUND`，
新契约因此落进 UPDATE 分支，更新 0 行却报 `updated`。临时库实测时发现（契约表是空的）。现在行是否存在在锁之后立即存进变量，
UPDATE 另断言恰好 1 行。修后把同步的七种情况重跑了一遍。

### 回复一：第 4 条的措辞

「进行中通话的收尾靠每日回扫，回看收的是迟写入的话单」不完全对。按 LD-01 4.4 与假接口的行为：通话短于回看（15 分钟）的，
下一次增量的回看就取到它的后续腿；只有长于回看的才靠每日回扫。契约与 `cdr.py` 均已改为：**「短于回看的由下一次增量的回看
取到，更长的靠每日回扫（LD-01 4.4）」**。同时把原句「必须恰有一行 -1」改为「至多一行」——原句与紧接着的「缺失不报错」自相矛盾，
代码一直按「至多一行」执行。这句话在 `quality.batch_checks` 里，是契约正文的数据，所以 **if03a 升 v3**。

### 回复二：每日回扫现在没有任何东西在跑

LD-01 4.4 与契约都依赖「每日回扫」补长通话，但它还不存在。拟在 DAG 那一步加：每天对前一天跑一次 `--mode backfill`（不动水位，
DD-89 现成），与 5 分钟增量共用 pool `icp_cms` 与咨询锁。回扫的时刻与覆盖范围（只前一天，还是前两天以盖住跨零点的长通话）请定；
开发机拟「每日 02:00 UTC，覆盖前一个完整的 UTC 日加前一日最后 1 小时」。

## 2026-10-24  开发机：troubleshooting 第 30 条（-K 口令输错时剧本静默卡住），请 Mac 侧把 CLAUDE.md 的「29 条」改为 30

重跑 30-python 时停在第一个要 root 的任务上不动。查明是 `-K` 口令输错：本地连接下 Ansible 不报错、不超时，一直等。
已用 `sudo -k` 加故意输错复现。做法：跑剧本前先 `sudo -v`。已写进 `troubleshooting.md` 第 30 条与 runbook 09。
CLAUDE.md 第 5 节「完整清单在 troubleshooting.md（29 条…）」请改为 30 条。

## 2026-10-24  开发机：ICP 话单的增量 DAG 与部署剧本 50-dags；七处实现选择请 Mac 侧确认

`src/dags/oss911_ingest.py` 按发布清单（45-contracts 复制到 `/opt/oss911/contracts` 的那份）每个源生成一个 DAG，现在只有
`ingest_icp_cdr_voice`：调度取契约 `window.schedule`（`*/5 * * * *`），任务就是 `oss911-ingest run --source icp_cdr_voice`。
`infra/playbooks/50-dags.yml` 部署它并当场解析校验。以 Airflow 3.3.2 自己的 DagBag 在本地解析过：参数如下表；未登记 pool 的源
生成时报导入错误（这正是期望的行为）。真机由 Gavin 执行，步骤与并发测试见 runbook/11。

| # | 选择 | 理由 |
| --- | --- | --- |
| 1 | DAG 由发布清单生成，不手写；一个源一个 DAG | LD-06：「DAG 由接入契约注册表动态生成，界面只展示不编辑」；轮询间隔是契约的发布项（LD-06 2.4） |
| 2 | 每个源走哪个全局 pool 写在 DAG 文件的 `POOLS` 里（`icp_cdr_voice → icp_cms`）；发布了却没登记 pool 的源，DAG 文件报导入错误 | 并发限制必须显式；pool 是编排的事，不放进面向业务的契约 |
| 3 | `skip_on_exit_code=None`；重试 2 次、间隔 2 分钟，不分退出码；超时 30 分钟 | DD-84 · LD-01 2.5「第一版一律有限次重试」；24 小时切片的下载以秒计，30 分钟没完即卡死 |
| 4 | `max_active_runs=1`、`catchup=False` | 增量从水位起算，追数是接入程序的事：停一天再开，下一次运行自己切批追到现在（真机已见：3 天缺口切成 4 批） |
| 5 | 任务环境只给 `PATH` 与 `LANG`，不继承 Airflow 进程环境 | 工作进程的环境里有元数据库连接串（含口令）；LD-01 2.5「口令不经 DAG、模板或 Airflow 父进程传入」反过来也成立：Airflow 的口令不该进接入程序 |
| 6 | 新 DAG 保持 Airflow 默认的「创建即暂停」，由运维手工开 | 开发机假接口不常开；**生产要定**：是部署后由剧本打开，还是保留人工开启这一步。注意不要让 DAG 的暂停变成第二个「启停」——源的启停只认 `meta.source_contract.enabled` |
| 7 | 50-dags 让 DAG 目录与 `src/dags/` 的 `.py` 一一对应，仓库里删掉的文件在目标上也删掉（Airflow 自己写的 `__pycache__` 除外） | 过期的 DAG 文件不是无害的残留——调度器会一直跑它（CLAUDE.md 第 5 节第四条的反例） |

仍缺：**每日回扫 DAG**，等上节问的时刻与覆盖范围。双活下 pool 是否全局生效，须在测试环境双节点验证（DD-85）。

## 2026-10-24  开发机：DAG 任务全部失败——Airflow 执行 API 地址指到了 Keycloak（已修）；另报一个生产隐患：Airflow 密钥未受管

**现象与原因**：`ingest_icp_cdr_voice` 每次都失败，接入程序一次都没被调到。Airflow 3 的任务进程运行时向执行 API 汇报，
默认地址 `localhost:8080`，而 8080 是 Keycloak；API server 当初因此改到 8081，汇报地址没跟着改。已在 `airflow.env` 写入
`AIRFLOW__CORE__EXECUTION_API_SERVER_URL`，30-python 并断言该地址回的是执行 API 的 health。troubleshooting 第 31 条。
**第 7 步「Airflow 完成」当时只验证了组件启动与元数据库连接，任务执行路径到今天才第一次走到**——开发日志里第 7 步的
「完成」应理解为「装好」而非「能跑任务」。

**生产隐患（请 Mac 侧登记，建议并入 LD-08 的 Airflow 部署）**：Airflow 的两把密钥目前不受剧本管理——
- `[api_auth] jwt_secret`：scheduler 签发任务令牌、API server 校验，**所有组件必须同一把**
- `[core] fernet_key`：加密元数据库里的连接与变量口令，所有节点必须同一把，换了旧密文即不可读

二者都是 Airflow 首次启动时随机生成、写进各自 `AIRFLOW_HOME/airflow.cfg` 的。开发机所有组件共用一个 `airflow.cfg`，
所以没出问题；生产上 ETL-1、ETL-2 双活加独立的 API server（LD-08），每台机器会各生成一把，**任务在另一台机器上汇报即被拒、
连接口令在另一台上解不开**，且是间歇性的——单机开发环境永远复现不了（CLAUDE.md 第 5 节第一条）。
拟：两把密钥与其他口令同样由剧本生成一次、存 `/etc/oss911/airflow.env`（0600）、各节点同一份；轮换流程进 runbook/91。
开发机可以现在就改成受管（生成后写入 env、组件重启一次；目前元数据库里没有任何加密的连接，换 fernet_key 无损）。请定是否现在做。

## 2026-10-24  Mac 侧：对 DAG 七处选择、评审两条与密钥问题的处理

- 评审第 1 条采纳并改方案：**DAG 不读契约 YAML**。45-contracts 在数据库同步提交后、作为最后一步原子写出发布清单
  `/opt/oss911/release/dags.json`（每源 source_id、schedule、pool、contract_version；写临时文件再 rename），DAG 只读它。
  `POOLS` 字典从 DAG 文件挪进清单。候选目录里先契约校验 + DagBag 解析，再同步数据库，再写清单。发布失败 → 清单不动。
- 评审第 2 条采纳：`window.schedule` 进该源类型必填层，校验器用 croniter 查 cron 语法。
- 七处选择：1、2、3、4、5、7 **同意**。6：保留人工开启，写成首次上线验收步骤（初始化水位 → 开启 → 确认首个任务成功）；
  **50-dags 重复部署不得改变暂停状态**。配套：增量模式无水位 → 退出码 2；首个水位由人工执行增量命令带 `--start-at` 建立（**不是 backfill**，DD-89 定它不动水位——此处原文写错，评审指出，已改）。
- 30 分钟超时：作初值可以，依据是按批提交、超时只丢当前批、每次运行有净进展。要落实：SIGTERM → 回滚当前批、正常退出；
  追数进度 M-01 可见（ingest_batch 已够）。
- 每日回扫：仍是未完成项。先做成可配参数（默认前一天 + 回看）在开发环境跑起来，范围待 ICP 问题单 Q19/Q20 等答复后定；
  加「至今无 -1 腿的 CALLID 数」统计作回扫是否够长的观测指标（LD-01 4.4）。
- **密钥受管：现在就做。** jwt_secret 与 fernet_key 由剧本生成一次、`/etc/oss911/airflow.env` 各节点同一份、已有优先纳管；
  轮换进 runbook/91。已记 DD-85 注、LD-08 3.3。
- CLAUDE.md：troubleshooting 改 31 条；第 9 节第 7 步加注「完成指装好」；硬约束加「DAG 只读发布清单」。

> **Mac 侧更正（2026-10-24 评审第七轮，三条采纳）**
> - 清单方案不是原子的：库提交后写 `dags.json` 失败 = **发布未完成**，剧本报错；重跑即使契约全部 unchanged 也必须重生成分发；
>   两节点都更新并验证通过才算完成；部署锁持有到清单分发结束。LD-01 2.1 措辞已改。
> - 首个水位：`oss911-ingest run --source X --start-at <时刻>`，不是 backfill。LD-01 2.5、LD-08 已改。
> - `dags.json` 由**库内全部受管源**生成（含 enabled=false），不是 `release.yml`；pool 映射独立为 `contracts/pools.yml`
>   覆盖全部受管源，库内有源而映射缺失 → 发布失败。


## 2026-10-24  开发机：第六、七轮评审已实现；三处落实方式与原文不同，请 Mac 侧确认

> 对上面「Mac 侧：对 DAG 七处选择、评审两条与密钥问题的处理」及其第七轮更正的处理结果。

### 一、已实现（临时库加假接口实测，真机待 Gavin 执行）

| 项 | 落实 | 实测 |
| --- | --- | --- |
| DAG 只读发布清单 | `src/dags/oss911_ingest.py` 只读 `/opt/oss911/release/dags.json`；清单缺失即导入错误 | DagBag 解析：一源一增量 DAG，停用的源也在；有时间窗口的源另有回扫 DAG |
| 清单的生成 | 新子命令 `oss911-ingest dag-manifest`：库内全部受管源（含 enabled = false）＋ `contracts/pools.yml`；缺 pool 或缺 schedule 即失败；临时文件加 rename，内容不变不重写（重跑 `changed=0`） | 首次写、不变、停用的源仍在、缺 pool 失败且旧文件不动 |
| 45-contracts 顺序 | 复制候选 → 契约校验 → 候选清单＋Airflow DagBag 按文件名解析本次的 DAG 文件 → 同步 → **每次都**从库重写清单；清单写不出时剧本报「RELEASE UNFINISHED」并说明重跑即可 | — |
| schedule 必填 | 进必填层；校验器只查「5 个字段」，语法以 Airflow 解析为准（见下第二节 1） | — |
| SIGTERM | 转成异常：回滚进行中的批、写 run_log、退出码 1 | 三天补数在第 2 批下载中途发 SIGTERM：第 1 批已提交，第 2 批无登记，run_log `partial`、`watermark_to` 停在第 1 批终点、原因写明 |
| 每日回扫 | `--mode resweep --days N`（1–30，默认 1）：窗口 = 源端时区今天零点往前 N 天、再往前一个回看；批次记 `backfill`（0004 的 CHECK 只有两值），不动水位。DAG `resweep_<源>` 每天 02:00 UTC，与增量同 pool | 窗口与批次、水位不动均对 |
| 「无 -1 腿的 CALLID 数」 | 回扫结束时计算：窗口内开始的腿所属 CALLID 中，**全部腿里**都没有 -1 的个数（零点前开始的呼叫末腿可能在零点后）；打印并写进 run_log 的 message（见下第二节 2） | 2 个，与独立查询一致 |
| 暂停状态 | 50-dags 不碰暂停；首次上线三步写进 runbook/11（`--start-at` 建水位 → 开启 → 确认首个任务） | — |
| Airflow 密钥受管 | 30-python：env 文件已有 → 沿用；否则取 Airflow 自己写进 `airflow.cfg` 的 → 都没有才生成（fernet 用 Airflow 自带的 cryptography 生成）；写入 `airflow.env`。runbook/91 第四节写了两把各自的轮换步骤——**「删文件重跑」对它们不是轮换**（会从 airflow.cfg 把旧值纳管回来），fernet 按「新,旧 → rotate-fernet-key → 只留新」 | 三种来源优先次序按剧本原文测过 |
| 50-dags | 按清单核对：每个 `ingest_<源>`、每个应有的 `resweep_<源>` 都在 | — |

### 二、与原文不同的三处，请确认

1. **cron 语法不用 croniter**。croniter 不在接入程序的依赖里，引入它要过 MANIFEST、许可与上游健康度（DD-46）；而 Airflow 自己
   就用 croniter 解析 schedule。最权威的检查是部署时让 Airflow 解析一遍 DAG——第六轮本来就要求候选目录里做 DagBag 解析。于是
   校验器只查形式（5 个字段），语法以 45-contracts 第 3 步的 Airflow 解析为准。规则仍只有一处权威，也不多一个依赖
2. **「无 -1 腿的 CALLID 数」暂放 run_log.message**。没有指标表，Prometheus 也还没接入接入程序。等监控那一批再做成正式指标；
   在此之前按 `outcome = 'success' AND message LIKE 'open calls%'` 可查
3. **「部署锁持有到清单分发结束」改为「写清单的命令自己拿同一把锁」**。同步事务的 `pg_advisory_xact_lock` 在提交时释放，剧本是
   独立的一条条命令，没法让一个会话跨任务持锁，生产上两台 ETL 各写各的更做不到。`dag-manifest` 在读库到 rename 之间持有同一把锁
   （会话级 `pg_advisory_lock`，同一个键 `oss911.contract_sync`），与同步串行：任何一台节点最后写下的，都是它持锁那一刻库里的最新
   状态，较早的部署不可能最后写入旧清单。实测：同步持锁 3 秒，`dag-manifest` 等了 2.6 秒才写。用 ingest 库用户读库（对 meta 只读），
   ETL 节点本来就有 `ingest.env`

### 三、留意

- 生产上同步只该在一处执行、清单要在每台 ETL 节点上写；开发机单台，剧本现在是 `hosts: all`。inventory 分组随 LD-08 的部署清单定，
  届时同步一步加 `run_once`、清单一步按 ETL 组执行
- 回扫的范围（默认 1 天）与时刻（02:00 UTC）是首版值，待 ICP 问题单 Q19 / Q20 答复后定

## 2026-10-25  开发机：troubleshooting 第 32 条（env 文件同时被 systemd 与 shell 读，含空格的值须加引号）；请 Mac 侧把 CLAUDE.md 的 31 条改为 32

graphviz 警告的屏蔽对 Airflow 服务生效、在命令行却不生效：值里有空格未加引号，shell 读 `airflow.env` 时把它截断。已加引号并以两种读法验证。

> **Mac 侧确认（2026-10-25）**：三处落实方式**均同意**（croniter 不引入、open calls 暂放 message、dag-manifest 自持锁——后者比原文正确）。
> 两条待办：回扫批次记 `backfill` 是 0004 CHECK 两值的权宜，下一个结构变更加 `resweep`；「无 -1 腿的 CALLID 数」在监控那批转正式指标。
> CLAUDE.md 已改 32 条。

## 2026-10-25  Mac 侧：DS 第二轮答复要点（影响后续 CAD 模型，现在不动代码）

- **`sjc` 不可依赖**（DS 原话）：中间库触发器时间戳是唯一路径，回扫必需。
- 单据链：`t_jzsjd → t_cjd（每次派遣）→ t_unitddzld（每 unit）→ t_fkd`，一对多。派警事实粒度据此定。
- `t_dm_czlx` 是 `t_sjczjl`（时间线）的 OperateType；`t_unitczjl` 只记 unit 状态变更。此前理解有误。
- 工号是否与华为同套：DS 不保证 → IR-53 发 MOI 二选一（行政保证同号 / 本方维护对照表）。
- 坐标暂定 WGS84 经纬度（AS-35）；`hwhrbh` 废弃；CLOSE=100、CANCEL=110；案由两视图以表同步。
- 中间库规范（IR-51）答复未到，CAD 接入仍不开工。

## 2026-10-25  Mac 侧：中间库 DDL 改由本方生成（DD-90 条件一更正）

- Gavin 告知：上一代 SQL Server 中间库的 DDL 是当时的 BI 方建的，DS 只写入。现在也一样：**我们建，DS 核对会签**。
- 结构表里的类型是上一代中间库的 SQL Server 类型，不是 CAD 原库的；按规范 3.2 映射，**宁宽勿严**（text / timestamp / numeric / bigint），
  写入不得因类型失败，收紧在 staging。开发机那十条差异里 NUL、空串、尾随空格几条对 DS 的搬运程序仍然有效。
- 74 张表可能是上一代 BI 挑的子集 → 向 DS 要 CAD 原库 `information_schema.columns` 完整目录（规范 v0.5 第 3.6 节）。
- 主键结构表未标，必须 DS 答（IR-51 第 2 项）。
- 拟：Mac 侧写 `tools/cad_mirror_ddl.py`（不交付）从结构表生成 74 张建表 SQL，输出交开发机审类型映射后按结构变更流程放进
  `ddl/cad_mirror/`；主键以 `-- PK: TBD` 占位，DS 答复前不执行。待 Gavin 定。

## 2026-10-26  Mac 侧：cad_mirror DDL 生成器与列目录，请开发机落为结构变更

- DS 答复：**每张表第一列即主键**。`tools/cad_mirror_ddl.py` 据此从结构表生成 74 张表的 DDL（schema `cad`），
  `contracts/cad_mirror_catalog.csv` 是 1060 列的目录（源表、镜像表、源字段、镜像列、源类型、映射类型、中文说明）——
  中文说明只进 CSV，不进 SQL（check_no_cjk）。类型宽映射：text 720、bigint 204、timestamp 136，无未映射类型。
- 请开发机：`python3 tools/cad_mirror_ddl.py > ddl/changes/0005_cad_mirror.sql`，审一遍类型映射与列名（翻译列小写，
  同表重名加序号后缀——实测无重名），按结构变更流程登记。注意：
  1. `cad_mirror` 是**另一个 database**，不在 oss911 的 0000–0004 序列里：建库、`cad_mirror_owner`（NOLOGIN）与
     `cad_mirror_writer`（LOGIN，超时参数）是集群级，放剧本（建议 41-cad-mirror），DDL 以 owner 身份在该库执行；
     角色超时 `statement_timeout = 120s`、`idle_in_transaction_session_timeout = 60s`（规范第 4 节）。
  2. 生成的 SQL 末尾的 GRANT 只授表级权限，不含 CREATE / DELETE / TRUNCATE / TRIGGER（LD-07 2.1）。
  3. pgBouncer 为 `cad_mirror` 加 session 模式单独池（LD-08 第 8 章）。
  4. 结构表 Source Index 跳号的四张表（t_sld 17、t_gb 5、t_rms_xyjhjz 5、t_rms_zzdy_sb 3）按现状生成，已问 DS。
  5. **暂不执行到真机**，等 DS 会签规范与 DDL（IR-51）。开发库可先建，便于写 IF-02 契约与 pg_read 连接器。
- 主键清单（源表 · 镜像表 · 源字段 · 镜像列），请过一眼，有疑问的标出来：

| 源表 | 镜像表 | 主键源字段 | 镜像列 |
| --- | --- | --- | --- |
| t_rms_bmgz | t_coderules | id | -> |
| t_rms_xyjhjz | t_responseplanagency | lsh | -> |
| t_gb | t_broadcast | id | -> |
| t_gb_group | t_broadcastgroup | id | -> |
| t_dxdwfqjl | t_smslocation | id | -> |
| t_zjb | t_evidence | lsh | -> |
| t_bd_tzjsfz | t_notificationrole | xlbm | -> |
| t_sjczjl | t_chronology | lsh | -> |
| t_rz_tysjrz | t_auditlog | lsh | -> |
| t_sfdzkzb | t_eventaddressextension | lsh | -> |
| t_jqfpjl | t_eventdistribution | lsh | -> |
| t_jzsjd | t_agencyevent | jzsjdbh | -> |
| t_jqfpzjl | t_eventdistributiongroup | lsh | -> |
| t_sxgdjb | t_loginandlogoutlog | lsh | -> |
| t_jajl | t_eventclosure | lsh | -> |
| t_bjlxr | t_callerinfo | id | -> |
| t_sjd | t_commonevent | sjdbh | -> |
| t_unitczjl | t_unitoperation | lsh | -> |
| t_cjd | t_dispatchrecord | cjdbh | -> |
| t_pjjh | t_dispatchplan | pjjhbh | -> |
| t_fkd | t_feedbackrecord | fkdbh | -> |
| t_yhdqqx | t_userpermission | lsh | -> |
| t_bd_tzglb | t_notificationassociation | lsh | -> |
| t_bd_tzpzb | t_notificationconfiguration | lsh | -> |
| t_unitsxgdjb | t_unitlogonandlogoff | lsh | -> |
| t_rms_zzdy_cl | t_unitvehicle | id | -> |
| t_bd_aydwglb | t_agencyandeventtypemapping | lsh | -> |
| t_rms_zzdy_zbz | t_logonunitgroup | id | -> |
| t_grpzx | t_personalsetting | lsh | -> |
| t_hw_hjdxjl | t_callrecord | lsh | -> |
| t_rms_cl_zbz | t_vehicledutygroup | id | -> |
| t_rms_cl | t_vehicle | id | -> |
| t_jzsjd_ymszl | t_agencyeventcolumnsetting | id | -> |
| t_jzsjd_ymszdx | t_agencyeventobjectsetting | id | -> |
| t_rms_zzdy | t_unitinfo | id | -> |
| t_bd_aykzb | t_eventtypeextension | ay | -> |
| t_zzdy_zt | t_unitstatus | id | -> |
| t_sjkzzd | t_eventextensionfields | lsh | -> |
| t_rms_zzdy_sb | t_unitequipment | lsh | -> |
| t_rms_zzdy_ry | t_unitperson | lsh | -> |
| t_dhhr_sld | t_callrecordlinkedwithcontactevent | lsh | -> |
| t_sop_czjl | t_sopoperationlog | lsh | -> |
| t_agency_temp | t_opencontactevent | lsh | -> |
| t_zzdy_ymsz | t_unitsettings | lsh | -> |
| t_bd_tzjsglb | t_notificationroleassociation | lsh | -> |
| t_jzsjd_ymsz | t_agencyeventwebpagesetting | id | -> |
| t_rms_zbz | t_lineupgroup | id | -> |
| t_rms_gw | t_lineuprole | id | -> |
| t_cjzlzf | t_cancellationinstructionlog | zfbh | -> |
| t_rms_zzdy_lx | t_unittype | id | -> |
| t_coverage | t_collection | lsh | -> |
| t_jqxgjl | t_eventupdatelog | lsh | -> |
| t_zjjl | t_transferlog | lsh | -> |
| t_rms_zd | t_station | id | -> |
| t_sld | t_contactevent | sldbh | -> |
| t_dhhrjl | t_callnotify | hrbh | -> |
| t_wgczjl | t_violationoperation | lsh | -> |
| t_remark | t_remark | lsh | -> |
| t_unitddzld | t_unitinstruction | unitddzlbh | -> |
| t_ycddsj | t_delayedevent | ycddsjbh | -> |
| t_dm_cllx | t_calldispositiondictionary | bh | -> |
| t_dm_yxj | t_prioritydictionary | bh | -> |
| t_dm_jqjb | t_alarmleveldictionary | bh | -> |
| t_dm_xtdm | t_eventstatusdictionary | dm | -> |
| t_dm_wgczlx | t_violationtypedictionary | bh | -> |
| t_dm_remark_yxj | t_remarkprioritydictionary | bh | -> |
| t_dm_cjlx | t_eventdispositiondictionary | bh | -> |
| t_dm_offline_reason | t_offlinereasondictionary | bh | -> |
| t_dm_czlx | t_operationtypedictionary | bh | -> |
| t_dm_bjfs | t_alarmmethoddictionary | bh | -> |
| t_dm_unitskill | t_unitskilldictionary | bh | -> |
| t_dm_unitztlx | t_unitstatusdictionary | bh | -> |
| t_dm_unitlxtb | t_unittypedictionary | bh | -> |
| t_dm_tzjcsj | t_alarmtriggerdictionary | zdxid | -> |
- （表中「镜像列」一栏误取了分隔符，真正的列名见 `contracts/cad_mirror_catalog.csv` 每表 source_index = 1 的行；开发机已指出。）
- 两处已向 DS 追问：`t_dhhrjl` 主键 = CALLID 是否意味着 CAD 范围内 CALLID 唯一；`t_sld.sldbh`「event 数据时为首次受理单编号」是否会出现多行。


## 2026-10-26  开发机：cad_mirror 已落为独立变更序列与剧本 41；生成器改了六处（Gavin 定由开发机改）；收回各库公开的 CONNECT

> 对上节「Mac 侧：cad_mirror DDL 生成器与列目录，请开发机落为结构变更」的处理结果。Gavin 2026-10-26 定三件：
> 一、另起一套变更序列，不进 ddl/changes.yml；二、开发阶段可改写已执行的变更，正式软件发布起才「执行过不能改」；
> 三、生成器由开发机改，改完告知 Mac 侧。

### 一、落在哪

- `ddl/cad_mirror/changes.yml`：0000 登记表（schema `ddl`，写入账号碰不到——不能放 cad_meta，写入账号对那里有 INSERT），
  0001 生成的 74 张表。全部 `run_as: owner`（属主拥有该库，能建 schema，这个序列没有超级用户变更）
- 执行器从 40-schema 抽成 `infra/playbooks/tasks/ddl_changes.yml`，oss911 与 cad_mirror 共用一套规则；抽出后把参数代回去与原文
  逐行比对，**只多了「开发阶段可重建」的提示**，40 的行为不变
- `infra/playbooks/41-cad-mirror.yml`：两个角色（属主 NOLOGIN、写入账号 LOGIN 加两个超时）、建库、执行变更、按写入账号的权限
  核对 19 条。开发阶段改了已执行的文件 → 提示 `-e cad_mirror_rebuild=true` 整库重建；inventory `cad_mirror_ddl_frozen: true`
  之后拒绝重建。写入账号口令存 `/etc/oss911/cad_mirror_writer.env`（root 0600，交 DS），轮换写进 runbook/91
- pgBouncer 加 `cad_mirror` session 池 5 个连接，全局上限 70 → 75
- runbook/12

### 二、生成器改的六处（`tools/cad_mirror_ddl.py`，均在临时集群实测）

| # | 问题 | 改法 |
| --- | --- | --- |
| 1 | 21 个翻译列名是 PostgreSQL 保留字（`order` 19、`primary`、`collation`），**建表直接语法错误** | 改名而不加引号（加引号则 DS 与我们每条 SQL 永远要带）：`order` → `sort_order`，`primary` → `is_primary`，`collation` → `sort_rule`；**`t_dispatchplan.yxj`（优先级）原译 `order` 是翻译错误，改为 `priority`**。生成器内置 PG 16 的保留字表（取自 `pg_get_keywords()`），再出现即失败。列目录 CSV 随之改了 21 行 |
| 2 | 触发器对原样重写也推 `mirror_updated_at`，还多一个行版本 | UPDATE 时内容不变即返回 NULL：不写新版本、不动时间戳。实测 xmin 不变。DS 重发一天或整表重写不再造成伪变更（DD-78） |
| 3 | UPSERT 能冲掉 `mirror_inserted_at` | UPDATE 时两列一律沿用旧值，写入方写什么都无效 |
| 4 | 水位列 BRIN | 改 B-tree：中间库的行被 UPSERT 原地改写，按 DD-78 不宜 BRIN |
| 5 | `CREATE SCHEMA IF NOT EXISTS` | 去掉（执行与否由变更登记决定，DD-73）；触发器函数固定 `search_path`、收回 PUBLIC 的 EXECUTE |
| 6 | 无默认权限 | 补 `ALTER DEFAULT PRIVILEGES`：日后新增的表自动授写入账号与 `oss911_ingest`。库级 CONNECT 改由剧本授（见下） |

另：NOTES 那张主键表的「镜像列」一栏全是 `->`——取的是生成器标准错误输出里的分隔箭头，真正的列名在箭头后（如 `bh -> number`）。

**请 Mac 侧把规范 v0.5 里写给 DS 的两点同步**：其一，原样重写的行 UPSERT 会回报「0 行受影响」，这是有意的，DS 的程序不得据此
判定失败；其二，21 个改名的列（目录 CSV 里可查）。

### 三、收回各库公开的 CONNECT（顺带发现，与中间库直接相关）

PostgreSQL 默认把每个库的 CONNECT 授给 PUBLIC，**DS 的写入账号实测能连上 oss911**，读得到系统目录（全部表名、函数源码）。
LD-07 2.1「对 oss911 零权限」原来做不到。现在由 `tasks/db_connect.yml` 在各库自己的剧本里收回、只授真正用它的角色：

| 库 | CONNECT 给谁 | 剧本 |
| --- | --- | --- |
| oss911 | 四个权限角色（其登录用户继承）、pgbouncer（auth_query 在此库执行） | 40 |
| cad_mirror | cad_mirror_writer、oss911_ingest | 41 |
| airflow、keycloak | 只有属主 | 30、22 |
| postgres、template1 | 只有超级用户 | 20 |

21 的 auth_query 探测用户改为显式授 CONNECT、删除前先收回（带着授权的角色删不掉，实测）。临时集群实测：写入账号连其他五个库全部
被拒，`ingest`、`pgbouncer`、探测用户照常连得上；41 的 19 条核对全过，改坏三处（给写入账号 DELETE、把 oss911 的 CONNECT 还给
PUBLIC、删一个触发器）各自被查出。**请登记**（建议并入 LD-07 2.1 或新 DD）：各库 CONNECT 不对 PUBLIC 开放。

### 四、暂未做

- IF-02 契约与 pg_read 连接器（心跳围栏取数上界，DD-90）
- 跳号的四张表、`t_dhhrjl` 主键 = CALLID、`t_sld.sldbh` 多行两处追问，等 DS

## 2026-10-26  Mac 侧：对开发机 cad_mirror 一节与评审第八轮的处理

**开发机三件**：规范 v0.6 已加 3.1a（21 处改名、两处重名列待 DS 译名）与「原样重写回报 0 行是有意的」；各库 CONNECT 收回记入
LD-07 2.1、DD-74 注、CLAUDE.md 硬约束（第十三条）；Gavin 定的三件（独立序列、开发期可重建、生成器归开发机）记入 DD-73 注。
生成器六处改法全部同意，第 2 条（内容相同不写新版本）尤其对。主键表的「->」是我取错了列，已在那节加注。

**评审第八轮四条全部采纳，请实现**：
1. 每次部署独立不可变候选目录（按发布版本号），复制、校验、DagBag、转换、pool 读取都用这一份；清单写入照旧加锁 + rename。
2. 回扫窗口由 DAG 按 `data_interval` 传 `--window`，程序不再用 `datetime.now()`；回扫 DAG `catchup=True`、`max_active_runs=1`；
   增量 DAG 仍 `catchup=False`。重试同窗口，停机漏日按逻辑日期自动补。
3. Airflow 两把密钥：权威只有控制节点一份（生成一次或从首台纳管一次），分发到全部 ETL 节点，启动前断言一致、不一致即失败。
   单机验收不了，留到双节点测试；开发记录里「双节点共享」先不标完成。
4. 无末腿统计分两个数：已收到末腿 / 已有可用末腿（排除 pending、discarded），收尾判断用后者。
已记 LD-01 2.5、LD-08 3.3 / 7.2、DD-85 / DD-89 注。


## 2026-10-26  开发机：评审第八轮四条已实现；三处由 Gavin 定的落法

> 对上节「Mac 侧：对开发机 cad_mirror 一节与评审第八轮的处理」的处理结果。

| 条 | 落实 | 实测 |
| --- | --- | --- |
| 1 不可变候选目录 | `tasks/release_dir.yml`：`/opt/oss911/releases/<内容哈希前 12 位>/`（root），含契约、pools.yml、resweep.yml、DAG 文件；只复制不覆盖，每次部署在目标机上按同一脚本（`infra/files/release_hash.py`）重算哈希核对；`SOURCE` 记 git 提交号与工作区是否干净；保留最近 10 个。45 的校验、预演、转换、清单都读它；50 安装的 DAG 文件也从它取，找不到当前内容的目录即失败（「先跑 45」）。旧做法留下的 `/opt/oss911/contracts` 与 `release/candidate` 由 45 删除 | 临时目录：首次建、重跑 `changed=0`、改动其中一个文件后部署失败 |
| 2 回扫按逻辑日期 | 程序：`--mode resweep --day YYYY-MM-DD`，窗口 =（该日 00:00 − 有效回看，次日 00:00），不再用 `now()`；当天未过完即退出码 2。DAG：`--day {{ macros.ds_add(ds, -1) }}`、`catchup=True`、`max_active_runs=1`，按源端时区 02:00 触发（清单带 `timezone`）；增量仍 `catchup=False` | DagBag：从 resweep_from 次日 02:00 起每天一次，各扫前一天；渲染出 `--day` 正确 |
| 3 密钥权威在控制节点 | 30-python：权威文件 `/etc/oss911/control/airflow_keys.env`（root 0600，inventory `control_secrets_dir`）；没有时从第一台节点纳管一次；各节点从它取值，写 env 后、启动前按哈希断言一致，不一致即失败。runbook/91 改为「轮换只改控制节点那一份再重跑 30」 | 按剧本原文测三种：无权威→从节点建、权威不同→节点取权威、节点被改→断言失败。**双节点分发与一致性未验，开发记录不标完成** |
| 4 无末腿两口径 | 回扫后报两个数：没收到末腿的 / 没有可用末腿的（排除 pending、discarded），都写进 run_log 的 message | 临时库：注入重复使第 1 批隔离 → 收到口径 1、可用口径 3315；重试同一天，隔离批中的好行照常重新落地 → 两口径都回到 1 |

### Gavin 定的三处

1. **候选目录按内容哈希命名**（开发阶段没有发布版本号）：内容相同即同一目录，不可变由此自然成立
2. **回扫起始日由人在首次上线时显式填写**：新文件 `contracts/resweep.yml`（每源一个日期）。没填的源**没有**回扫 DAG——
   补跑从这个日期起，写早了一开启就排出一长串，所以它必须是一个明确的决定。首次上线步骤第 4 步：填日期、重新部署、开启回扫
   （runbook/11）。开发机填 2026-10-25
3. **控制节点的密钥放 `/etc/oss911/control/airflow_keys.env`**：生产上控制节点是哪台、这个文件怎么备份，随 LD-08 的部署清单定

### 与原文不同的一处

第 2 条原文是「DAG 按 data_interval 传 `--window`」。两处改动：一，回扫 DAG 用的 CronTriggerTimetable 的 data_interval 长度为 0
（起止都是触发时刻），只能用逻辑日期；二，DAG 只传**日期**，窗口由程序算——回看可热调，只有程序启动时才知道有效值，由 DAG 拼
窗口等于回看有了第二个来源。手工触发回扫因此须带逻辑日期：`af dags trigger resweep_<源> -l <日期>T02:00:00+00:00`。

## 2026-10-26  开发机：第 2 批 dbt 层（raw → staging → core.fact_call_leg）的实现方案，请 Mac 侧确认

结构变更 0005（`meta.staging_batch_state`、`ingest_batch.mode` 增 `resweep`）已提交（8041c59），未上真机。下一步 dbt。把设计里约束
这一层的条文逐条查过（LD-02 第 3、4、6、9、12、13 章，DD-06 / 66 / 74 / 77 / 78 / 79 / 82 / 89，契约 if03a，指标定义，HD-03），
**有十二处须先定**——不少是一经上线就改不动的（代理键）。开发机不替设计做这些决定。

**本步范围**：staging `stg_icp_cdr_voice` 与 `core.fact_call_leg`。`core.fact_call`（通话级聚合）规则缺口更多（见第三节），放下一步。

### 一、需定

| # | 问题 | 依据 / 现状 | 开发机建议 |
| --- | --- | --- | --- |
| 1 | **ICP 的 `src_system` 取什么值**。`core.sk(站点, src_system, 自然键)` 的一部分——**上线后改它等于全部主键重算** | 两张事实表 NOT NULL，任何文档、契约、DDL 都没给值 | `icp`：按「系统」而不按「源」（`icp_cdr_voice`）取，同一系统的话单、参照数据、坐席会话共用一个命名空间，坐席工号在 dim_agent 与 fact_call_leg 上算出同一个键 |
| 2 | **站点**：`site_code` 与 `core.dim_site` 那一行由谁写 | DDL 注释 `'MKK'`；dim_site 无写入方；if03d 把 vdn 数据集指向 dim_site，但 if03d 未发布 | `MKK`；以结构变更写入这一行（站点是部署常量，不是接入数据）。**name_ar / name_en 请给定**，开发机不编 |
| 3 | **坐席与技能队列维度是空的**（if03d 未接入），而 `agent_sk` / `queue_sk` 有外键 | 只有接入侧的「维度迟到进待澄清、不隔离」（LD-01），core 侧没有规则；dim 的 name、role_code NOT NULL，造不出占位成员 | 维度到之前 `agent_sk` / `queue_sk` 留空，原值在 `device_no` / `queue_code` 里；维度接入后由同一模型重算填上（按第 7 条它属于「派生内容变了」，会被写入）。不造推断成员 |
| 4 | **DD-06 与两个 CHECK 冲突**：`direction` 只许 inbound / outbound / internal，`release_source` 只许 agent / caller / system——**没有「未映射」可写** | DD-06「未映射不得归入其他」。契约把两个集合以外的 CALLTYPE 一律当 internal——已知的内部呼叫与「值表里根本没有」的码被混在一起 | 新结构变更给两列 CHECK 加 `unmapped`。值表内、但不在呼入 / 呼出集合 → `internal`；**值表外 → `unmapped`**；release_cause 不在归类表 → `unmapped`。未映射行数作为 M-03 可见的计数 |
| 5 | **释放原因归类表（429 个码）运行时放哪** | 在 `3rd API/icp_release_cause.csv`，不随系统部署（与当初 CALLTYPE 值表同一问题）；M-C03、M-C11 压在它上面 | 进契约 if03a 的 `enums.release_cause.classes`（码 → agent / caller / system），升 v4。理由同 CALLTYPE：LD-01 4.3「取值集合登记在契约 definition 中」，运行期唯一读取点是库。须与 CSV 一致，由生成脚本写入并比对 |
| 6 | **主叫号码哈希 `caller_number_hash`** | DD-35 敏感字段；没有算法、盐、规范化；CALLERNO 可为邮件或网址；不加盐的电话号码哈希可被穷举还原 | 本步先**留空**，不建议现在定一个将来要改的算法（改算法 = 历史哈希全变，R-06 的分组断档）。若要现在做：HMAC-SHA256，密钥只存 `/etc/oss911`、不入库，规范化规则另定。请定 |
| 7 | **DD-82「版本更大」在 core 里没法比**：core 没有存版本的列 | 契约 `core_version: from: observed_at`；observed_at 不是任何表的列 | 新结构变更给 `fact_call_leg` 加 `src_observed_at timestamptz`（取所选观测的 `raw.ingested_at`——必须是**收到**的时刻，不是 staging 的时刻：周一的批次周四放行，不得覆盖周二的新数据）。写入条件：观测更新 **且** core 行的派生内容哈希不同；或同一观测、派生内容变了（归类表更正、维度补齐）。「内容」按 core 行算而不按源端原文——否则第 3、5 条的重算永远写不进去 |
| 8 | **一行的 `date_key` 变了怎么办**（同一 CALLID+CALLIDNUM 的 WAITBEGIN 变了） | 无规则。主键 (sk, date_key)，跨分区唯一只靠测试 | 写入按 sk 先删后插（任何分区里的旧行一并删），只对第 7 条判定为「变了」的行做——等价于一次 UPDATE，不违反 DD-78。WAITBEGIN 相差超过 24 小时则按第 9 条进待澄清、不写 |
| 9 | **24 小时防线**（Mac 侧 2026-10-13 定：同键两次观测 WAITBEGIN 相差 >24h 视为键可能重复，进待澄清、不写 core）——**dbt 对 `meta.clarification_queue` 没有写权限** | 0004 只授了 ingest | 新结构变更授 `oss911_transform` 对 `clarification_queue` 的 INSERT（同一值只开一条，同接入的做法）。`value_profile`、`reference_value` 的写入不在本步，暂不授 |
| 10 | **dbt 什么时候跑** | 任何文档都没写；DD-08 三档（准实时约 1 分钟 / 当日临时 / 日切封账）；LD-02「夜间构建」指 mart | 接在增量后面：`ingest_icp_cdr_voice` 增加第二个任务 `dbt build --select stg_icp_cdr_voice+ fact_call_leg`，进 pool `dbt_build`。即每 5 分钟一次，core 话单比 raw 晚一个周期以内。mart 的夜间构建另起 DAG，到时再定 |
| 11 | **core 分区只往前建了一个月**：补数早于此会因无分区失败；之后也没有任何东西按月预建 | 40-schema 初始窗口；DD-77「分区缺失属必须监控项」 | 每天一次的维护任务调 `meta.ensure_monthly_partitions` 往后建 3 个月（dbt 身份，已有 EXECUTE）；补数前由补数步骤先建它覆盖的月份 |
| 12 | **`source_tz_offset`**：ICP 不给偏移 | LD-02 第 6 章要求记录原始偏移；DD-60 / LD-01 6.3 的先例：「保留并在元数据中说明它是约定而非源端事实」 | 写入契约 `source_timezone` 的偏移（UTC 即 0），并在列注释与契约里写明「来自契约约定，非源端事实」。也可以留空——请定 |

### 二、拟（开发机的实现选择，无异议即按此）

| # | 选择 |
| --- | --- |
| 1 | dbt 项目放 `src/dbt/`，随发布目录部署（同 DAG 文件）；profile 沿用 30-python 生成的那份（`role: oss911_transform`，session 池）。**不引入任何 dbt 包**（dbt_utils 之类）：离网、许可、八年维护都多一件事，需要的几个宏自己写 |
| 2 | `stg_icp_cdr_voice`：增量，取「可处理（未隔离或已放行）且本模型未登记」的批次（DD-89）；post-hook 在同一事务内登记这些批次。每个原始观测一行，不在 staging 去掉版本——选哪一版是 core 的事 |
| 3 | staging 的类型化：空串 → NULL；时间 `to_timestamp(text, 'YYYY-MM-DD HH24:MI:SS')` 再按契约 `source_timezone` 解释为 timestamptz（读库内契约，不读会话时区）；格式不符的值接入时已整批隔离，放行的批次里若仍有，置空并计入「未映射」计数而不让模型失败 |
| 4 | `direction` 的呼入 / 呼出集合从库内契约 `definition` 读（LD-01 4.3），不写死在模型里 |
| 5 | `date_key` = wait_begin 在 Asia/Riyadh 的日历日（LD-02 第 6 章「日切以 00:00 AST 为界」）。**AS-12 / IR-35 仍是「拟」**：日切若改，date_key 要重算 |
| 6 | 通话键 `call_sk = core.sk(MKK, icp, core.nk(CALLID))`，腿键 `call_leg_sk = core.sk(MKK, icp, core.nk(CALLID, CALLIDNUM))`；`queue_code` 取 DEVICETYPE = 1 时的 DEVICENO（DDL 有此列、契约没有映射） |
| 7 | `correction_seq` 本层恒为 0（修正与 close_version 属 mart，LD-02 第 10 章）；`last_change_at` 只在内容真变时更新，`built_at` 每次写入时更新——两者的 DEFAULT 只对 INSERT 生效，写入时显式给值 |
| 8 | dbt 测试：sk 跨分区唯一（TC-DISC-13）、键非空、`direction` / `release_source` 取值、`is_last_leg` 与 `leg_seq` 一致、每个 CALLID 至多一个 -1；都随构建运行、失败即失败 |

### 三、下一步（fact_call）要先补的规则

`call_result_sk` 无派生规则；`call_type_code` / `direction` 取哪条腿；首腿或末腿缺失（进行中、或在待处理批次里）时怎么办；
`is_transferred` 的原因码没有归类表；M-C07 定义「按坐席腿逐条平均」而 source_tables 写的是 `fact_call`（其 talk_seconds 是按通话的合计），
两种平均不是一个数；M-C11 用坐席腿的 RELEASECAUSE，而文档说它只在末腿有意义（Q23）。这些在写 fact_call 前请定。

### 四、顺带发现的文档问题（供 Mac 侧顺手更正）

- ddl/02 文件头说每张 core 表都有 `ingested_at`，事实表 DDL 里没有
- LD-10 的 TC-DISC-04、TC-ING-01/02 与 `test/cases_seed.csv` 的编号含义不一致
- AS-33 引用的问题单编号应为 Q21（写成了 Q22）；CALLID 唯一范围是 Q20
- M-C11、M-C13 依赖 mapped_unverified 的列，却标 `depends_unverified: false`（与 DD-06、LD-03 不符）
- AS-32 说核实结果回写 if03a 的 `integration_checks` 段，该段不存在

## 2026-10-27  Mac 侧：dbt 层十二处需定——全部已定（DD-91、DD-92），八处拟全部同意

### 一、需定（编号同开发机那节）

| # | 决定 |
| --- | --- |
| 1 | `src_system = icp`，按系统不按源；CAD 用 `cad`，归档迁入用 `cad_archive`（与 cad 分开，割接并行期合并在 staging 做） |
| 2 | `MKK`，以结构变更写入 dim_site。**Gavin 已定**：name_en = `Makkah`、name_ar = `مكة المكرمة` |
| 3 | 同意：留空、不造成员；维度接入后同一模型重算填上 |
| 4 | 同意：两列 CHECK 加 `unmapped`；值表内非呼入呼出 → internal，值表外 → unmapped；M-03 计数 |
| 5 | 同意：进 if03a `enums.release_cause.classes`，升 v4，脚本从 CSV 生成并比对 |
| 6 | **本步留空**。算法已定为 HMAC-SHA256（DD-92）：密钥只存 /etc/oss911、不入库；规范化规则见 DD-92；core 留有明文列，可整体重算，故不急。密钥进 dbt 的路径随 LD-07 那批定 |
| 7 | 同意：加 `src_observed_at`（取 raw.ingested_at）；写入条件与「内容按 core 行算」照开发机建议 |
| 8 | 同意：按 sk 跨分区先删后插，只对变了的行；>24h 进待澄清 |
| 9 | 同意：授 transform 对 clarification_queue 的 INSERT |
| 10 | **改为独立 DAG** `build_icp_cdr_voice`：与增量同频 5 分钟、pool dbt_build、按 staging_batch_state 取可处理批次。不接在增量 DAG 后面——同一 DAG 的 max_active_runs=1 会让 dbt 失败重试堵住接入 |
| 11 | 同意：每日维护任务预建 3 个月；补数前先建；缺分区告警 |
| 12 | 写契约 `source_timezone` 的偏移，列注释注明「来自契约约定，非源端事实」 |

### 二、拟：八处全部同意。补一句第 6 条：`DEVICETYPE = 1` 即技能队列请对照 `3rd API/icp_device_type.csv` 再确认一次。

### 三、fact_call 的规则缺口：Mac 侧下一轮在 LD-03 与 metrics 里补（call_result 派生、direction / call_type 取首腿、
首末腿缺失时的处理、is_transferred 的原因码归类、M-C07 两种平均二选一、M-C11 改用末腿）。写完再开 fact_call。

### 四、文档问题五条
- ddl/02 文件头：开发机自己改（dev 文件）。
- LD-10 三条编号与 cases_seed 冲突：已改为 TC-DISC-19 / 20 / 21（DD-79 / 88 / 90 注与 LD-01、LD-02 引用同步改）。
- AS-33 → Q21，已改；CALLID 唯一范围是 Q20，NOTES 早先几处写 Q21 的以问题单为准，不改历史记录。
- M-C11、M-C13 的 `depends_unverified` 应为 true：请开发机改 metrics.yaml（口径文件在开发机，规则 DD-06 / LD-03 不变）。
- AS-32：已改为「if03a 须按 if03d 同一格式增设 integration_checks 段后回写」——请开发机在 if03a v4 一并加该段。


## 2026-10-27  开发机：DD-91 已实现——0006、if03a v4、dbt 项目（staging 与 fact_call_leg）、构建与维护 DAG

> 对上节「Mac 侧：dbt 层十二处需定——全部已定」的处理结果。

| 项 | 落实 |
| --- | --- |
| 结构变更 0006 | dim_site 写入 MKK（site_sk = `core.site_namespace('MKK')`，即该站点派生一切键的命名空间本身）；fact_call_leg 加 `src_observed_at`；四个 CHECK（两表的 direction、release_source / released_by）加 `unmapped`；source_tz_offset 列注释；授 transform 写 clarification_queue |
| if03a v4 | `enums.release_cause.classes`（37 / 25 / 367）由 `tools/icp_contract_enums.py` 从 CSV 生成，`--check` 比对；`integration_checks` 十二条；校验器认 classes 并列为必填 |
| metrics | M-C11、M-C13 的 `depends_unverified: true`（metric 的 version 未动：口径没变，变的是标记——若须随之升版本请告知） |
| dbt | `src/dbt/`：`stg_icp_cdr_voice`（按批次消费、类型化、方向与挂断方映射）、`fact_call_leg`（delete+insert，只写变了的行）；不引入任何包 |
| DAG | 独立的 `build_icp_cdr_voice`（同频 5 分钟、pool dbt_build）与 `maintenance_partitions`（每日，预建 3 个月）；新文件 `src/dags/oss911_build.py` |
| 部署 | dbt 项目进发布目录，50 装到 `/opt/oss911/dbt`（与 DAG 一样：只装发布内的文件、多余的删）；发布目录对 `src/dbt` 做「漏列即失败」的核对 |

### 临时库实测（开发机，假接口数据）

首次构建 18 项测试全过：raw = staging = core 2632 行。之后逐条验证 DD-91：

| 场景 | 结果 |
| --- | --- |
| 没有新数据 | 两个模型都 `INSERT 0 0` |
| 下一次增量（回看重叠） | 只写 17 条新腿；原 2632 行 xmin 一个都没变（DD-78） |
| 清掉 core 的登记、全部重算 | 写入 0 行：派生内容没变就不写 |
| 更新的观测、内容变了 | 改写 |
| 更旧的观测（迟放行） | 不写，新数据保留（DD-82） |
| 被隔离的批次 | 跳过；放行后下一次构建自动补入 |
| date_key 跨月（10-31 23:30 AST → 11-01 00:30） | 行移到 `fact_call_leg_p202611`（分区由 pre-hook 建出），旧分区不留副本 |
| 同键 WAITBEGIN 相差 2 天 | 不写 core；一条待澄清；再来一次不重复开 |
| 分区维护 | 首次建 52 个，重跑 0 个 |

### 开发机的三处处理，请知悉

1. **ddl/02 的文件头没改**：它属于已执行的变更 0001，改了校验和即失败（DD-73）。更正写在 0006 的文件头里
2. **挂断方只看末腿**：非末腿的 `release_source` 为 NULL（不适用），不计入 unmapped——否则每通电话的非末腿都会被算成「未映射」，
   把真正的未映射淹没。与 Mac 侧下一轮「M-C11 改用末腿」一致
3. **core 怎么知道 staging 有新数据**：core 模型也用 `meta.staging_batch_state` 登记（model = `fact_call_leg`）。它的登记在 post-hook
   里按「staging 中本模型尚未登记的批次」写，这只在 dbt 不并发时精确——pool `dbt_build` 一槽保证了这一点（DD-85），已写在宏的注释里

### 未做

- fact_call（等 Mac 侧补 LD-03 与 metrics 的规则）
- caller_number_hash（DD-92，密钥进 dbt 的路径随 LD-07 那批定）
- `value_profile`、`reference_value`、mapping_registry 逐列登记的写入方

## 2026-10-27  开发机：dbt 在真机上首次构建失败——pgBouncer 库别名与真实库名不同；须改 DD-67 的池命名，请 Mac 侧定

**现象**（Gavin 真机，runbook/13 第一次构建）：

```
Database Error in model stg_icp_cdr_voice
  cross-database references are not implemented: "oss911_etl.staging.stg_icp_cdr_voice"
```

**原因**：dbt 的 profile 连 `oss911_etl`——pgBouncer 上 session 池的**别名**，进了 PostgreSQL 是 `oss911` 库（DD-67「按库名选池模式」）。
dbt 生成 SQL 时把每个表写成三段式 `库名.schema.表名`，库名取 profile 的 `dbname`；PostgreSQL 见到 `oss911_etl.…` 与当前库 `oss911`
不同，当作跨库引用拒绝。**开发机的临时库测试直连真实库名，没经过 pgBouncer 别名，所以没测出来**——测试的连接路径与真实部署不同
（CLAUDE.md 第 5 节第二条）。已在开发机起一个本地 pgBouncer、别名 `oss911_etl` 指向 `oss911`，原样复现。

**dbt 内部绕不过去**（已逐一试过）：dbt-postgres 没有「渲染用库名」与「连接用库名」之分；它还校验每个关系的库名必须等于 profile 的
`dbname`（`verify_database`），所以把库名覆盖成 `oss911` 会被 dbt 自己拒绝，覆盖成空则 dbt 报错。**唯一的办法是 dbt 连接时用的库名就是
真实库名。** 本地 pgBouncer 上把 session 池改名为 `oss911` 后，同一项目 18 项全过，且仍经 pgBouncer 连接。

**须定：pgBouncer 的池怎么命名**（DD-67 · LD-08 第 3 章）

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A（开发机建议） | 两个池互换名字：session 池叫 `oss911`（= 真实库名），transaction 池改叫 `oss911_txn` | 仍按库名选池模式，DD-67 的做法不变、只改名字。现在用 transaction 池的只有 21 的探测用户；要改 21 的模板与 inventory、`ingest.conf`、dbt profile、40 的登录核对、runbook 05 / 08；重跑 21 / 30 / 40，pgBouncer 重启一次。服务层（尚未建）将来连 `oss911_txn` |
| B | 只留一个名字 `oss911`，默认 transaction；在 pgBouncer 的 `[users]` 段把 dbt、ingest 等登录用户设为 session | 改动少，但池模式从「按库名」变成「按登录用户」——DD-67 做法的改变；以后每加一个登录用户都要记得设模式，忘了不报错、只是偶发失败 |
| C | dbt 绕过 pgBouncer 直连 HAProxy | 违反 DD-67「应用一律经连接池」，dbt 的连接数不再受 pgBouncer 全局上限约束 |

另一点（不论选哪个）：`ingest` 的 psycopg 连接没有这个问题（不生成三段式名字），但若选 A，`ingest.conf` 的库名随之改为 `oss911`，
免得两个接入组件各用一个名字。

真机现状：0005、0006 已执行，dbt 项目已部署；构建在 staging 第一步失败、未写入任何数据（事务回滚）。`build_icp_cdr_voice` 处于暂停，
不要开启，等这件事定了再说。

> **Mac 侧处理结果（2026-10-27）：选 A，加 HBA 护栏（DD-93）**
> - 会话池改名 `oss911`，事务池 `oss911_txn`；DD-67「按库名选池模式」不变。
> - **护栏**：pgBouncer `auth_type = hba`，HBA 按池 × 用户拒绝错配——`oss911_query_svc` / `oss911_admin_svc` 连 `oss911` → reject；
>   dbt、ingest 的登录用户连 `oss911_txn` → reject。连错池立刻失败。HBA 文件随登录用户增减由剧本生成。
> - `ingest.conf` 库名改 `oss911`；21 / 30 / 40、inventory、runbook 05 / 08 / 13 随改；pgBouncer 重启一次。`cad_mirror` 不动。
> - troubleshooting 请加一条（第 33）；CLAUDE.md 第 5 节第二条已加这个实例。改完再开 `build_icp_cdr_voice`。


## 2026-10-27  开发机：DD-93 已实现（池改名、HBA 护栏）；troubleshooting 第 33 条

| 项 | 落实 |
| --- | --- |
| 改名 | pgbouncer.ini：`oss911` = 会话池、`oss911_txn` = 事务池；dbt profile、`ingest.conf`（inventory `ingest_db_name`）、40 的建分区与登录核对改连 `oss911` |
| HBA 护栏 | `auth_type = hba`，新模板 `infra/files/pgbouncer_hba.conf.j2`，由 21 按 inventory `db_logins` 生成、变了即 reload：会话池用户（角色 oss911_ingest / oss911_transform 的登录）连 `oss911_txn` → reject；服务层用户（oss911_query_svc / admin_svc 的登录，现在还没有）连 `oss911` → reject；**另加一条**：`cad_mirror_writer` 连 `cad_mirror` 以外的池一律 reject（与库级收回 CONNECT 是同一个意思，多一层）。pgBouncer 的 HBA 只认登录名、不认角色成员，所以按 db_logins 的 role 展开成登录名 |
| 以真实身份验证 | 40 的每个会话池登录用户再连一次 `oss911_txn`，断言 `login rejected` |
| 文档 | runbook 05 / 08 / 13 改名；troubleshooting 第 28 条旧代码块加注；新增第 33 条 |

**开发机实测**（本地 pgBouncer 1.22，与真机同版本；`auth_query` 与 SCRAM 照 21 的做法）：dbt、ingest、探测用户连 `oss911` 通过；
dbt、ingest 连 `oss911_txn` → `login rejected`；写入账号连 `oss911` / `oss911_txn` → `login rejected`；错口令仍是 SCRAM 失败。

**写模板时出过一个缺陷，已修**：第一版用 `{%- set %}` 去掉空行，按 Ansible 的 `trim_blocks` 渲染后，第一条 reject 规则被粘到了
上一行注释的末尾——**整条规则成了注释，护栏静默失效**，而 `--syntax-check` 照样通过。按 Ansible 的渲染方式实际渲染一遍才看出来
（CLAUDE.md 第 5 节第三条：语法正确、语义错位，要用解析器复核）。现在 set 放在文件最前，渲染结果逐行核过：三条规则。

CLAUDE.md 第 5 节「29 条」之类的条数：troubleshooting 现在 33 条，请 Mac 侧顺手改。

## 2026-10-27  开发机：第 2 批下一步 if03d 参照数据（坐席、技能队列等维度）的实现方案，请 Mac 侧确认

fact_call 等 Mac 侧补 LD-03 规则期间，ICP 线先做 if03d：`fact_call_leg` 的 `agent_sk` / `queue_sk` 等它，if03b / c 的坐席类事实也等它，
它又不依赖 fact_call 的规则。把契约 if03d、LD-01 4.6 与第 9 章、LD-02 第 4、7、9 章、DD-12 / DD-56、ddl/02 与 ddl/04 的相关表、
以及接口文档 synwas 一组（PDF p.907–933、p.1029–1033，逐页读完）对过一遍，**有十四处须先定**；其中几处是契约与接口文档对不上。
开发机不替设计做这些决定。

### 〇、接口文档里与契约不同的事实（先读这一段，后面的需定都从这里来）

| # | 接口文档原文所示 | 契约现在的写法 |
| --- | --- | --- |
| a | **版本号不是按数据集的**：`versions` 返回一个 CTI 级版本（`ccId` 一个）加每个 VDN 一个版本（`vdnsVersion[]`），VDN 版本**一次覆盖该 VDN 的全部七个数据集**；另带 `startTime` / `endTime`（毫秒时间戳）、`status`（0 初始 / 1 进行中 / 2 成功 / 3 失败）、执行同步的网关集群 `clusterId` | `watermark: {type: sequence, column: config_version}`，一个水位 |
| b | **GET 读的是网关自己的副本**，不是实时读 WAS：先要有一次 CTI / VDN 同步（`POST synwas/syncti`、`synvdn`），GET 返回上次成功同步的结果。这两个 POST **是写动作**（删 VDN 时网关会连带删其坐席、技能等），文档没说网关会不会自己定时同步 | 未提 |
| c | 所有 GET 都是**全量**，无分页、无增量、无按版本取；表里没有任何时间或版本列 | 与「版本变了才拉全量」相符 |
| d | `agents` 每行：`agentNo`、`agentName`（**单一名称**，样例里可为空串、可等于工号）、`groupId`（**一个**坐席组）、`skillId` 与 `skillGroupId`（**分号分隔的多值串**，样例可为 null）、`roleId`（分号分隔：2 配置管理员 / 3 质检员 / 4 普通坐席 / 5 报表管理员——**是 CMS 的权限角色，不是接警员 / 调度员 / 主管**） | `dim_agent.role_code NOT NULL`，取值注释为 call_taker · dispatcher · supervisor |
| e | `groups`：`groupId`、`groupName`、`monitorId`（组长工号） | 契约写 target `core.dim_agent`，未说落哪列 |
| f | `skills`：`skillId`、`skillName`（单一名称）、`realFlag`（1 实队列 / 0 虚队列）、`serverId`（业务类型 ID）、`mediaType`（1–20，5 = 普通语音） | `dim_queue.name_ar` / `name_en` 均 NOT NULL |
| g | `skillgroups`：`skillGroupId`、`skillGroupName`、`skillIds`（分号分隔）——**技能与技能组是多对多** | `dim_queue.skill_group_code` 是单值列 |
| h | `calledconfigs`：`accessCode`、`subCode`、`deviceType`、`deviceNo`、`deviceName`、`clientLevel`——接入码路由到哪个设备（技能队列、IVR 等） | target `core.dim_channel`；而 dim_channel 是**警情来源渠道**（phone · patrol · sms…，带 has_call） |
| i | 文档类型与样例不符：`ccId` / `vdnId` 标 int 而样例是字符串；`startTime` 标 string 而样例是数字；`versions` 的 `result` 标 array 而样例是对象 | — |

顺带回答 Mac 侧 10-27 节第二段的确认请求：**`DEVICETYPE = 1` 是技能队列**（PDF p.1033 设备类型附录，与 `3rd API/icp_device_type.csv` 一致）。

### 一、需定

| # | 问题 | 依据 / 现状 | 开发机建议 |
| --- | --- | --- | --- |
| 1 | **raw 表**：契约写 `target_raw: raw.icp_reference`，没有对应结构变更；而 LD-01 9.3、DD-56、`audit.reference_data_change_log` 的表注释都写「参照数据不经 raw 层」 | 后者说的是 M-10 的人工维护（译名、班次窗口），不是 ICP 同步来的配置 | 同步来的参照数据**经 raw**（契约为准），新结构变更建 `raw.icp_reference`；请把 9.3 那句收窄为「M-10 人工维护的参照数据不经 raw」 |
| 2 | **raw 的列形**：八个数据集八种结构，LD-02 第 9 章要求「与源逐字一致」 | 一源一表 | 通用列：`dataset`、`src_natural_key`、`payload jsonb`（原样）、`vdn_version`，加标准接入元数据；不按数据集展开列——八种结构展开成宽表大半为空，且新增字段要改表 |
| 3 | **raw 写快照还是写变化**：契约 `append_if_changed`。但只写变了的行，core 无法从 raw 区分「这个坐席没变」与「这个坐席消失了」，而「消失即置非当前」是契约要求 | 版本号只在同步后变；数据量小（坐席数百） | **版本变了就整份快照写入**（每个数据集一个批次，批次即快照）；core 以「最新快照里有没有」判断消失。去重仍在：同一版本重取（例如重跑）不再写。契约 `raw_write.mode` 改为 `snapshot_on_version` |
| 4 | **以哪个版本为准**（〇-a） | 单一 VDN（AS-30，Q31 待书面确认） | 数据集 `vdns` 用 CTI 版本，其余七个用该 VDN 的版本；水位改为按（ccId, vdnId）各记一个。只在 `status = 2`（成功）时取数；进行中或失败本次跳过并记 run_log |
| 5 | **网关的副本谁来刷新**（〇-b） | 若网关不自动同步，GET 永远是旧副本，版本号也永远不变——看起来像「配置没变」 | **本方不调 POST 同步**（写动作、会删数据，不属只读接入）；新增问题单一项问 ICP（见第三节）。在答复之前，`versions` 的 `endTime` 超过 N 天未变即告警，N 请定（建议 7） |
| 6 | **缓变维的 valid_from 取什么**：源端不给生效时间 | `core.sk_scd` 把 valid_from 纳入键 | 取该版本同步的 `endTime`（网关上配置生效的时刻，源端事实）；取不到时用批次落地时间，并在列注释写明。**同一实体的第一版 valid_from = `-infinity`**：否则 10-19 以来、第一份快照之前的话单都关联不到坐席。代价：「第一版」覆盖它被观测之前的全部历史，是推断——请定是否接受 |
| 7 | **`dim_agent.role_code` NOT NULL 无来源**（〇-d） | ICP 的 roleId 是 CMS 权限角色；接警员 / 调度员 / 主管是业务角色，ICP 不知道 | 不能一律写 `call_taker`（主管、调度员也可能有 ICP 工号）。建议 `unmapped`（DD-06），待身份系统或 M-10 提供业务角色后改；roleId 原样留在 raw。请定来源 |
| 8 | **`dim_agent.team_code`（班组）是否取 ICP 坐席组 `groupId`** | 坐席组是管理分组、带组长 `monitorId`；DDL 注释「班组」 | 取 `groupId`；组名进 `meta.reference_value`（domain `agent_group`）；`monitorId` 暂不落 core（权限另走身份系统，DD-41） |
| 9 | **`dim_queue` 双语名 NOT NULL，ICP 只给一个 `skillName`**（〇-f）；LD-02 第 7 章：「强制这件事在数据进入 core 之前完成」 | 若严格执行，新队列在 M-10 补译名之前进不了 core，`queue_sk` 一直空 | 两列先写源端原文，译名经 `meta.label_override` 覆盖、展现层取用（DD-56 已定「回落显示源端原文」，与此一致）。请定是否改 LD-02 第 7 章那句 |
| 10 | **技能与技能组多对多**（〇-g），`skill_group_code` 单值 | — | `skill_group_code` 留空；技能组名进 `meta.reference_value`（domain `skill_group`）；成员关系暂只在 raw。报表若要按技能组汇总，另立桥表——等有指标要它再说 |
| 11 | **被叫配置落哪**（〇-h）：dim_channel 是警情来源渠道，接入码路由配置不是 | 写进 dim_channel 会把「911 接入码」和「巡逻上报」这类渠道混在一张维度里 | 不进 dim_channel；本步只落 raw。它的用处（例如按接入码区分话务入口）等指标需要时再定落点。请定 |
| 12 | **谁写 core 维度与留痕** | 维度属主 `oss911_owner`；dbt 现有权限只够写事实 | dbt 写（同 fact_call_leg 的做法：自定义宏，SCD2 关闭旧版 = UPDATE valid_to）。新结构变更授 `oss911_transform`：dim_agent / dim_queue 的 INSERT 与 UPDATE(valid_to, built_at)；`meta.reference_value` 的 INSERT 与 UPDATE；`audit.reference_data_change_log` 的 INSERT（changed_by = `icp_sync`）；`meta.label_override` 的 UPDATE(state, flagged_at)（源端改名即置 pending_reconfirm，DD-56） |
| 13 | **版本更小、版本变了但内容无差异** | 契约已写：前者进待澄清并告警，后者记运行日志并告警 | 照契约。前者由接入程序判（它最先看到版本）；后者由 dbt 判（比对在 core 侧） |
| 14 | **`mediaType` 与虚队列** | 本期只用语音（dim_queue 注释）；`realFlag = 0` 是虚队列 | 全部技能入 dim_queue，不按媒体类型过滤（注释已定保留）；`realFlag` 暂只在 raw，dim_queue 无此列，需要时再加 |

### 二、拟（开发机的实现选择，无异议即按此）

| # | 选择 |
| --- | --- |
| 1 | 接入程序加数据集式的 rest_api 取数路径：一次运行先取 `versions`，按第 4 条判定后，逐数据集取全量、各成一批（批次级隔离仍成立：一个数据集坏了只扣那一个） |
| 2 | DAG `ingest_icp_reference`，契约 `*/30`，进 pool `icp_cms`（与话单同一平台、同一服务账号，Q03 未答之前不并发）；不建回扫 DAG（快照无需回扫） |
| 3 | 假 ICP 接口加 synwas 九个 GET（按 PDF 的字段与类型不符之处原样模仿，包括字符串型 ccId），控制端点可「升版本、改名、删坐席、版本倒退、同步失败」 |
| 4 | dbt：`stg_icp_reference`（按批次消费、拆数据集、分号多值拆开）、`dim_agent`、`dim_queue`、`reference_value` 写入；之后 `fact_call_leg` 按 device_no / queue_code 与通话时刻关联当时那一版——按 DD-91 第 7 条属「派生内容变了」，历史行会被改写一次 |
| 5 | `agentName` 为空串或等于工号时照写原文，不当作缺失补值 |

### 三、拟新增 ICP 问题单（请 Mac 侧并入 `queries/ICP_CMS_Interface_Clarification_Questions.xlsx`）

| 拟编号 | 问题 | 为什么 |
| --- | --- | --- |
| Q33 | synwas 的 GET 读的是网关副本。平台是否会定时自动执行 CTI / VDN 同步？周期多长？若不会，谁在什么时候触发？本方是否应调用 `synvdn`（我方倾向不调，它是写动作） | 决定参照数据会不会无声地停在旧版本 |
| Q34 | `versions` 的 VDN 版本是否只在同步**成功**时递增？内容没变的同步是否也会递增？ | 契约「版本变了但内容无差异即告警」依赖这一点；若每次同步都递增，此告警每次都会响 |
| Q35 | 坐席的业务角色（接警员 / 调度员 / 主管）在 ICP 里是否有任何字段体现？`roleId` 只是 CMS 权限角色 | 需定第 7 条 |

Q30（config_version 是否单调、是否归零）已在单上，答复同样适用于 VDN 版本。

### 四、顺带发现的文档问题

- LD-01 9.3 与 `audit.reference_data_change_log` 表注释「参照数据不经 raw 层」与契约 if03d 冲突（需定第 1 条）
- 契约 if03d 的 `version_probe.path` 写作 `/cmsgateway/resource/synwas/versions`，LD-01 4.6 写作 `GET /synwas/versions`（缺前缀）

## 2026-10-27  Mac 侧：if03d 十四处全部已定（DD-94），五处拟同意，Q33–Q35 已入问题单

| # | 决定 |
| --- | --- |
| 1 | 经 raw，新结构变更建 `raw.icp_reference`；LD-01 9.3 已收窄为「M-10 人工维护的不经 raw」，`reference_data_change_log` 表注释请开发机同改 |
| 2 | 同意通用列（dataset、src_natural_key、payload、vdn_version + 接入元数据） |
| 3 | 同意整份快照、每数据集一批、同版本重取不写；契约 `raw_write.mode: snapshot_on_version`（DD-82 已加注） |
| 4 | 同意：vdns 用 CTI 版本、其余用 VDN 版本，水位按 (ccId, vdnId)，只在 status = 2 取 |
| 5 | 不调 POST；**N = 7 天**，作热调参数（白名单加 `version_stale_days`） |
| 6 | 同意 valid_from 取 endTime；**首版用哨兵，接受这个推断**——哨兵值由开发机定一个 `sk_scd` 能表示的（`-infinity` 进 to_char 要实测），列注释写明 |
| 7 | **改**：ICP 工号只有接警员与其主管（Gavin，AS-36），故 role_code 默认 `call_taker`，主管由 M-10 标 `supervisor`；调度员的维度行将来自 CAD 上下岗记录。Q35 仍问但不阻塞 |
| 8 | 同意 groupId → team_code；组名进 reference_value；monitorId 不落 |
| 9 | 同意先写原文、译名走 label_override；LD-02 第 7 章已补这条 |
| 10 | 同意留空、关系只在 raw、需要时立桥表 |
| 11 | 同意只落 raw；契约 if03d 的 calledconfigs target 改为 none / raw-only |
| 12 | 同意，按所列权限新结构变更 |
| 13 | 同意 |
| 14 | 同意 |

拟五处全部同意。文档：LD-01 4.6 已补版本 / 副本 / 快照四条并改路径前缀；Q33–Q35 已加进 `queries/ICP_CMS_Interface_Clarification_Questions.xlsx`
（共 35 题）。契约 if03d 的改动（mode、版本探测、targets）由开发机做，升 v2。troubleshooting 条数 CLAUDE.md 已改 33。


## 2026-10-27  开发机：DD-94 已实现（if03d 参照数据：接入、0007、dbt 维度）；实现中定的十四处，请 Mac 侧知悉或登记

> 对上节「Mac 侧：if03d 十四处全部已定（DD-94）」的处理结果。提交 0d200c5（假接口 synwas）、73eba10（0007）、
> 0aec2c0（接入与契约 v2）、1f7a8a2（dbt 与 runbook 14）。**未上真机**，步骤见 `infra/runbook/14-reference.md`。

### 临时库实测（假接口数据）

| 场景 | 结果 |
| --- | --- |
| 首次取数 | 8 批（vdns 用 CTI 版本、其余七个用 VDN 版本），77 条；再跑 0 批 |
| 首次构建 | 60 个坐席、4 个队列（含虚队列）、10 个小码值；core 里已有的 823 条坐席腿、854 条队列腿全部关联上；再跑四个模型 `INSERT 0 0` |
| 坐席改名、删一个坐席 | 改名者两版（旧版关于同步 endTime）、删除者关版不删行；逐字段留痕；已确认的译名转 `pending_reconfirm` |
| M-10 标主管（set_at 09:00） | 新版从 09:00 起；09:00 之后的 4 条腿改关联新版，之前的 6 条不动；撤销标记再生成一版 |
| 版本倒退 | 不取、不应用，待澄清一条（按 VDN 一条，不是每数据集一条） |
| 同步进行中 / 副本 26 天未刷新 | 不取 / 照取，run_log message 以 `STALE:` 开头 |
| 快照内工号重复 | 整批隔离；同版本不再重取；更新的版本应用后再放行它，只登记不应用 |
| 只升版本、内容不变 | dbt 测试 WARN（Q34），下一个真变化的版本到来后消失 |
| 关掉重新关联（变异） | 新测试 `fact_call_leg_dimension_links` 失败 7 条；修复操作 `relink_all_call_legs` 后通过 |
| 话单模型重算全部腿 | 写入 0 行：话单模型自己的维度关联与重新关联的结果一致 |

权限验证增至 141 项。

### 实现中定的（DD-94 没写到，开发机定了；有异议请提）

| # | 定了什么 | 为什么 |
| --- | --- | --- |
| 1 | 已取到哪一版记在 `meta.ingest_batch` 新列 `snapshot_scope` / `snapshot_version` / `snapshot_synced_at`，**不写 `watermark_state`** | 后者一源一行，装不下「每个范围一个版本」；批次本来就是快照 |
| 2 | 判断「已取」时**隔离中的批次也算** | 否则一份被扣下的快照每 30 分钟被重取、重新隔离一次 |
| 3 | 快照内主键重复、或快照突然变空而上一份不空：整批隔离；主键缺失的记录照写、不隔离 | 照单应用一份空快照会把全部坐席置为非当前 |
| 4 | `versions` 列出多于一个 VDN：照常接入，开待澄清 | raw 记源端给了什么；AS-30 不再成立须有人知道 |
| 5 | 副本过期只写进 run_log message（`STALE:`），任务仍成功 | 还没有告警通道；监控那一批把它接成告警。让任务失败会每 30 分钟红一次而数据并没错 |
| 6 | 首版哨兵 **`1970-01-01 00:00 UTC`**，不是 `-infinity` | 实测：`core.sk_scd()` 用 `to_char` 格式化 valid_from，`-infinity` 得 NULL，键为 NULL |
| 7 | 新表 **`meta.agent_role_override`**（M-10 写，dbt 读）存主管标记，不直接写进 dim_agent；撤销标记即删行，新版从 now() 起 | 维度由 dbt 按源重建，写进去的值下一版就丢了 |
| 8 | `meta.reference_value` 增列 **`src_label`**（源端原文，dbt 写）；`last_seen_at` 改为「最近一次变化的时刻」，不是每次看到都更新 | 原表只有两列译名（M-10 维护），源端原文没处放；每次看到都更新会让每个版本把全部码改写一遍（DD-78） |
| 9 | 每次构建只应用一份快照、从旧到新 | 两次构建之间来了 v14、v15，只应用 v15 会丢掉 v14 那一版历史 |
| 10 | 已有话单的关联由**维度模型的 post-hook**重新关联（只限本次变化涉及的坐席 / 队列、只写真变的行），不靠清登记让话单模型重算 | 维度变一次就重算全部话单代价太大；新测试守住两者一致，另有手工修复操作 |
| 11 | dim_agent 的两列名称也先写源端原文（DD-94 第八条原只说 dim_queue） | 同一个理由：ICP 只给一个名字、语种不明 |
| 12 | dbt 项目覆盖 `postgres__get_columns_in_relation`，跳过生成列 | dim 表的 `is_current` 是生成列，dbt 增量写入会往里插值而被拒；照抄 dbt-postgres 原实现，只加一个条件 |
| 13 | 「版本变而内容无差异」做成 dbt 的 warn 级测试，不写 run_log | 比对要看全部数据集的内容，在 staging 上一条查询即可；warn 出现在构建输出里 |
| 14 | 授 `oss911_transform` 对 schema `audit` 的 USAGE | DD-94 第十一条授了 `reference_data_change_log` 的 INSERT，没有 schema 的 USAGE 用不了 |

另：`version_stale_days` 的热调**可升可降**（只是早晚报告，不丢数据），与 lookback「只许升」不同。

### 请 Mac 侧顺手更正

- DD-94「被拒绝方案」第五条的理由写「主管与调度员也有 ICP 工号」，与同日更正后的第六条、AS-36（ICP 工号只有接警员与其主管）矛盾
- `audit.reference_data_change_log` 表注释已在 0007 改为「M-10 人工维护 + dbt 应用 ICP 同步变化（changed_by = icp_sync）」；
  LD-01 9.3、DD-56 里「参照数据不经 raw 层，此表是唯一追溯来源」宜同步收窄为「人工维护的」
- runbook 新增第 14 步；CLAUDE.md 第 9 节「当前进展」可加一行

## 2026-10-28  开发机：话单的坐席或队列在配置里已不存在——关联为空，指标里怎么算请 Mac 侧定

> 真机实测发现。提交见本节末。

### 现象

在假接口上删除坐席 102（网关同步 2026-10-27 18:38:37 +03）之后，话单里仍有 102 的坐席腿：至 10-28 20:51 共 51 条，
`agent_sk` 为空。假接口的话单生成器不看配置，所以一直派话单给 102。

### 现在的行为（开发机认为是对的）

- `dim_agent` 里 102 的版本关于同步时刻；之后开始的腿找不到「通话开始时有效的版本」，关联为空。**不把已关闭的版本拉长**
  去覆盖它：那等于替源端假设「这个坐席其实还在」。
- 已有测试 `fact_call_leg_dimension_links` 看不出这种情况：腿与维度一致地为空。
- 新增 WARN 级测试 `fact_call_leg_without_dimension`，按工号列出条数与首末时刻；维度为空时不报（否则 if03d 之前写入的腿全部被列出）。

### 真实环境里什么时候会出现

坐席或队列已从 ICP 配置删除，但删除前开始的通话迟到；配置删早了；或者网关副本比话单更新得快、慢不一致（Q33）。

### 请定

1. 这类腿在指标里怎么算：按「未知坐席 / 未知队列」计入总量，还是另列、还是从按坐席分组的指标里剔除。DD-06 只说未映射的码值不得归入「其他」，这里是**维度成员缺失**，不是码值未映射，不确定是否同一条
2. 要不要进待澄清队列（一个工号一条），还是 WARN 足够
3. 指标口径定下来之前，开发机不在 fact_call_leg 里加任何「未知坐席」哨兵行

## 2026-10-28  Mac 侧：if03d 评审三条全部采纳（DD-94 十五至十七）

1. `snapshot.py`：声明了自然键的快照型数据集，存在任一缺键记录 → 该数据集整批隔离（原因写明「snapshot incomplete: N rows without key」），
   raw 照常写入；全部缺键同样。隔离批次不参与「消失即置非当前」。
2. M-10 主管标记改为变更记录表 `meta.agent_role_override`（agent_no、role_code、valid_from、valid_to、changed_by、reason；
   撤销 = 关 valid_to），放进下一个结构变更；`reference_models.sql` 按「源端快照变化点 ∪ 角色标记变化点」切版本，
   每个变化点一版。开发期可先由 SQL 维护该表，M-10 做好后接上。
3. `stg_icp_reference` 只处理契约配置的 `(ccId, vdnId)`；其他 VDN 的快照留 raw + 待澄清（接入已做），不进 dim。
已记 DD-94 注十五至十七、LD-02 第 7 章第三条规则。

## 2026-10-28  Mac 侧：维度成员缺失的腿怎么算（DD-95）；十四处知悉

**DD-95**：
1. 总量照计；按坐席 / 队列分组归显式 **`unlinked`** 桶（mart 按 `agent_sk IS NULL` 归入，保留原始工号可下钻），不是「其他」、不剔除。
2. **进待澄清**：每个工号一条，未处理前不重复开（`clarification_queue` 已授 transform INSERT）。dbt WARN 测试保留。
3. 不造哨兵行、不拉长已关闭版本（与开发机一致）。成员重新出现 → post-hook 重新关联补上。

**十四处**：全部知悉，记入 DD-94 注。三处以昨天评审答复为准：第 3 条缺键 → 整批隔离（十五）；第 7 条 `agent_role_override` →
带 valid_from / valid_to，撤销关区间不删行、按变化点并集切版本（十六）；第 4 条多 VDN → staging 只处理配置的 VDN（十七）。
其余十一处同意，哨兵 1970-01-01 UTC 认可。三处更正已做：DD-94 被拒方案五理由、DD-56 收窄、CLAUDE.md 第 9 节。


## 2026-10-28  开发机：DD-94 十五至十七与 DD-95 已实现（0008、契约 v3、维度按变化点并集切版本、未关联待澄清）

> 对上两节「Mac 侧：if03d 评审三条」「Mac 侧：DD-95」的处理结果。提交见本节末。**未上真机**，步骤见 runbook 14 末节。

### 临时库实测（假接口）

| 场景 | 结果 |
| --- | --- |
| 首次装载、重跑 | 60 坐席 / 4 队列，话单全部关联；重跑四个模型 `INSERT 0 0`；全量构建 PASS=32 |
| 13:00 同步改名 + 14:00 起标主管，同一次构建应用 | 103 三版：旧名 → 13:00 新名接警员 → 14:00 新名主管（旧实现会合成 13:00 一版主管） |
| 标记撤销于 15:00 | 15:00 再切一版接警员，旧标记行留着 |
| 补登过去的标记（12:30，早于该坐席 13:00 的最新版） | 从 13:00:01 起生效；留痕 reason 写明标记原始起点、之前的版本不改写 |
| 起点在将来的标记 | 本次不生效；重跑 `INSERT 0 0` |
| 两份快照同时待应用（v17、v18） | 各成一版，各从自己的同步时刻起 |
| 契约 site_vdn 改成不存在的 VDN | 接入照常、两条待澄清（「VDN 1/1 不是本中心的」「VDN 1/2 不在版本号里」）；staging 不取、维度不动。改回后下一次构建补上 |
| 删除坐席 102 后补取话单 | 12 条坐席腿关联为空；待澄清一条（102，12 条），再构建不重复开；WARN 测试仍报 |
| 快照里有一条缺工号 | 该数据集整批隔离（`snapshot incomplete: 1 row(s) without agentNo`）；维度不动，其他数据集照常 |
| 全部腿重新关联 | 写入 0 行（维度模型的关联与重算一致） |

权限验证增至 147 项；0007 表里已有的标记搬进新表（valid_from = set_at，区间开放）。

### 实现中定的（DD 没写到，开发机定了；有异议请提）

| # | 定了什么 | 为什么 |
| --- | --- | --- |
| 1 | 维度一次构建**应用全部待应用的快照**（原为一次一份、旧到新）；`reference_value` 仍一次一份 | 变化点并集要求同一时间轴上同时看到所有快照与标记；一次一份时，积压的快照与其间录入的标记交错，仍会被合成或错位 |
| 2 | 起点早于该坐席最新版本的标记：从最新版本之后一秒起生效，留痕注明；**不改写已有版本** | 维度只增只关（0007）。补登过去的事要么改写历史，要么延后生效，选了后者。**请定：M-10 是否应拒绝这种标记，或提示「将从 X 起生效」** |
| 3 | 起点在将来的标记：到点之后的那次构建才切版 | 当前版本 valid_to = infinity 的含义不能被将来的版本打破 |
| 4 | `meta.agent_role_override` 在 Mac 侧列名之外保留 site_code、src_system，加 override_id、recorded_at（录入时刻，区别于生效时刻）；同一坐席区间不重叠用排他约束 | 排他约束要扩展 `btree_gist`（PostgreSQL contrib，随 postgresql-16 包，不是新组件），由 20-database 按 inventory 建：owner 角色不能建扩展。真机须先跑 20 再跑 40 |
| 5 | M-10 只能 INSERT 与 UPDATE(valid_to)；不能改角色、起点、删行 | 「撤销 = 关 valid_to」之外的改法都会改写已生效的判断 |
| 6 | 标记带来的新版本，留痕 changed_by 仍为 `icp_sync`，reason 写「(role mark)」 | 标记本身的留痕归 M-10 路径；这里记的是维度的变化 |
| 7 | 契约 v3 增 `endpoint.site_vdn`（cc_id、vdn_id，开发值 "1"/"1"），必填；integration_checks 增 site_vdn（Q31） | 「契约配置的 VDN」原先没有落处。**开发值会随发布目录进生产，联调时必须核实** |
| 8 | 接入：原「VDN 多于一个」改为「出现 site_vdn 以外的 CTI / VDN」每个一条待澄清，另加「site_vdn 不在版本号里」 | 前者包含原判据；后者若不报，维度会安静地永远不变 |
| 9 | staging **不登记**其他 VDN 的批次 | 若登记为已处理，改正 site_vdn 之后这些批次再也不会被取 |
| 10 | DD-95 的待澄清：source_id `icp_reference`，`src_column` 为「agentNo / skillId of call legs, not in the configuration at their start (DD-95)」；由 fact_call_leg 与两个维度模型的 post-hook 开 | 根因在参照数据（要问 ICP 运维），不在话单；三处都可能产生空关联。经部分索引 (agent_sk / queue_sk, wait_begin) 只读空关联的腿 |
| 11 | mart 的 unlinked 桶尚未实现 | mart 还没有；随 fact_call / mart 一批做 |

## 2026-10-29  Mac 侧：对「DD-94 十五至十七与 DD-95 已实现」十一处的答复

| # | 答复 |
| --- | --- |
| 1 | 同意 |
| 2 | **M-10 不拒绝、不静默**：录入时显示「将从 X 起生效」（X = 该成员最新版本 + 1 秒）并要求确认；记录保留原始申请起点 `requested_from`。不改写已生效版本（维度只增只关 · DD-12 统计按当时） |
| 3 | 同意 |
| 4 | 同意；「真机先 20 后 40」写进 runbook 顺序说明 |
| 5 | 同意 |
| 6 | **改**：标记引起的版本，留痕 `changed_by` 取 `agent_role_override.changed_by`（M-10 操作人），`reason` 写 `(role mark)`；`icp_sync` 只用于源端驱动的版本。留痕要回答「谁造成了这次变化」 |
| 7 | 同意；开发值 1/1 随发布进生产 → 列入 LD-08 7.2 发布核对清单的必核项（Mac 侧补文档） |
| 8 | 同意 |
| 9 | 同意 |
| 10 | 同意 |
| 11 | 同意 |
已记 DD-94 注十八。


## 2026-10-29  开发机：DD-94 注十八已实现（结构变更 0009 · 留痕按字段记操作人）

对 Mac 侧「对 DD-94 十五至十七与 DD-95 已实现十一处的答复」的处理：

| 答复 | 处理 |
| --- | --- |
| 2 `requested_from` | 0009 加 `meta.agent_role_override.requested_from`（可空；`CHECK requested_from < valid_from`）。约定：**`valid_from` 写 M-10 确认后的 X，`requested_from` 写原来要的起点**；按时生效的标记留空。dbt 的「最新版本 + 1 秒」兜底保留：M-10 显示 X 之后、保存之前维度又多一版时仍会发生 |
| 4 先 20 后 40 | runbook 08「跑」一节开头写明；runbook 14 的 0008 节原已写 |
| 6 留痕 `changed_by` | 按**字段**分：`role_code` 那一行取标记的操作人、reason 带 `(role mark)`（补登时另带 `requested from …`）；名字、组等源端字段与 `is_current` 仍是 `icp_sync`。同一变化点上既有快照又有标记（例如同一次构建里改名与撤销），各字段各记各的来源 |

**实现中新增一处，请 Mac 侧知悉并登记：`cleared_by`。** 撤销标记只关 `valid_to`，表里原本不记是谁撤销的；按「留痕要回答谁造成了这次变化」，
撤销引起的版本不该记成当初设标记的人。0009 一并加 `cleared_by`（可空；只在 `valid_to` 非无穷时可填），M-10 获 `UPDATE(cleared_by)`。
撤销引起的版本，留痕取 `coalesce(cleared_by, changed_by)`——录入时就带结束时刻的标记，`changed_by` 同时代表设与撤。
被拒方案：另建撤销记录表（多一张表，只为一列）；撤销改成插一条 `call_taker` 标记（与「撤销 = 关 `valid_to`」的十六条冲突，且「回到默认」与「明确标为接警员」从此无法区分）。

**顺带修正 df471ad 的一处**：补登说明「mark valid from …, versions before are not rewritten」原先挂在变化点上，同一点上源端字段的留痕也带着它；
标记早已生效、只是后来又有改名时，改名那行会错误地写上这句。现在只写在 `role_code` 那一行。

临时库：alice 标记、carol 补登（带 `requested_from`）、bob 撤销与同一次构建里的源端改名、frank 补登到最新版本之前（兜底路径），留痕各记各人；
重跑写入 0 行；全量构建 PASS=32；权限验证 150 项（新增三项：`requested_from` 合法与不合法、开放区间不能填 `cleared_by`）。
真机步骤见 runbook 14 末节「0009」（40 → 45 → 50）。

> **Mac 侧（2026-10-29）**：`cleared_by` 同意并登记（DD-94 注），被拒方案的理由成立。下一步计划待 Gavin 定后写入 CLAUDE.md 第 10 节。

## 2026-10-29  Mac 侧：下一步计划已定（方案甲：ICP 垂直切片打穿到报表），开发机请开始第 7 步

计划全文在 CLAUDE.md 第 10 节。**第 7 步 if03b / if03c 可立即开始**：契约现成（if03b_icp_agent_session、if03c_icp_agent_day），
维度已有；raw 表与结构变更、staging、core 的 fact_agent_session / fact_agent_day 照 fact_call_leg 的做法。注意：
- 两个接口都有「平台报表批先跑完」的前置（契约 prerequisite），报表批时刻未答（ICP 问题单 Q27）——假接口按契约假设实现，联调核实项记进契约 integration_checks。
- if03c 的日累计会被平台重算改写（restate_days 3），core_version 取 observed_at（契约已写）。
- DD-65：坐席状态时长取平台指标是唯一例外，其余考核指标仍自算；if03c 的值与 if03b 区间合计、与话单按坐席腿计数对账（契约 note）。
- 新决策请照旧在 NOTES 列「需定」，不自定。
Mac 侧同时开始第 8 步的设计：LD-03 fact_call 规则与指标注册表落地形态，写完发 NOTES。

