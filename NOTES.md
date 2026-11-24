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

## 2026-10-30  Mac 侧：if03d 评审两条采纳（DD-94 十九、二十），请开发机实现

1. `snapshot.py`：「已取版本」继续含隔离批次；「空快照检查」的基线改为最近一份**可信**快照（未隔离、或 state = released 且已应用），
   连续空快照每份都与该基线比、每份都隔离。清空全部配置只能经 M-03 放行。补测试：v15 空隔离后 v16 空仍隔离。
2. dbt 维度构建：pre-hook `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ`；`scd2_timeline` 只算一次落临时表，留痕 / 关旧版 / 插新版
   三步都读它。M-10 写 `agent_role_override` 为独立短事务，不取构建锁；构建中提交的标记下次构建应用（写进 runbook 14 与 LD-06 M-10 节）。

## 2026-10-30  Mac 侧：第 8 步设计已定（DD-96、DD-97），fact_call 与话务 mart 可开工

规则全文：LD-03 2.4（fact_call 十条）与第 5 章补充（注册表与 mart）。给开发机的清单：

**结构变更**
1. `core.fact_call` 加 `is_complete boolean NOT NULL`；`dim_call_result` 取值改为 `answered · abandoned · unanswered_system · ivr_only`（删 transferred / malicious 两行；若 CHECK 约束，改之）。
2. `fact_call` 的 `released_by` CHECK 加 `unmapped`（与 fact_call_leg 一致，DD-91 第 4 条）。

**dbt：`core.fact_call`**（只写变了的行，同 fact_call_leg 的做法）
3. 按 CALLID 聚合；**首腿未到不建行**；末腿未到 `is_complete = false`。
4. 取法按 LD-03 2.4 第三至八条；`call_result` 按第九条优先级（先看末腿 release_source，再看有无坐席腿）。
5. 各腿 CALLTYPE / direction 不一致 → 待澄清一条（按 CALLID）。
6. 测试：每列可从腿重算（DD-66 表注释）；`is_complete` 与末腿存在一致；结果码四值互斥完备；`answered` 蕴含 `answered_leg_count ≥ 1`。

**metrics.yaml**（口径文件在开发机）
7. M-C07：source_tables 改 `core.fact_call_leg`，定义「每条接通的坐席腿一个样本」。
8. M-C11：grain call、aggregation ratio、source `core.fact_call`（released_by）。
9. M-C06：定义补「分母为全部有队列腿的通话（含放弃）」。
10. M-C15：实现说明「mart 内按 caller_number 分组，只输出计数」。
11. 每条已发布指标加 `mart_table` / `mart_column`；M-C05 加 `params: {x_seconds: 20}`。
12. 注册表同步步骤（与 45-contracts 同模式，建议 46-metrics）：校验 published 指标的 mart 列存在，否则失败。

**mart 第一组**
13. `mart.calls__day__volume`、`mart.calls__day_queue__service`、`mart.legs__day_agent__handling`、`mart.calls__day__release`；
    只算 `is_complete`；另给「进行中」计数；agent/queue 为空归 `unlinked`（DD-95 第十一条）；每行 `close_version`、`publication_seq`（DD-81，
    `meta.data_publication` 已有）。夜间构建 DAG 另起；当日临时数每小时一次。
14. `unanswered_system` 命名：Gavin 2026-10-30 定。


## 2026-10-30  开发机：DD-94 十九已实现；二十实现了「时间轴只算一次」，REPEATABLE READ 需定

**十九**（`snapshot.py`）：「取过哪个版本」仍含隔离批次；空快照的基线改为最近一份可信快照——未隔离，或 `state = released` 且已被某个
dbt 模型登记（`meta.staging_batch_state`，即「已应用」）。临时库：删光坐席后 v74、v75 两份空快照都按 60 条隔离；放行 v75 但未构建时，
v76 仍按 60 条隔离；构建应用 v75 后，v77 以 0 条为基线照常接受。单元测试 83 项。

**二十，已实现部分**：每个维度模型取锁后先把本次用的快照集合（`fix_snapshot_set`）与时间轴（`fix_timeline`）各算一次，落事务内临时表
（`ON COMMIT DROP`）；留痕、关旧版、插新版、**登记已应用批次**都读这两张表。`reference_value` 同样先固定四个数据集各自生效的快照。
构建中提交的标记或快照留给下一次构建（runbook 14 已写）。原来靠 `built_at = now()` 认出「本次已关闭的版本」的那段逻辑随之去掉。

顺带修正 df471ad 的一处缺陷：原先登记已应用批次时会**重新查询**待应用快照。构建期间如果有新快照完成 staging（例如手工构建与调度构建交错），
它会被登记为已应用而实际没应用，以后永远不会再被选中。现在只登记本次固定下来的那一份集合。

**需定：REPEATABLE READ 是否仍要。** 照原文实现会破坏话单与维度之间的关联锁。可重复读的快照取在事务内第一条查询开始时；
维度模型的第一条查询正是 `pg_advisory_xact_lock`，等锁期间（话单模型在写）快照已经取定，话单模型提交的新腿对本次构建不可见，
重新关联补不到它们，2026-10-27 真机上的那次竞态（6b5002f）又回来。临时库实测：会话 A 持锁，B 以 REPEATABLE READ 等锁，
C 在 B 等待期间插入并提交，A 放锁后 B 读到 0 行。

| 方案 | 代价 |
| --- | --- |
| **甲（开发机建议）**：不用 REPEATABLE READ，保持 READ COMMITTED + 事务级锁 + 两张临时表 | 时间轴与快照集合已固定，留痕、关闭、插入、登记读的是同一份；之后的重新关联在 READ COMMITTED 下反而能看到锁前提交的新腿，这正是它需要的 |
| 乙：锁改为在事务外取会话级锁（dbt 的 `transaction: false` 钩子），事务内再 `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` | 模型失败时 post-hook 不跑，会话锁一直挂在该线程的连接上，直到 dbt 进程结束；同一次运行里另一线程的模型等这把锁，而运行结束又要等它——挂死。要靠 `lock_timeout` 兜底，阈值又必须大于正常构建时长 |

请 Mac 侧在 DD-94 二十上定，开发机按定的改。

## 2026-10-30  开发机：第 7 步（if03b / if03c）开工前核对接口文档，九处需定

逐页核对 `hindex/agent`（接口文档 p.935–936）与坐席指标定义（p.1035）后，契约 if03b、if03c 有几处与文档不符或没写，
**第一条决定整个接入的形态**，其余是细节。开发机先做第 8 步，等这里定了再开工。

| # | 问题 | 文档原文 / 现状 | 开发机建议 |
| --- | --- | --- | --- |
| 1 | **取的是哪一天？** 请求体只有 ccId、vdnId、objectIds、indexIds，**没有日期参数**。同组的技能、IVR、VDN 指标按时间点返回（`{"2016-01-25 07:00:00": "1", ...}`），坐席指标只返回一个值 | 契约的「每小时重取当日」「restate_days 3」与问题单 Q27、Q28 都以「能按日期查」为前提，文档里没有这个能力 | 新增 ICP 问题（Q36）：返回的是哪一天（当日至今？最近一个报表批完成的那天？），按网关哪个时区切日，能否查指定日期。**若是「当日至今」**：① 回补不可能，restate_days 作废；② 某天的终值只能靠当天最后一次观测，每小时取一次会丢最后一小时——要在 23:5x 加取或缩短周期，且仍受报表批延迟所限，日终值可能永远拿不全；③ 话单对账（呼入数）会有系统性差。这会影响 M-R02–M-R04 能否承诺「日终定稿」。答复前开发机按「当日至今」实现假接口，契约 integration_checks 记一条 |
| 2 | 某一观测属于哪一天 | if03c 的幂等键是 `[agentId, date_key]`，但 date_key 不在响应里 | 若第 1 条是「当日至今」：date_key = 观测时刻按 LD-02 第 6 章的归日时区取日期；午夜后 N 分钟内的观测因报表批延迟可能仍是前一天的值，**不用**（N 首版 30，热调）。if03b 的区间自带 inTime，按 inTime 归日，不受影响 |
| 3 | objectIds 从哪来 | 契约写 `{{ agent_ids }}`，没说来源。接入角色读不到 core（DD-74），只能读 raw 与 meta | 取 raw.icp_reference 中本中心 VDN 最近一份**可信** agents 快照（DD-94 十九的口径）的工号，再并上该日有过版本、后来被删的工号（同样从 raw 快照取），否则当天中途删掉的坐席这一天的指标取不到。文档上限 20000、契约写 2000：按 2000 分批，无害 |
| 4 | 值的类型 | 文档全是 string；示例里 `IDX_04_02_022` 是 `"10759.0"`；不存在的对象 `val: null` | raw 按原样存；staging 转整数，`"n.0"` 视为整数，非整数小数或非数字 → 该批隔离；null 保留 null（契约已写「null 不得写成 0」） |
| 5 | **核心表的 agent_sk NOT NULL REFERENCES core.dim_agent** | `fact_agent_session`、`fact_agent_day` 都如此。配置里已删的坐席（DD-95 的情形）的签入区间与日累计进不了 core，且 DD-95 禁止造哨兵成员 | 结构变更把两表的 agent_sk 改为可空、保留外键，另加 `agent_no text NOT NULL`（源端工号，供重新关联与对账）；缺维度时开 DD-95 那种待澄清（每工号一条）。维度模型的重新关联（relink）扩到这两张表 |
| 6 | 关联到哪一版 | 维度是 SCD2 | 区间：signin_at 时有效的版本（与话单腿一致）。日累计：该日结束时刻（次日 00:00 前一秒）有效的版本；该日中途被删的坐席则取当日最后一个有效版本 |
| 7 | raw 的形态 | 契约只写幂等键 | `raw.icp_agent_session`：每段区间一行（工号、inTime、outTime、duration 原样 + payload）；`raw.icp_agent_day`：每坐席每次观测一行（工号、归属日、idxs 原样 jsonb）。两表按 DD-88 按月分区、DD-82 先比哈希只写变化的观测 |
| 8 | 仍在线的区间 | outTime 为空，duration 是否随时间增长不明 | 按 core_version = observed_at 取最新观测（契约已写），签出后区间定稿；未签出的区间在报表里算到观测时刻。Q37 问 ICP：未签出时 duration 是什么 |
| 9 | 跨日区间与返回范围 | 文档示例只有一段 | Q38 问 ICP：返回的是「当日签入的区间」还是「与当日有交集的区间」；前一天签入、今天仍在线的区间今天还返回吗 |

第 1 条若答复为「能按日期查」，第 2 条随之取消，契约原设计基本成立；第 3–9 条与第 1 条无关，可以先定。

## 2026-10-30  开发机：第 8 步第一段 fact_call 已实现（结构变更 0010 · DD-96），五处请 Mac 侧知悉或定

按 LD-03 2.4 十条实现：0010（`is_complete`、`dim_call_result` 四值、腿表 `last_change_at` 索引）、`core.fact_call` 模型、四个测试
（每列从腿另法重算、结果码规则、有首腿必有通话行三条 ERROR，缺首腿一条 WARN），两个构建 DAG 都选上 fact_call。
临时库：首建 856 通 = 有首腿的 CALLID 数；重跑写入 0；补取后未完整的变完整（写 152 行）；改名后关联随维度版本走；
注入不一致的 CALLTYPE 开一条待澄清、不重复；全量构建 PASS=42、权限验证 150 项。runbook 15。

清单第 2 条（`released_by` 加 `unmapped`）**0006 已经做了**，0010 不再动。

| # | 事项 | 开发机的做法 / 问题 |
| --- | --- | --- |
| 1 | **单腿通话编几号？** 文档只写「1 为首张、-1 为末张」。若只有一张话单的通话编 -1（假接口就是这样生成的），按第一条「首腿未到不建行」，**纯 IVR 的单腿通话永远进不了 fact_call**，所有通话级指标都少了它们，只有 WARN 测试看得见 | 照第一条实现。请问 ICP（并入 Q21 或新开）：单张话单的通话 CALLIDNUM 是 1 还是 -1。若是 -1，建议规则改为「首腿 = 序号最小的正序号腿；只有 -1 腿的，以 -1 腿为首腿」 |
| 2 | 只有队列腿、末腿不是主叫释放（例如排队超时被系统释放）按第九条落到 `ivr_only` | 照规则实现；这类通话叫「仅 IVR」名不副实。是否要让「有队列腿」也归 `unanswered_system`，请定 |
| 3 | `dim_call_result` 的阿拉伯语名称 | 0010 里先写了开发机拟的四个（مُجابة · متروكة · غير مُجابة (أنهاها النظام) · الرد الآلي فقط），**请 Gavin 在真机执行 40 之前确认**；改要靠后续结构变更 |
| 4 | 哪些通话要重算 | 腿的 `last_change_at` 晚于 fact_call 最近一次写入、再多看一天。`last_change_at` 是写入事务的开始时刻而非提交时刻，先开始、后拿到锁的腿构建会带一个更早的时间，一天余量兜住；正确性靠逐行比内容哈希（与 DD-89「时间只缩小扫描」一致）。0010 为此给腿表加 `last_change_at` B-tree 索引（每次改写都会写这一列，按 DD-78 不用 BRIN） |
| 5 | fact_call 排在维度模型之后 | 维度模型重新关联腿会改通话的坐席与首队列；临时库上 fact_call 先于 dim_agent 跑过一次，当次没跟上（下次构建补上）。已用 `depends_on` 固定顺序；fact_call 同时取 `lock_call_leg_links` |

`dim_call_result` 的键：`core.sk('MKK', 'oss911', code)`——取值是本系统的分类，不是哪个源端的，所以 src_system 用 `oss911`，不用 `icp`。

## 2026-10-30  开发机：第 8 步第二段（注册表同步 46-metrics 与 mart 第一组）开工前，九处需定

清单第 7–10 条与 M-C05 的参数已改（metrics.yaml、icp_source_map.yaml，本节同一提交）。**M-C07、M-C11 的口径变了但没有递增 version**
（见下表第 5 条）。第 11–13 条对照 `meta.metric_registry` 的现有结构、LD-02 第 10、11 章的封账与发布规则，有以下几处写表之前必须定，
否则表结构要返工。

**注册表（46-metrics）**

| # | 问题 | 开发机建议 |
| --- | --- | --- |
| 1 | `meta.metric_registry` 主键只有 `metric_code`，DD-97 要「按（编码, 版本）指向列」；也没有 mart_table / mart_column / params 列 | 结构变更：主键改 (metric_code, metric_version)，加 `mart_table`、`mart_column`（见第 3 条）、`params jsonb`、`entity_date`、`retrospective`；同步时只增不改旧版本行 |
| 2 | 表里 `name_ar`、`name_en`、`sql_expression` 都是 NOT NULL，metrics.yaml 只有 `name_zh` 与 `measure`；DD-97 又说注册表不生成 SQL | metrics.yaml 补 `name_en`、`name_ar`（59 条的译名谁出？）；`sql_expression` 改可空或改名为 `measure` 存文字说明 |
| 3 | **比率与均值指标映射到哪一列？** M-C04 应答率、M-C06 平均等待、M-C11 占比……日 × 队列的均值无法再按周、按全部队列汇总（均值的均值是错的） | mart 只存可加的列（计数与合计），比率与均值在服务层由两列相除；注册表对 count/sum 类写 `mart_column`，对 ratio/avg 类写 `mart_numerator` 与 `mart_denominator`，部署时三者都校验 |
| 4 | **「已发布的指标必须对应到存在的 mart 列，否则部署失败」**：metrics.yaml 里 51 条 `published: true`，其中 CAD 一侧的 mart 一张都还没有 | 只校验声明了 mart_table 的指标；已发布但没声明的照常同步、标「未实现」，服务层对它返回明确的「未实现」而不是空。否则 46 要等全部 mart 建完才能第一次跑通 |
| 5 | 版本后缀从哪一版算起 | 很多指标已是 version 2（设计期的修订），还没有任何一版进过生产。建议：**进入生产之前的口径修改不递增版本**（本次 M-C07、M-C11 即按此处理），首次上线时的版本对应不带后缀的列，之后递增才加 `_v<n>`。若要递增，请 Mac 侧改 metrics.yaml |

**mart 的封账与发布（DD-81 · LD-02 第 10、11 章）**

| # | 问题 | 开发机建议 |
| --- | --- | --- |
| 6 | 当日临时数（每小时）存在哪、是不是「行」 | 未封账的日子以 `close_version = 0` 的行存在，每小时整日替换（删了重写）；「不改旧行」只约束已封账（≥ 1）的行 |
| 7 | 某一天什么时候封账 | D 日在「D 日的每日回扫完成之后的第一次夜间构建」封账（回扫在源端时区次日 02:00），写 `close_version = 1` 的行并删掉 D 日的 0 行。封账时仍未完整的通话不计入，之后完整了走第 8 条 |
| 8 | 修正怎么发现、往回看几天 | 夜间构建重算已封账的最近 N 天（建议 7），与该日最新一版逐行比，有任一行不同就整日写一版 `close_version + 1`。N 天之外的变化不自动修正，进监控（变化计数）由人决定 |
| 9 | `meta.data_publication` 的 kind | LD-02 只列 nightly · correction。每小时的临时数也改变查询结果，按 DD-81 缓存要失效，所以要加 `intraday`；每次写 mart 一行，mart 行带写入它的 `publication_seq`。此表随本段的结构变更建立（LD-02 第 11 章「随写入方一起建」） |

另两处细节开发机先这样做，不阻塞：「进行中」作为 `mart.calls__day__volume` 的单独列（未完整的通话数，只在临时行有值），不当作一个结果值；
坐席或队列为空的行（DD-95）在 mart 里以 `agent_key` / `queue_key = 'unlinked'` 成桶，同时保留源端工号列供 M-03 下钻。

## 2026-11-16  Mac 侧：对 10-30 四节「需定」的答复（Gavin 已定）

**一、DD-94 二十：选甲，撤回 REPEATABLE READ。** 分析成立（快照取在取锁那条语句，等锁期间提交的腿不可见）。READ COMMITTED + 事务级锁 +
两张临时表即定稿。已更正 DD-94。

**二、fact_call 五处**
1. 首腿规则改为「序号最小的正序号腿；只有 -1 腿的以 -1 腿为首腿」。Q39 已入问题单。请改模型与「有首腿必有行」测试。
2. 只有队列腿、系统释放 → **`unanswered_system`**；`ivr_only` 只剩「既无队列腿也无坐席腿」。请改规则与结果码测试。
3. 四个阿语名 **Gavin 已确认**，照 0010 现文，可上真机。
4、5 同意；`dim_call_result` 的 src_system = `oss911` 同意。LD-03 2.4 已同步改。

**三、if03b / if03c 九处：全部按开发机建议（DD-98）。** 第 1 条答复前按「当日至今」；Q36–Q38 已入问题单（现共 39 题）；
M-R02–M-R04 请在 metrics.yaml 标 `depends_unverified: true`。第 5 条两表 `agent_sk` 可空、加 `agent_no NOT NULL`，进结构变更。第 7 步可开工。

**四、注册表与 mart 九处：全部按开发机建议**，写进 LD-03 第 5 章补充与 DD-97 注。两点说明：
- 第 2 条译名：`name_en` 59 条由 Mac 侧出，`name_ar` 出草稿并标 `draft`（MOI 确认后翻正）——随下一次 Mac 侧提交给
  `metrics/metric_names.csv`，开发机并入 metrics.yaml。在此之前 46-metrics 的名称列可先用 name_zh 占位、不阻塞。
- 第 5 条：进生产前的口径修改不递增版本，已登记为规则；M-C07、M-C11 不递增是对的。

仓库根目录的 `归档.zip` 与项目无关，Gavin 自行删除，不要提交。
Mac 侧下一件：第 9 步 LD-04 API 契约。

## 2026-11-16  Mac 侧：59 条指标的英文名与阿语草稿——`metrics/metric_names.csv`

列：code、name_zh、name_en、name_ar、name_ar_state（全部 `draft`）。请开发机并入 metrics.yaml（每条加 `name_en`、`name_ar`、
`name_ar_state`），注册表增列 `name_ar_state`，展现层对 draft 的阿语名照常显示、M-04 指标字典标出「待确认」。
- 英文名定稿，改名不算口径变更（LD-03 第 6 章）。
- **阿语全部是草稿**：CAD 单据的阿语术语（بلاغ / أمر إحالة / أمر مباشرة）是 Mac 侧按通用用法拟的，须与 MOI 现行术语和 CAD 界面用词对齐后
  才能翻为 `confirmed`；确认由 M-10 的译名流程做（DD-56），不走发布。
- 带粒度的名称保留括号里的粒度（按出警单 / 按派警单 / 按接警单），LD-03 2.3 要求报表标题写明粒度，不要为了简短删掉。

## 2026-11-16  Mac 侧：第 9 步服务层最小集（DD-99 · LD-04 3.5 · LD-07 2.3 补充），开工清单

前置：第 8 步第二段的 mart 四张表与注册表（46-metrics）先有，服务层读它们。新决策照旧列「需定」。

**工程**
1. `src/svc/`，Python + FastAPI，独立 `venv-svc`，lock 进仓库、离线安装（同 venv-ingest 的剧本做法）；psycopg 3。新依赖逐个登记组件清单与许可（FastAPI MIT、Starlette BSD、uvicorn BSD、pydantic MIT；JWT 校验库请选宽松许可的，登记前在 NOTES 报名字）。
2. systemd 单元，监听 8090（8080 Keycloak、8081 Airflow）；配置 `/etc/oss911/svc.env`；库连接经 pgBouncer `oss911_txn`，登录用户属角色 `oss911_query_svc`（21 的 HBA 已为它留了规则，此时第一次真正用到——请实测它连 `oss911` 被拒）。
3. SQL 全部参数化、两段式表名；`statement_timeout` 10s（会话或语句级，经事务池时用 `SET LOCAL`）。

**接口**（`/v1`）
4. `POST /query`：`metrics[]`、`dimensions[]`、`filters{维度: [成员]}`、`range{from, to}`（日期，左闭右开，Asia/Riyadh）、`granularity`（day/week/month/quarter/year，周日起周）、`version`（latest | as_closed | 整数）、`locale`（ar | en）。
5. 多指标须同一 `mart_table`，否则 `incompatible_grain`。count/sum 取 `mart_column` 求和；ratio/avg 取分子分母列各自求和后相除，分母 0 → null。
6. 版本：latest = 每日最大 close_version（含 0）；as_closed = 仅 1，未封账日列入 `meta.unclosed_dates`。
7. 响应 `meta`：每指标 `metric_version`、`depends_unverified`；`data_as_of`、`publication_seq`、每日 `close_version`、`in_progress_calls`、`has_unlinked`、`empty_reason`（no_data | out_of_availability，3.4）。
8. `GET /metrics`（注册表：名称按 locale、name_ar_state、版本、implemented、可用维度）；`GET /dimensions/{dim}/members`；`GET /me`。
9. 上限 10000 行 → `result_too_large`（不截断）。

**身份与权限**
10. Keycloak OIDC：验签（JWKS 缓存）、`aud`、`exp`。开发机建 realm 客户端与四个角色、几名测试用户（剧本 22 之后的一步，口令不进仓库）。
11. 角色取令牌；部门取令牌声明，无则 `meta.user_department_map`；映射不到 → 403 `department_unmapped` + 告警日志，**不返回空**。
12. 语义层配置（建议 `metrics/marts.yaml`，随注册表同步）：每张 mart 表声明 `row_filter: none | department | self`、维度清单、敏感列。**未声明的 mart 表查询 API 不读。**
13. 话务落地：`legs__day_agent__handling` 为 `self`——接警员只见令牌工号对应的坐席行；`agent_name` 为敏感列，仅 supervisor / admin。无权的维度或字段 → 整请求 403 `field_not_permitted`（带字段名），不静默去列。
14. 每次请求写 `audit.access_log`（action = query，metric_codes、sensitive_fields 记字段名不记值、row_count、duration_ms、client_kind、request_digest）；写失败 → 请求 500，不放行。

**不做 / 其他**
15. 不接缓存后端；`cache_key()` 按 LD-04 3.3 实现并单测（含「不同部门不得同键」「发布序号变则键变」两条）。
16. OpenAPI：由应用生成 `contracts/openapi/v1.json` 提交；部署步骤核对一致，不一致失败。
17. 错误码：`metric_unknown`、`metric_not_implemented`、`dimension_invalid`、`incompatible_grain`、`department_unmapped`、`field_not_permitted`、`result_too_large`、`timeout`。
18. 测试至少覆盖 LD-10 的 TC-DISC-02（主管与接警员返回列不同）与 TC-DISC-03（部门映射不到的行为），在话务数据上的对应版本。

## 2026-11-16  Mac 侧：第 9 步开工清单的更正（评审五条全部采纳，以本节为准）

上节清单中与下列冲突的条目作废：第 2 条的单一连接、第 4 条的 `version`、第 6 条的选版方式、第 11 条的「映射不到一律 403」。

1. **权限上下文一次解析，三处共用**（主查询、`/dimensions/*/members`、响应 meta）。接警员调 agent 成员接口只得本人；
   `in_progress_calls`、`has_unlinked` 在其获准范围内算。无权字段既不能作输出维度，也**不能作过滤条件**。
2. **本人身份**：令牌声明工号（声明名开发机定，报 NOTES）→ 按 `agent_no` 过滤（覆盖全部 SCD 版本，不按某一版 sk）。
   工号缺失 / 对不上坐席 / 对上多个 → 403 `identity_unmapped`，不得漏加过滤继续执行。
3. **校验按适用的过滤**：表的 `row_filter = department` 才要求部门，`self` 才要求工号，`none` 两者都不拦。
4. **两个连接用途**：`SVC_DSN_ANALYTIC_READ`、`SVC_DSN_AUDIT_WRITE`（svc.env）。开发机两者同指 `oss911_txn`；生产读走分析副本的只读池
   （pgBouncer 届时加 `oss911_ro`，随 LD-08 部署清单）。顺序：查询 → 结果留在内存 → 审计 INSERT 并提交 → 返回。
5. **两个版本分开**：`data_version`（latest | as_closed | 整数）；`metrics` 元素可为 `"M-C04"` 或 `{"code": "M-C04", "metric_version": 1}`。
   省略口径版本 → 每日用当日生效的版本；范围跨生效日：day 粒度照常、每行带版本，week 及以上 → 400 `metric_version_boundary`；
   版本不存在 → `metric_version_unknown`。注册表每版本加 `effective_from`（第 8 步第二段的结构变更里一并加）。
6. **选版先于过滤**：新表 `meta.mart_day_publication (mart_table, date_key, close_version, publication_seq, row_count, published_at)`，
   每次写 mart 的每个日期一行，**零行也登记**；查询先据它定每日版本，再读行、过滤、聚合。meta 的每日状态取三值：
   published（含零行）/ not_built / not_closed。请把它放进第 8 步第二段的结构变更，mart 模型每次写入同事务登记。
7. **话务 mart 的可见角色**（进 `metrics/marts.yaml` 的 `roles`）：`legs__day_agent__handling`——call_taker（self）、supervisor、admin；
   `calls__day__volume`、`calls__day_queue__service`、`calls__day__release`——仅 supervisor、admin。dispatcher 四张都不可见。
   无权的表 → 403 `table_not_permitted`。
8. `cache_key()` 加入本人范围；补测「同部门两名接警员键不同」。
9. 第一版查询不经 Superset（LD-05 2.1 已注）。
10. **前置**：fact_call 首腿规则与「队列腿系统释放 → unanswered_system」先落到模型（现仍限定 `leg_seq = 1`），服务层验数在其后。
已记 DD-99 注、LD-04 3.5、LD-03 第 5 章、LD-05 2.1。


## 2026-11-16  开发机：fact_call 两处更正已实现（结构变更 0011）；译名已并入 metrics.yaml

**fact_call（答复第二节第 1、2 条）**
- 首腿 = 序号最小的正序号腿，只有 -1 腿的取 -1 腿。新列 `core.fact_call.first_leg_seq` 记下用的是哪一条：1 正常，-1 单张话单的通话（Q39），
  2 以上是 1 号腿缺了或迟到。WARN 测试 `fact_call_leg_without_first_leg` 改为只报「2 以上」的通话；-1 的属待 Q39 答复的正常情形，不报。
  「有首腿必有行」改为 `fact_call_for_every_call`：腿表里的每通电话都有行（ERROR）。
- 结果码第 3 条改为「有队列腿或坐席腿、都未接通、非主叫释放」→ `unanswered_system`；`ivr_only` 只剩两者都没有。结果码测试同步。
- 0011 **清空 fact_call** 后加列（NOT NULL 无默认）：按旧规则建的行有两类错（缺 1 号腿的通话没有行、队列腿系统释放记成 `ivr_only`），
  表空则下一次构建全量重建。附带效果：40 与 50 之间若调度触发构建，旧模型因结构不一致失败，不会按旧规则把表填回去（runbook 15 已写）。
  目前没有读 fact_call 的 mart，清空无下游影响。
- 临时库（保留 0010 时的数据升级）：0011 后首建 999 通 = 腿表的通话数（原 997，多出的 2 通是只有 -1 腿的）；重跑写 0；
  把一通只有队列腿的通话末腿改为系统释放 → `unanswered_system`；删掉某通的 1 号腿 → `first_leg_seq = 2`、WARN 1，补回 → 1、
  呼叫时刻回到 1 号腿的时刻；旧版模型对新表结构报 out of sync。fact_call 及其测试 PASS=12。

**译名（11-16 第二节）**：`metric_names.csv` 的 59 条并入 metrics.yaml（每条 `name_en`、`name_ar`、`name_ar_state: draft`），
中文名逐条核对一致；csv 已删除，metrics.yaml 仍是唯一来源。M-R02–M-R04 标 `depends_unverified: true`（DD-98）。

**下一步**：第 8 步第二段——结构变更 0012（注册表主键改为（编码, 版本）、mart_table / mart_column / mart_numerator / mart_denominator /
params / name_ar_state / effective_from、`sql_expression` 可空、`data_publication` 加 `intraday`、`meta.mart_day_publication`）、
46-metrics、`metrics/marts.yaml`、四张话务 mart 与封账发布 DAG。第 7 步（if03b/c）排在其后。

## 2026-11-16  开发机：真机上 fact_call 重算测试跑了一个半小时没完——已改；Airflow 超时留下孤儿查询

执行 0011 时 TRUNCATE 被 4 个 dbt 会话挡住，事务已开 1:33、1:03、0:32、0:03——每 30 分钟一个，正是构建任务的 `execution_timeout`。
- **慢的原因**：`fact_call_recomputes_from_legs` 把腿表写成一个 CTE，每列一个相关子查询都引用它。CTE 被引用多次即物化，物化结果没有索引，
  每个子查询对每通电话全表扫一遍：真机约 4 万通 × 15 列 × 全部腿，几个小时。临时库只有 999 通，7 秒，没看出来。
- **改法**：子查询直接读腿表，走 `call_sk` 索引（`ix_leg_parent`）。临时库实测仍抓得到差异（改一通的 talk_seconds → FAIL 1）。
  另：这条查询表达式很大，JIT 编译约占 7 秒（`jit = off` 时 0.2 秒）；固定开销，不随数据量增长，先不处理。
- **孤儿查询**：Airflow 超时只杀 dbt 进程，库里的查询照跑，下一次构建又起一个，越积越多、互相抢 CPU。建议给 `dbt` 角色设
  `statement_timeout`（略小于任务超时，如 25 分钟），让库自己终止；属角色设置，开发机下一次改角色剧本时一并做，不另开结构变更。请 Mac 侧知悉。
- runbook 15 的 0011 一节补了「先暂停两个构建 DAG、确认没有残留 dbt 会话」。

## 2026-11-16  开发机：一致性测试按「近一天的通话」查、夜间全量；dbt 语句超时；假接口补三类通话

**检查范围**（Gavin 定）：`fact_call_recomputes_from_legs` 与 `fact_call_for_every_call` 在构建里只查「最近一天 fact_call 写过的通话」∪
「最近一天有腿变化的通话」（宏 `calls_to_check`）；时间只选通话，选中的通话读全部腿。前者抓写错，后者抓漏更新与漏建行。
新 DAG `maintenance_checks`（每日 00:30 UTC = 03:30 利雅得，回扫之后）以 `full_check` 查全部通话，兜住窗口外的变化。
临时库：腿改了、通话行未重算 → 重算测试 FAIL 1；同一变化放到两天前 → 构建档 PASS、全量档 FAIL 1；删掉近期有腿变化的通话行 →
每通都有行 FAIL 1；还原后全 PASS。真机 9 万通时全量重算 21.5 秒，其中约 7 秒是 JIT 编译。
fact_call 的 `built_at` 没有索引，构建档每次顺扫 fact_call 一遍（现在毫秒级）；随第 8 步第二段的 0012 一并加。

**dbt 语句超时**：`db_logins` 增可选 `statement_timeout`，dbt 设 25min（构建任务超时 30 分钟）；40 的登录用户步骤 `ALTER ROLE ... SET`。

**假接口**：按真机核验时发现，原生成器每通都是 IVR → 排队 → 坐席，结果只有 answered 与 abandoned，Mac 侧这次更正的两条规则在真机上
没有数据可测。另加三类（另一条随机流、CALLID 以 `-91` 结尾，不改原有通话的内容）：只经 IVR（单张话单、编 -1）、排队超时系统释放、
坐席振铃未接系统释放，约 6%。临时库补取 7 小时后四种结果都出现，`ivr_only` 的 `first_leg_seq = -1`。比例是编的，不作口径依据。

## 2026-11-16  开发机：第 8 步第二段第一块——结构变更 0012 与 46-metrics 已实现

按 DD-97 落地九条与评审补充（runbook 16）：
- **0012**：注册表主键（metric_code, metric_version）；`sql_expression` 改名 `measure` 可空；增 name_zh、name_ar_state、aggregation、
  entity_date、retrospective、params、mart_table / mart_column / mart_numerator / mart_denominator、effective_from、definition_sha256；
  CHECK：count/sum 只填 mart_column，ratio/avg 只填分子分母，其他聚合（median、p90、min、max）不能指向 mart。
  新表 `meta.data_publication`（kind：intraday · nightly · correction）、`meta.mart_day_publication`（主键（表, 日, 发布序号），
  close_version ≥ 1 的（表, 日, 版本）唯一；零行也登记）；dbt 只可 INSERT，不可改删。`fact_call.built_at` 索引。
- **46-metrics**：读发布目录里的 metrics.yaml（发布目录增 `metrics/`），`metrics_to_json.py` 校验并算口径哈希，`metric_sync.sql`
  以属主一个事务内同步、咨询锁串行。JSON 经 stdin 以 dollar-quote 传入（45 KB，作命令行参数会随指标增多撞 128 KiB 单参数上限）。
- 临时库：首次 59 inserted、再跑 59 unchanged；改名 → updated；上线前改口径 → redefined，上线后 → 失败；升版本 → 新行、旧行保留、
  effective_from 为当天；版本倒退 → 失败；指向不存在的表或列 → 失败并列出；已确认的阿语名不被草稿覆盖；median 指 mart → 转换即拒。
  权限验证 157 项（新增 7 项）。

开发机自定、请 Mac 侧知悉的四处：
1. **口径哈希**覆盖 definition、grain、aggregation、measure、entity_date、params、retrospective；名称、维度、来源表、depends_unverified、
   published、mart 映射可不升版本更新（LD-03 第 6 章「新增维度」「结果恒等的重写」不升版本）。
2. **上线前后开关**：inventory `metrics_pre_production`（现 true），上线那天改 false。
3. **effective_from**：某指标的第一版为 `-infinity`（覆盖全部历史，服务层「省略口径版本按当日生效版本」才有版本可取）；新版本取
   yaml 可选字段 `effective_from`，未写则为同步当天（利雅得）。
4. **阿语名确认后部署不覆盖**：库中 `name_ar_state = confirmed` 时 46 保留库中名称。M-10 写这两列的授权等 M-10 开工时加。

**顺序约束**：46 校验 mart 列存在，故 mart 表须先于 46 建好；第二块（四张话务 mart）会给出「50 → 首次 mart 构建 → 46」的顺序。

## 2026-11-16  开发机：第 8 步第二段第二、三块——四张话务 mart、按日发布、marts.yaml 已实现

runbook 17。结构：每张 mart 表背后一个计算视图 `staging.mart_calc__<表>`（从 core 现算全部日子，服务层读不到 staging）；
mart 模型（incremental append）只发布某些日子，宏 `macros/mart.sql`：
- **intraday**（每小时 :15）：最近 7 天内未封账的日子整日重写为 close_version 0。
- **nightly**（每日 04:00 UTC，先跑 fact_call 使其跟上腿）：回扫已进 core 的日子按日期顺序、不跳过地封为 1，删其 0 行；
  最近 7 个已封账日重算、逐行比（EXCEPT ALL 双向），有差整日写 +1。「回扫已进 core」从库判断：该日（利雅得）覆盖的每个源端日
  （契约 source_timezone，ICP 为 UTC）都有成功的回扫运行且其批次全被 fact_call_leg 消费；早于第一次回扫的源端日算定稿。
  所以真机这半个月回扫失败（假接口夜里没开）只会推迟封账，补跑后的下一晚自动补上。
- 每次写一张表 `data_publication` 一行（按种类），每写一天 `mart_day_publication` 一行，零行也登记；测试 `mart_matches_publication`
  核对表与登记一致、已封账日无残留临时行。mart 目录 `full_refresh: false`。
- 临时库：intraday 两次（第二次整日替换）；回扫 10-26 至 11-15 后 nightly 封 21 天、删去其临时行、11-16 保持临时；重跑 nightly 不产生发布；
  改 core 一通电话 → 只有受影响的两张表出 v2，再改回 → v3，v1 行保留；删一行或复制一行已封账数据 → 测试 FAIL。

**`fact_call_recomputes_from_legs` 再改**：临时库一次补进 16 万条腿后，新分区尚未被 autovacuum 分析，规划器当它为空，相关子查询对每列每通
全表扫，9 分钟未完。改为窗口函数排名 + FILTER 聚合，一次哈希连接，不依赖统计信息：7.6 万通 3 秒，仍能抓出差异。真机月初或大批补数后同样会遇到，
故一并上线。

**marts.yaml**（DD-99 更正第七条的落地）：每张表的 roles、row_filter（none · department · self）、self_column、dimensions（维度名 → 列）、
required_dimensions、attributes（成员附带列）、sensitive_columns（列 → 可见角色）。46 一并校验：格式、表与列在库里存在、
metrics.yaml 每个 mart_table 都在其中声明。服务层从哪里读它（发布目录还是同步进 meta）第 9 步再定，开发机倾向同步进 meta 一张只读表。

**metrics.yaml**：12 条加了映射（M-C01–07、M-C09–13）；M-C08、M-C14 未发布，M-C15 未实现（见下第 5 条）。

开发机自定、请 Mac 侧知悉或定的六处：
1. **volume 表的粒度是「日 × 站点」，结果与方向展开为列**，不是 LD-03 第 5 章写的「日 × 方向 × 结果」：比率要在同一行的两列之间算
   （DD-97 第三条），应答 / 呼入必须并排。M-C01 的「CALLTYPE 属呼入集合」体现在列上（只数 inbound），非呼入另列 `non_inbound_calls` 供对账。
2. **M-C05 的分母取排队的通话**（`queued_calls`，含放弃），分子为等待 ≤ X 秒且应答的；定义原文只写「等待不超过 X 秒即被应答的比例」，
   分母未写。若应取「已应答」或「呈现」，请定。X 来自 dbt 变量 `service_level_seconds: 20`，与 metrics.yaml 的 params 同值，暂未做自动核对。
3. **`incomplete_calls` 在已封账行也有值**：即封账时仍未完整的通话数（不计入呼入），是数据质量信号，而不是只在临时行有值。
4. **每张表一次发布一个序号**：一次 DAG 运行写四张表即四个 `publication_seq`（按种类再分），不是全 mart 共用一个。缓存键用到的「发布序号」
   因此按表取（LD-04 3.3），请知悉。
5. **M-C15（同一主叫失败次数）未实现**：「时间窗」未定义（同日？滑动 N 小时？），mart 无从按 caller_number 分组计数。请定窗口。
6. **坐席姓名拆为 `agent_name_ar`、`agent_name_en` 两列**，均为敏感列；维度 `agent` 取工号 `agent_no`（跨 SCD 版本），`agent_key` 为版本或 unlinked。

## 2026-11-16  开发机：回扫的「全重复」批次不登记，封账卡住——已修

真机第一次 nightly 封了 10-19 至 10-28，停在 10-29。原因：10-29、10-31 回扫的第 2 批（23:45–24:00）收到 93、96 行，全部与 raw 已有行
相同，去重后 `rows_written = 0`，raw 没有它的行；staging 的登记只认 `row_count = 0` 为空批次，于是它既不被 staging 也不被 fact_call_leg
登记，那天永远不算「回扫已进 core」，封账按日序停住。临时库的回扫每批都恰有新行，所以没测出来。

- `register_staged_batches`：空批次的判定改为 `rows_written = 0`（未去重前的 `row_count` 不再用）。参考数据快照不去重
  （`rows_written` 恒等于 `row_count`），行为不变。
- `register_core_batches`（fact_call_leg）：另登记 staging 已登记、`rows_written = 0` 的批次。
- 新测试 `batches_reach_core`：满一天、可处理（非隔离待定、非丢弃）、却未被 fact_call_leg 登记的 CDR 批次，一批一行；不引用模型，构建不选它，
  `maintenance_checks` 夜间跑。
- 临时库：对已回扫的 11-14 再回扫一次，两批均 `rows_written = 0`，构建后两批都登记；删去一批旧登记 → 测试 FAIL 1，恢复后 PASS。
- 已卡住的批次无需手工处理：部署后下一次构建自动登记。

请 Mac 侧知悉：隔离待定（pending）的回扫批次会让该日及之后不封账，直到 M-03 放行或丢弃；被丢弃的回扫批次在封账判定里仍算「未进 core」，
目前会一直挡住封账——是否应把「已丢弃」视为该源端日已定稿，请定（开发机倾向视为定稿：丢弃是人做的决定，不会再有数据来）。

## 2026-11-16  Mac 侧：对第 8 步第二段各节的答复

**mart 六处**：1 同意（LD-03 已更正为日 × 站点、展开为列）。2 同意分母取排队的通话；**请加部署核对**：dbt 变量 `service_level_seconds`
与注册表 M-C05 的 `params.x_seconds` 不同即 46 失败。3、4、6 同意。
5 **M-C15 窗口 = 自然日（利雅得），Gavin 定**：未接通 = 结果 abandoned 或 unanswered_system 的呼入通话；新 mart
`mart.calls__day__repeat_failed`（日 × 站点）：`repeat_callers`（当日未接通 ≥ 2 次的主叫数）、`repeat_failed_calls`（其未接通呼叫数）；
库内按 caller_number 分组、只出计数；caller_number 为空的通话不参与。M-C15 映射到 `repeat_failed_calls`；roles 仅 supervisor、admin。

**封账**：被丢弃的回扫批次视同该源端日已定稿，同意。**加一条告警**：最新封账日落后当前日期超过 3 天 → `maintenance_checks` 里一个
ERROR 级检查（监控批次接通道之前先让它红）。

**其余知悉并同意**：注册表四处；dbt 25 分钟语句超时；marts.yaml 同步进 meta 只读表、服务层读库；first_leg_seq；检查范围。
已记 DD-96 / DD-97 注、LD-03 第 5 章。第 7 步、第 9 步照排。


## 2026-11-16  开发机：Mac 侧答复的四处已实现

- **M-C05 参数与 dbt 变量核对**：做成通用的——metrics.yaml 每条指标可写 `param_vars`（参数 → dbt 变量），`metrics_to_json.py`
  读发布目录里的 `dbt/dbt_project.yml` 逐项比，不等即 46 失败。`param_vars` 只说参数怎样到达模型，不进口径哈希。
  M-C05：`x_seconds → service_level_seconds`；M-C15：`min_failed → repeat_min_failed`（新 dbt 变量，值 2）。
- **M-C15**：新 mart `mart.calls__day__repeat_failed`（日 × 站点）：`repeat_callers`、`repeat_failed_calls`，M-C15 映射到后者。
  metrics.yaml 的 M-C15：definition 写入自然日窗口与「未接通」的界定，params `{window: calendar_day, min_failed: 2}`，dimensions 去掉
  dim_agent（主叫跨坐席，按坐席拆无意义），source_tables 只留 fact_call。上线前，46 报 `redefined`。marts.yaml：supervisor、admin。
  开发机自定一处请知悉：**同一主叫按「日 × 站点」计**（一个号码同日在两个站点各失败一次不算重复）；每个有呼入的日 × 站点都出一行，
  零也是值。另：**跨日相加的含义是「各日重复失败之和」**，不是「一周内失败 ≥ 2 次」——后者是另一个口径。
- **被丢弃的回扫批次视同定稿**：`mart_final_days` 里「进了 core」= 被 fact_call_leg 登记，或在隔离里 discarded。待定仍挡住。
- **封账落后告警**：新测试 `mart_closing_not_behind`（最新封账日早于今天 − 3 天，或从未封账），入 `maintenance_checks`，ERROR。
- 假接口加一类「同一主叫当日反复打不通」（另一随机流、CALLID 序号 92，每分钟 3%，主叫取自当天固定的 15 个号码），已有呼叫不变。
- 临时库：回扫两天后，nightly 给这两天写出修正版本（8 个主叫 20 通、12 个主叫 34 通），与直接查 core 一致；判定探测：批次未进 core 或待定 → 该日不封，
  丢弃 → 封；造一张落后的表 → 告警 FAIL，删去 → PASS；46 同步 M-C15 `redefined | implemented`；dbt 变量改成 25 → 46 报错。

另：一个源端日回扫多次时（补跑、手工重跑），**任一次**完整进了 core 即算定稿；之后的回扫带来的差异由封账后 7 天的修正吸收。

## 2026-11-17  开发机：第 9 步第一块——读取规则进 meta、服务登录用户、Keycloak realm

**报名字（登记组件清单之前，Mac 侧开工清单第 1 条）**：验 JWT 选 **PyJWT**（MIT），它验 RS256 依赖 **cryptography**
（Apache-2.0 或 BSD-3-Clause 二选一）及其依赖 cffi（MIT）、pycparser（BSD-3-Clause）。其余照清单：FastAPI（MIT）、Starlette（BSD-3）、
uvicorn（BSD-3，不带 [standard]，即不引入 uvloop、httptools）、pydantic 与 pydantic-core（MIT）、psycopg 3（LGPL-3.0，入库已在用）。
锁文件 `infra/svc-requirements.lock` 随第二块提交；如对 PyJWT 有异议请在那之前说。

**令牌声明名（开发机定，第 9 步更正第 2 条）**：工号 `agent_no`、部门 `agency_type_code`，均为 access token 顶层字符串声明；
角色取 `realm_access.roles` 中四个之一（call_taker · dispatcher · supervisor · admin）；服务层核对 `aud` 含 `oss911-svc`。

**已实现**
- 结构变更 **0013** `meta.mart_rule`：marts.yaml 的只读副本，一表一行（roles、row_filter、self_column、dimensions、required_dimensions、
  attributes、sensitive_columns、规则哈希）。46 同步：新增、改动、不变、**移除**（yaml 里删掉的表从规则表删除，查询 API 随即不读）。
  marts.yaml 每张表须写 `grain`。权限验证 157 → 162 项（query_svc 能读不能改；owner 以外无人能写；两条约束）。
- 服务登录用户 **svc**（属 `oss911_query_svc`），口令在 `/etc/oss911/svc.env`（0600，属新系统账户 `oss911svc`，由 db_login 建）。
  db_login 的验证按池分：服务层用户经 `oss911_txn` 登录、会话池拒绝；dbt 与入库反之（原只验后者）。svc 的 `statement_timeout` 30s
  作兜底，查询 API 自己每条 `SET LOCAL statement_timeout = '10s'`（DD-99）。
  与清单的一处差别：`SVC_DSN_ANALYTIC_READ` / `SVC_DSN_AUDIT_WRITE` 两个连接串（只含主机、端口、库名）放第三块部署时写的
  `/etc/oss911/svc.conf`（0644，非机密，同 ingest.conf 的做法），用户名与口令只在 svc.env——连接串里不带口令，两个用途也可各配各的用户。
- 剧本 **23-keycloak-realm**（`infra/files/keycloak_realm.py`，只用标准库，读后再写、只改不同处）：realm `oss911`、四个 realm 角色、
  客户端 `oss911-svc`（只作 aud，不登录任何人）与 `oss911-portal`（公共客户端；其令牌带 aud 与两条属性声明）、七个开发测试用户
  （ct101、ct102 对应假接口坐席 101、102；ct_noagent 无工号；ct_unknown 工号 999 对不上坐席；disp1、sup1、adm1）。
  **两条属性在用户配置里声明为仅管理员可改**：否则用户可在个人账户页把工号改成别人的，「只见本人」形同虚设——已实测用户自改返回
  `error-user-attribute-read-only`。开发机为命令行测试开了 password grant（inventory `keycloak_password_grant`），**生产不开**
  （只走门户的浏览器流程）。管理员口令：keycloak.env 里还有引导管理员就用它，已按 runbook 91 换掉的，剧本提示输入（不回显）。
- 临时验证：0013 与同步在临时库上（插入、不变、改一张、改回）；Keycloak 26.0 起一个临时容器跑剧本 23 两遍（第二遍 changed=0），
  七个用户登录，令牌的 aud、角色、工号逐个核对。
- runbook 18（真机顺序：21 → 40 → 45 → 50 → intraday → 46 → 23，连同第 8 步第二段收尾的改动）。

**下一块**：`src/svc/` 服务代码（四个接口、权限上下文、选版先于过滤、审计、`cache_key()`、OpenAPI 生成）与测试。

## 2026-11-17  开发机：第 9 步第二块——服务代码 src/svc/（查询 API）

`src/svc/`（包 `oss911_svc`，结构同 src/ingest）、锁文件 `infra/svc-requirements.lock`、契约 `contracts/openapi/v1.json`（由代码生成；
测试与部署都核对一致）。四个接口按 DD-99 与其评审更正实现；测试 48 项（单元 21 项不依赖库；其余连临时库，以属 `oss911_query_svc`
的登录用户运行，即与生产同权限）。另用临时 Keycloak 签发的真实令牌走 JWKS 验签端到端跑通。

**依赖补报**：锁定时新版 FastAPI 带进两个此前没报的包：`opentelemetry-api`（Apache-2.0）、`annotated-doc`（MIT）。其余同第一块所报。
测试用的 pytest、httpx 只装在开发环境，不进锁文件、不进 venv-svc。

**实现上开发机自定、请 Mac 侧知悉或定**
1. **成员接口按指标定表**：`GET /v1/dimensions/{dim}/members?metric=M-C09&from&to&attributes=...`。维度名是按表的（agent 只在坐席处理表），
   按指标取表与「按指标查、不按表查」一致。属性要显式点名（`attributes=agent_name_en`），无权的属性整请求 403——默认不返回任何属性，
   于是不存在「对主管多给、对接警员静默少给」。日期范围缺省为最近 31 天。
2. **多角色取最宽的一个**（admin > supervisor > dispatcher > call_taker）：四个角色的可读范围是嵌套的，取最宽不会超出用户本有的任何一个角色。
3. **部门过滤**（话务上无表用到，只在单元测试里验了机制）：作用于 admin 以外的全部角色；过滤列取该表维度 `department` 对应的列。
   **请定**：主管是否也受部门过滤（LD-07 2.3 未写），以及将来调度类 mart 的部门列是否就叫 department 维度。
4. **每日状态多一个值**：除 published · not_built · not_closed 外，指定 `data_version` 为某个整数而那天没有该版本时为 `version_not_found`
   （那天已封账、只是没有这一版修正——与「未封账」不同）。
5. **审计**：拒绝的请求也写（role、metric_codes 照记，row_count 为空）；令牌无效（401）无从知道是谁，不写库、只写服务日志。四个接口的
   `action` 都记 `query`，`object_ref` 记 mart 表或接口路径；`client_kind` 由令牌的 azp 定（oss911-portal → portal，其他 → api）。
6. **错误码**（Mac 清单之外新增的都是更正与评审带来的）：`unauthenticated` 401、`role_missing` 403、`table_not_permitted` 403、
   `field_not_permitted` 403、`department_unmapped` 403、`identity_unmapped` 403、`metric_unknown` 404、`metric_version_unknown` 404、
   `metric_not_implemented` 404、`dimension_invalid` 400、`incompatible_grain` 400、`metric_version_boundary` 400、`result_too_large` 400、
   `request_invalid` 400（请求体不合格式）、`timeout` 504、`audit_unavailable` 500、`internal_error` 500。
7. **一次查询的范围上限三年**（超出 `request_invalid`）；结果上限 1 万行；`SET LOCAL statement_timeout = '10s'`。
8. **连接**：每个请求两次连接（分析读一次、审计写一次），服务内不另设连接池——pgBouncer 事务池就是池；关掉 psycopg 的自动预备语句
   （事务池下预备语句不跟连接走）。分析读的事务设为只读。
9. **元字段**：`in_progress_calls` 只对有 `incomplete_calls` 列的表给（量表），为所读临时日的未完整通话；`has_unlinked` 只对有
   `queue_key` / `agent_key` 的表给；两者都在请求者获准的行内算。另返回 `cache_key`（LD-04 3.3 的算法，无缓存后端）、`row_scope`（self:101 之类）。
10. 一次请求的口径版本跨生效日：按日且输出日期维度时照常，每行带 `metric_versions`；其余（周及以上、或不按日期输出）`metric_version_boundary`。

**下一块**：部署——30-python 装 venv-svc 与服务（离线 wheel，同 venv-ingest），`svc.conf`，systemd 单元（8090），部署时核对 OpenAPI，runbook 19 与真机验证。

## 2026-11-17  开发机：第 9 步第三块——服务层部署（60-svc）

- 剧本 **60-svc**：取 wheel（按 `infra/svc-requirements.lock`，wheelhouse 的 svc 子目录为空或 `-e svc_relock=true` / `wheelhouse_refresh=true`
  时才联网）→ 离线装 venv-svc 与程序 → **以服务账户运行装好的程序生成 OpenAPI，与仓库里的 contracts/openapi/v1.json 比，不同即停**
  （不重启）→ 写 `/etc/oss911/svc.conf`（两个连接串、realm 的 issuer 与 aud，均非机密）→ systemd 单元 `oss911-svc`（uvicorn，
  127.0.0.1:8090，`User=oss911svc`，`ProtectSystem=strict` 等收紧项）→ 验证：无令牌 401；开发机上以 ct101、sup1、disp1 从 realm 取令牌
  调查询，ct101 只得 101（self:101）、sup1 得全部坐席、disp1 被拒（table_not_permitted）。
- 验证调用写成 `infra/files/svc_probe.py`（标准库），口令经 stdin 传入。
- 依赖登记：MANIFEST 一行（21 个包及各自许可）、components.csv 六行（FastAPI 含 Starlette、uvicorn、pydantic、PyJWT、cryptography、
  opentelemetry-api）。
- 临时验证：用临时 svc.conf / svc.env 让 uvicorn 以 `--factory oss911_svc.app:serve` 起服务（读配置的真实路径），对临时 Keycloak 取令牌，
  svc_probe 输出与剧本断言逐行吻合；`--check` 对一致与不一致的文件分别 0 与 1。剧本本身要 root，未在开发机代跑。
- runbook 19。

第 9 步至此三块齐。服务层没有经过 Superset（DD-99 第八条），前端（LD-05）可以按 contracts/openapi/v1.json 开工。

## 2026-11-17  Mac 侧：服务层评审四条采纳、两处更正开发机的自定；下一步排期

**评审四条（请实现）**
1. 分析读：一个 `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY` 事务覆盖目录、权限、选版、取数、元字段；审计仍独立提交。
   补测：选版后、取数前插入一次 intraday 发布，响应不得为「published + 空」。
2. 封账边界（`macros/mart.sql`）：边界 = `contracts/resweep.yml` 的回扫起始日，不取 `min(source_day) from swept`。起始日之前算历史；
   起始日及之后逐日须回扫成功且批次全进 core（或已丢弃），遇第一个不满足的日子停。补测：15 日有 pending、16 日成功 → 15 日不封。
   真机上已封的日子请核一遍有没有被这条缺陷提前封的（列出来，Mac 侧定是否出修正版本）。
3. 主体键 = `sub`（配可信 issuer），`preferred_username` 只作显示。`meta.user_department_map` 改按 sub（结构变更，含已有数据迁移）；
   `audit.access_log.user_principal` 记 sub，另加 `user_display`。
4. 身份确认与权限判断分开：`role_missing`、`request_invalid` 等身份已确认的拒绝都写审计（role_code 记 `none`；不记请求里的敏感取值）；
   令牌无效仍只写服务日志。

**更正开发机自定的两处**
- 第 2 条「多角色取最宽」：不成立（接警员可见本人坐席数据、调度员不可见，范围不嵌套）。改为**一个用户只许一个业务角色**，
  令牌里多个 → 403 `role_ambiguous`（写审计）。
- 第 3 条部门过滤：只对 **dispatcher、supervisor**（LD-07 2.1）；call_taker 与 admin 不受。维度名 **`agency_type`**，不叫 department；
  marts.yaml 的 `row_filter: department` 保留为过滤种类名，过滤列取 `agency_type` 维度对应的列。

**其余八处知悉并同意**；PyJWT 及新增依赖无异议。已记 DD-99 注、LD-04 3.5、LD-03 第 5 章。

**排期**：① 上述六项 → ② 第 7 步 if03b / if03c（DD-98）→ ③ 监控告警通道（封账落后、STALE、待澄清数、dbt WARN、未关联工号现在只进日志；
开工前请列方案与需定，Prometheus 已在机上）。Mac 侧同时做第 10 步设计（门户外壳、四张话务报表、Superset 去留）。


## 2026-11-19  开发机：服务层评审六项已实现、真机已部署；封账核对结论与需定

**实现（提交 76213e4、52dc2f3）**
1. 分析读一个 REPEATABLE READ READ ONLY 事务（目录、权限、选版、取数、元字段），审计另一连接独立提交。补测：选版后取数前插入一次
   intraday 重写，响应不得为 published + 空；退回 READ COMMITTED 时该测必败。
2. 封账边界取 `contracts/resweep.yml` 的回扫起始日：`publish_marts_nightly` 从 DAG 发布清单传 `--vars resweep_from`，缺失即编译失败。
   补测 `test/mart_closing_check.sh`：15 日有 pending、16 日成功 → 15、16 日都不封；丢弃后才封；旧代码该测失败。
3. 主体键 sub：结构变更 0014——`meta.user_department_map` 按 subject（唯一），用户名移到 username 只作显示，旧行保留但不匹配任何人，
   新写或改动的行必须有 subject（NOT VALID 约束）；`audit.access_log` 加 `user_display`，旧审计行不改写。23 剧本核验每个测试用户令牌的
   sub 等于其 Keycloak 用户 id（`verified: … sub ok`）。
4. 身份确认与权限判断分两步：令牌有效即写审计，含 role_missing、role_ambiguous（role_code 记 `none`）与 request_invalid
   （object_ref 为路径，不记 metric_codes 与请求取值）；令牌无效只进服务日志。
5. 一个用户只许一个业务角色，多个 → 403 `role_ambiguous`。
6. 部门过滤只对 dispatcher、supervisor，过滤列取 `agency_type` 维度的列；46 校验声明 `row_filter: department` 的表须有该维度。
   `/v1/me` 的 problems 只列对该角色生效的过滤所缺的映射。

服务 0.2.0，OpenAPI 重新生成；测试 60 项。真机按 40 → 45 → 50 → 46 → 23 → 60 部署完毕（Gavin 执行）。`meta.user_department_map`
真机 0 行，无旧数据需迁移。

**真机已封日子的核对：没有被这条缺陷提前封的日子**
- 已封：五张 mart 表 20261019–20261114 各 27 天（`calls__day__repeat_failed` 11-18 才建，同范围）。
- 逐日核对封账时其覆盖的源端日（2026-10-25 起）是否已回扫进 core：10-27 起全部满足。
- 源端日 10-25 一度看似「从未回扫」：它的回扫是 Airflow `scheduled__2026-10-26T02:00`（10-26 05:00 首跑、16:23 重跑，两批均进 core，
  窗口 10-24 23:45 – 10-26 00:00），当时 `resweep` 模式尚未存在（结构变更 0005 于 10-26 16:26 才加），记为 `backfill`。
  数据在 10-26 已完整进 core，10-25、10-26 两天的封账是对的。无需修正版本。

**需定**
- (a) **源端日 10-25 在新封账逻辑下永远算「未回扫」**：`mart_final_days` 只认 `mode = 'resweep'`，而 10-25 的回扫记为 `backfill`。
  已封的日子不重开、下一次 nightly 从 11-15 起，**当前无影响**；但任何从头写的 mart 表（新增表、整表重建，if03b/if03c 可能就有）
  会封到 10-24 后永远停住（封账不越过未封的日子）。三种做法：
  ① `contracts/resweep.yml` 起始日改为 2026-10-26，10-25 算历史（其数据实际已完整进 core）——只改一行配置，代码不动（开发机倾向）；
  ② 把那次运行的两批在 `meta.ingest_batch` 改记 `resweep`——改历史记录；
  ③ 封账同时承认 0005 之前回扫 DAG 记为 `backfill` 的运行——改代码，且须区分手工补数与回扫。
- (b) nightly 缺 `resweep_from` 时直接编译失败（DAG 失败），而不是悄悄不封。开发机这样定了，请确认。
- (c) 令牌无效且请求格式不对：答 401，不写审计（身份未确认，与令牌无效同一规则）。请确认。
- (d) role_ambiguous 与 role_missing 一样 role_code 记 `none`（多个角色不挑一个记）。请确认。

**下一步**：第 7 步 if03b / if03c（DD-98）。

> **Mac 侧答复（2026-11-19，Gavin 已定）**
> - (a) **选 ①**：`contracts/resweep.yml` 起始日改为 2026-10-26，10-25 算历史。不改 ingest_batch 的历史记录，不改代码。改后请在临时库验一次
>   「从头建一张 mart 表能一路封到最新」。
> - (b)(c)(d) 均同意。
> - 封账核对结论知悉，不出修正版本。已记 DD-97 / DD-99 注。
> - 第 7 步 if03b / if03c 可开工（DD-98）；之后是监控告警通道，开工前先列方案与需定。


## 2026-11-19  开发机：回扫起始日改 2026-10-26（Mac 答复 (a) ①）

- `contracts/resweep.yml`：icp_cdr_voice 改为 2026-10-26，注释写明原因。代码与 ingest_batch 历史记录不动。runbook 11 手工补扫的例子随之改为
  逻辑日期 2026-10-27T02:00（扫 10-26）。
- 回扫 DAG 的 start_date 随之从 10-26 移到 10-27；Airflow 里已有的运行不受影响，之后照常每天一次，不会多排补跑。
- **临时库验证「从头建一张 mart 表能一路封到最新」**：复制临时库为 oss911_fresh，补一次「源端日 10-25 由回扫 DAG 扫过、记为 backfill、
  两批进 core」以仿真机，清空 `mart.calls__day__volume` 及其发布登记后跑 nightly：
  - resweep_from = 10-25：一天都不封（首个未封日 10-26 跨源端日 10-25，认不出回扫）——即原先的问题；
  - resweep_from = 10-26：封 20261026–20261115 共 21 天（11-15 是临时库最后一个有回扫的源端日），再跑一次不变；封账行与原库
    同期最新版本逐行一致（md5 相同）。
- 真机部署：45 → 50（发布内容变了，45 生成新发布目录）。

## 2026-11-19  开发机：第 7 步 if03b / if03c 已实现（DD-98），待真机部署；开发机自定九处与需定三处

**实现（提交 dc06c68、a32ef34）**
- 契约 if03b / if03c 按 DD-98 重写（未发布过，仍为 v1），进 release.yml 与 pools.yml（icp_cms）。结构变更 0015：两张 raw 表（按月分区）、
  ingest_batch 增 mode `observation`、热调白名单加 `midnight_skip_minutes`、两张 core 表 `agent_sk` 可空并加 `agent_no` / `src_observed_at`、
  日表唯一键改 (agent_no, date_key)。
- 接入程序 0.2.0：`agent.py`（取工号、分批、批次检查、只写变了的观测）。假接口加 `hindex/agent`（当日至今，呼入数取自同日假话单）。
- dbt：两个 staging、两个 core 模型；dim_agent 版本变化时一并改关联；缺维度每工号一条待澄清（DD-95）；五个测试；两个构建 DAG。
- 权限验证 178 项、接入单元测试五组、假接口 26 项全过；临时库端到端（含隔离、去重、中途删坐席、修复关联）见两次提交说明。runbook 20。
- M-R02–M-R04 的 mart 表**未建**：第 7 步按 CLAUDE.md 第 10 节只到 core。

**开发机自定，请知悉或更正**
1. **每 10 分钟观测一次**（DD-98 第一条 ② 说「23:5x 加取或缩短周期」，取后者）。日终值缺当地 23:50 之后的变化。
2. **raw 去重比的是「该键上一次（未在待处理 / 已丢弃批次中的）观测」**，不是契约原写的「任一同键同哈希已存在即不写」。理由：日累计与
   未签出区间会变来变去，值改回原样（A→B→A）时按原写法第三次不写，core 会停在 B。两份契约的 raw_write 已改写为此义。if03a 不动。
3. 一份可信 agents 快照都没有：运行失败（exit 1，run_log failed），提示先跑 icp_reference。快照里是空的：success、不请求。
4. 工号不是整数（objectIds 是整数数组，p.935）：不请求，每个工号一条待澄清。
5. 返回了没请求的工号、记录缺请求的指标、值非整数 / 非 null、区间 inTime 缺失或格式错、outTime 早于 inTime、同一区间重复：整批隔离；
   返回了没请求的**指标**：登记 mapping_registry（unknown），不隔离。
6. ingest_batch：`window_from` = 归属日（YYYY-MM-DD），`window_to` = 观测时刻（ISO，带偏移）；run_log 的 watermark_from / to 同。
7. 区间 duration 与「签出 − 签入」之差超过 **60 秒** 才进 WARN 测试（契约原文「超过阈值」未给数）。
8. 日累计的 agent_sk 取「与当日重叠的最新版本」：当日结束时有效者即它；当日中途被删者，即当日最后一版——两种情形一条规则。
9. 假接口：平台的日按 UTC+3 切；话单把呼叫分给任意时刻的任意坐席，故每名坐席几乎全天在线（两段）。只影响开发机数据。

**需定**
- (a) **归日时区的风险**：日累计按 `Asia/Riyadh` 归日（DD-98 第二条、LD-02 第 6 章）。若平台自己按 UTC 切日（它的话单时间是 UTC），
  当地 00:00–03:00 的观测会被记成新一天，而值仍是前一天的累计——30 分钟挡不住，且不报错。Q36 答复前是否把 midnight_skip_minutes
  首版调到 180、宁缺勿错？开发机未改，仍为 30。
- (b) **日累计与话单的对账没有写成 dbt 测试**：日终值固有地缺最后十分钟，按坐席日比呼入数会天天有差、WARN 天天响（屏蔽无害噪声）。
  建议并入监控告警通道一起定：差异进对账表、按阈值报，而不是每次构建报。
- (c) **`tools/check_refs.py` 只读 `ddl/0*.sql`**，看不到结构变更用 ALTER 加的列：契约把 `id` 指向 `fact_agent_*.agent_no` 时校验失败，
  现暂指向 `agent_sk`（也对，note 写明另存 agent_no）。是否让它也读 `ddl/changes/`？（Mac 侧的工具，开发机未动。）

另：`batches_reach_core`（maintenance_checks）只查话单批次；坐席两源的批次进没进 core 暂无夜间检查，随监控告警通道一并列。

**下一步**：真机部署（runbook 20：40 → 45 → 30 → 50，重启假接口，各手工跑一次，开四个 DAG）。同时开始写监控告警通道的方案与需定（只列，不实现）。

## 2026-11-22 开发机：第 7 步部署顺序更正——30 须在 45 之前

真机跑 45 报 `the field 'args' has an invalid value … Expecting value: line 1 column 1`。原因两层：

1. **runbook 20 的顺序写错了**（40 → 45 → 30 → 50）。45 用已装的接入程序校验契约，真机上还是 0.1.0，不认识
   `icp_agent_session` / `icp_agent_day`（`no ingestion for connector 'rest_api'`），校验失败。前几步（13、14）一直是
   30 在 45 之前，这次是我排错了。已改为 40 → 30 → 45 → 50。45 停在校验，同步之前，库与发布目录未动，不需要回滚
2. **45 的断言把真正的报错盖住了**：assert 失败时也会渲染 success_msg，对空 stdout 做 `from_json` 即模板错误，
   fail_msg 里的 stderr 就看不到了。改为只在 rc 为 0 时解析 stdout。本地用两条模拟结果验过：成功的照常显示覆盖值，
   失败的显示 stderr

**下一步**：真机部署按 40 → 30 → 45 → 50（40 已跑过的不用重跑）。

## 2026-11-22 开发机：第 7 步真机部署完成；监控告警通道方案与需定（只列，未实现）

**第 7 步真机**：40 → 30 → 45 → 50 全过（顺序更正见上节）。core 两表：区间 120 行、日累计 60 行，全部关联，60 名坐席，
最新观测在十分钟内。坐席 101：两段区间，时长与签入签出逐秒相符，在线那段 `signout_at` 为空；日累计 `signin_count` 2
与段数一致，`signin_seconds` 70458 对区间合计 70460（观测早 1.5 秒）。`publish_marts_nightly` 11-21、11-22 两晚 success
（回扫起始日 10-26 之后）。

### 一、现状：信号已有，通道没有

| 信号 | 现在在哪 | 谁能看到 |
| --- | --- | --- |
| 封账落后 | dbt 测试 `mart_closing_not_behind`（ERROR，`maintenance_checks` 每日 04:30） | Airflow 里那一个任务变红 |
| 参照副本过期 | `run_log.message` 里的 `STALE:`，任务仍 success | 无人，除非去翻 message |
| 待澄清 | `meta.clarification_queue`（open / asked） | 无界面（M-03 未建） |
| dbt WARN | 六个 warn 测试（`fact_call_leg_without_dimension`、`agent_facts_without_dimension` 等），只写在 Airflow 任务日志 | 无人 |
| 未关联工号 | 话单腿与坐席两表 `agent_sk IS NULL`；坐席两表另开待澄清 | 同上 |
| 坐席两源批次进没进 core | 无（`batches_reach_core` 只查话单） | — |
| 日累计与话单对账 | 无 | — |

Prometheus 在机上，只采自己与 HAProxy；没有 Alertmanager，没有任何告警规则，也没有投递出口。

### 二、方案（建议）

**1. 一个只读的指标导出程序 `oss911-monitor`**，查 meta / core，以 Prometheus 文本格式暴露 gauge。规则放在 Prometheus，
导出程序只报「是多少」，不判「该不该响」。

- 放在接入程序包里作第二个入口（复用连库、配置与打包；不新增 wheel）；systemd 常驻，只绑宿主机对容器网关的地址
- 查询结果缓存 60 秒，Prometheus 该 job 采集间隔 60 秒——指标查询不随采集次数打库
- 新只读角色 `oss911_monitor`（结构变更 0016），只 SELECT 用到的 meta 表与 core 三表；凭据在 `/etc/oss911/monitor.env`
- 依赖 `prometheus_client`（Apache 2.0）进 wheelhouse

**2. 指标（第一批）**

| 指标 | 含义 | 来源 |
| --- | --- | --- |
| `oss911_mart_closed_lag_days{mart}` | 今天（Asia/Riyadh）减最新封账日 | `meta.mart_day_publication` |
| `oss911_reference_sync_age_seconds{scope}` | 参照副本距上次同步的秒数 | `meta.ingest_batch.snapshot_synced_at` 最新一份 |
| `oss911_clarification_open{source_id}` · `oss911_clarification_oldest_age_seconds{source_id}` | 未处理条数、最老一条挂了多久 | `meta.clarification_queue` |
| `oss911_dbt_test_failures{test, severity}` | 最近一次运行该测试的失败行数 | 新表 `meta.check_result`（见第 3 条） |
| `oss911_unlinked_agents{table}` | `agent_sk IS NULL` 的不同工号数 | core 三表（坐席两表走 0015 的部分索引；话单腿需同类部分索引，0016 加） |
| `oss911_unprocessed_batches{source_id, model}` · `_oldest_age_seconds` | 已提交、未进 core 的批次 | `meta.ingest_batch` 对 `meta.staging_batch_state`，覆盖全部源（补需定 (b) 之外的那条缺口） |
| `oss911_source_last_success_age_seconds{source_id}` · `oss911_source_consecutive_failures{source_id}` | LD-01 10 章「源中断」「水位停滞」 | `meta.run_log` |
| `oss911_threshold{signal, key}` | 库里已有的阈值原样导出（`version_stale_days` 热调值、封账落后天数） | `meta.source_runtime_param` / 契约 |

最后一行的用意：已有的阈值（可热调的 `version_stale_days`、dbt 变量里的 3 天）不在规则文件里再写一份，规则写成
`metric > on(...) oss911_threshold`，阈值只有一个权威。

**3. dbt 测试结果落库**：`on-run-end` 宏把本次每个测试的 状态 / 失败行数 / 严重度 写进 `meta.check_result`（0016 新建，
只增，按月分区，保留 90 天）。WARN 从此可查、可报，M-08 将来也读这张表。不读 `run_results.json`：它在 Airflow 节点
本地、每次覆盖，生产两台 worker 各一份。

**4. 规则与投递**：Prometheus 规则文件 `alerts.yml` 由 22-containers 部署，部署前 `promtool check rules`；新增 Alertmanager
容器（Apache 2.0，digest 钉死，进 MANIFEST 与离线介质）。告警只含定位信息（源、表、条数、阈值），**不含工号、号码等
数据本体**（LD-01 10.2：告警出 DMZ）。开发机没有短信邮件网关：Alertmanager 发 webhook 到 `test/` 下一个落文件的假网关，
用于验收「告警可送达」；生产接入方式见需定 ②。

**5. 日累计与话单对账**（需定 (b) 的落法）：按坐席逐日，比 `fact_agent_day.inbound_calls` 与**应答时刻早于该日
`src_observed_at`** 的话单腿数——用日终值自己的观测时刻截断话单，最后十分钟的缺口两边一起去掉，不再天天报差。
已封账的日子才比，结果写 `meta.check_result`（作为一项检查），超差计数进上表的 dbt 指标。

**实现分块**（批准后）：① 0016（角色、`meta.check_result`、话单腿部分索引）+ on-run-end 宏 + 对账测试；
② `oss911-monitor` + 30 部署 + Prometheus 采集与规则；③ Alertmanager + 假网关 + runbook 21 + 权限验证补项。

### 三、需定

| # | 问题 | 建议 |
| --- | --- | --- |
| ① | **新增 Alertmanager 组件**（选型变更，入 MANIFEST 与 LD-09） | 加。Prometheus 自身不投递；自写投递等于重造分组、静默、去重 |
| ② | **生产投递路径**：Alertmanager 直连总包商的邮件 / 短信网关，还是先进本方「统一投递服务」（LD-01 10.2，写 `audit.delivery_log` 留交接点证据）？后者尚未设计 | 走统一投递服务（举证要交接点证据，直连拿不到）；在它建成前，开发机用假网关，真机只出现在 M-08 |
| ③ | **收件人与分级**：几级、谁收、短信还是只发邮件、夜间是否发短信 | 两级：`page`（源中断、封账落后、批次积压）短信 + 邮件；`notify`（其余）只邮件，每日 08:00 一封汇总 |
| ④ | **阈值**（各项建议值） | 源中断：话务 10 分钟源连续 3 次、参照 30 分钟源连续 2 次、日批源 1 次；批次未进 core 超 1 小时；封账落后 > 3 天（沿用）；副本过期 > `version_stale_days`（沿用）；待澄清最老一条 > 7 天；对账差 > max(2, 2%) |
| ⑤ | **WARN 的报法**：一些 WARN 会长期不为零（配置未补全前的未关联工号） | 只在「由 0 变为非 0」与「比上次增加」时报，并进每日汇总；不按每次构建报 |
| ⑥ | **告警里能否带工号**：工号算不算数据本体 | 不带，只带条数；工号在 M-03 / 待澄清里看 |
| ⑦ | **`meta.check_result` 保留期** | 90 天（举证另有 `audit.sla_*`，这张表只为排障与 M-08） |
| ⑧ | **Airflow 任务失败**本身是否也进这条通道（Airflow 3 可出 StatsD / OpenTelemetry 指标，要再加 exporter） | 第一批不接，源中断已由 `run_log` 覆盖；dbt 构建失败由 `oss911_unprocessed_batches` 间接覆盖 |

**下一步**：等 Mac 侧对方案与上表答复再开工。

## 2026-11-22  Mac 侧：第 7 步九处自定与三处需定的答复；监控告警通道批准（DD-100）；坐席 mart 三张表（DD-101）

Gavin 已同意，登记册与正文已更新：DD-98 注、DD-100、DD-101；LD-01 10.2、LD-03 第 5 章、LD-09 第 3 章与 4.3。

**第 7 步**
- 九处自定全部认可。第 2 条（去重比「该键上一次观测」）是对原契约的更正，已记入 DD-98 注。第 1 条（10 分钟一次、日终缺最后不到
  10 分钟）先接受，Q36 答复后再定是否 23:58 加取。
- (a) `midnight_skip_minutes` **不调，维持 30**。两种取值的日终值相同，差别只在凌晨的日内临时值。请加一项检查：同一归属日内、
  同一坐席的累计值**变小**（午夜跳过窗口之外）即 ERROR，并进告警（page）。平台按哪个时区切日，接真机第一天即可由此看出；
  raw 留着每次观测，归日规则要改时 staging 可重算。
- (b) 同意并入监控通道，按你方案第 5 条的截断法做。
- (c) `tools/check_refs.py` 已改：基线之外也读 `ddl/changes/*.sql`（新建的表与 `ADD COLUMN`）。两份契约的 `id` 请指回
  `fact_agent_*.agent_no`。

**监控告警通道：方案批准，按三块做。八项需定**
- ① 加 Alertmanager。请登记 `compliance/components.csv` 与 MANIFEST（连同 `prometheus_client`）；LD-09 正文我已补。
- ② 走统一投递服务；现在用假网关。接口即 Alertmanager 的 webhook 格式，将来接投递服务不改规则。
- ③ 两级照你的建议作默认；收件人做成配置（谁收、夜间是否发短信待运维方确认，不要写死）。`page` 增一类：监控自身失效。
- ④ 数值同意。两点：**新增的阈值也放进随发布走的配置并由导出程序导出**，规则文件里不出现数字；
  「待澄清最老一条超 7 天」只进每日汇总，不单独发。
- ⑤ ⑥ ⑦ ⑧ 同意。

**另加三条（防「没有消息被当成没有问题」），请并入实现与验证**
1. `meta.check_result` 的新鲜度作指标（距各构建 DAG 上次写入的时长），超时即报。dbt 崩溃时 on-run-end 不执行，
   表里停着上一次的「全过」。
2. 导出程序某项查询失败时，该项**不再输出旧缓存值**，并置 `oss911_monitor_query_errors`；规则对它与 `up == 0` 报 page。
3. 一条常亮的心跳告警持续经 Alertmanager 发往假网关；假网关侧的检查脚本在超过设定时长未收到时判失败。
   验证里请含一例：停掉 Alertmanager，检查脚本须报出。

**监控之后：坐席 mart（DD-101，LD-03 第 5 章末段）**
三张表：`mart.agents__day_agent__duty`、`mart.agents__day_hour__on_duty`、`mart.agents__day_agent__state`。要点：
跨午夜区间按自然日切开；未签出区间算到该日最后一次观测；小时表的人数是去重计数、`hour` 为 required_dimensions；
占用率的分子分母两列仅在三个输入都非空时有值；封账**不要求回扫**（D 日结束后第一次夜间构建，条件为两源该日批次全进 core
或已丢弃）；读取规则与话务 mart 一致。M-R01 至 M-R04 的 `mart_table / mart_column / numerator / denominator` 请按 DD-101 填。
列名、结构变更编号、封账条件在 dbt 里的具体写法由你定，写不通处列需定。

**Mac 侧接下来**：第 10 步设计（门户、话务报表、Superset 去留），先与 Gavin 对答案再写。

## 2026-11-22  Mac 侧：评审三条采纳（动工前先看这一节）——少返回工号登记不隔离、最后确认时刻、预期检查清单；上一节有五处随之更正

评审意见 Gavin 已转来，两条 P1 对着代码核过，属实。已记入 DD-98 评审注、DD-100 评审注，DD-101 同日更正一处；ICP 问题单加 Q40。
**上一节（同日）与本节不一致处，以本节为准。**

**一、请求的工号没有返回（agent.py `check_records`）**
- 核对请求集合与返回集合。缺的工号**登记，不隔离**（Gavin 定）：请求里含当日已删除的坐席，平台若对其合法省略，整批隔离会让
  每十分钟一批全部隔离、坐席数据停下来等人。
- 该批照常接入已返回的记录。每批留一行只增的覆盖记录：请求的工号、返回的工号、观测时刻。
- 监控指标：「最新可信快照里仍在册而未返回」的工号数；连续多次未返回即告警（次数作阈值，建议 3）。已删除坐席的缺失只进每日汇总。
- 响应不是数组等结构性错误，仍整批隔离，不变。空数组按「全部缺失」登记，不隔离。

**二、`src_observed_at` 不是最后确认时刻**
- 它保持原义：内容最后变化的时刻。
- 两张 core 表各增 `src_confirmed_at`：按工号与归属日，取**返回集合里有该工号**的最后一个未隔离、未丢弃批次的观测时刻，
  由第一条的覆盖记录导出；不改 raw 旧行。
- 凡是要「数据截至何时」的地方都用它：对账截止点；DD-101 里未签出区间算到何时（原写「最后一次观测时刻」，已更正）。
- 对账降为提示级：notify、带容差、进每日汇总；不作封账条件，不发短信（请求时刻不等于平台报表的截止时刻，待 Q36）。

**三、预期检查清单（随发布维护）**
- 列每个构建任务与每项检查：周期、允许延迟、启停。导出程序逐项对照，分别输出执行状态
  （正常 · 失败 · 报错 · 跳过 · 从未运行 · 过期）与差异数量。报错、跳过不折算成 0。
- 新部署的项自部署时刻起算宽限期，到期仍无结果即报。
- 必需的指标或阈值缺失：规则用 `absent()` 兜住，不得悄悄不生效。
- 上一节「另加三条」的第 1 条（新鲜度）由此清单逐项判定，不按整个 DAG。

**四、上一节的三处措辞更正**
- 「规则文件里不出现数字」→「业务阈值不重复硬编码」。`up == 0`、窗口长度、防抖时间写在规则里。
- 每日汇总**不靠** Alertmanager 的 repeat_interval。由定时作业每日 08:00（Asia/Riyadh）生成，范围为前一个自然日，
  含当日已恢复的问题，经同一条投递路径发出。「WARN 增加时通知」另走 Alertmanager，比较相邻两次结果。
- 心跳的检查者：定时任务，不是验收时手工跑的脚本；**不依赖总部或总包商的监控平台**（Gavin 定）。生产两节点互查，
  失联时不经 Alertmanager、直接经投递端通知。开发机单节点：systemd 定时任务加假网关；runbook 写明单节点下
  「整机宕机无人发现」是已知局限。

**顺序建议**：第一、二条先做（修的是已上真机的第 7 步），再进监控三块。覆盖记录放哪张表、清单的文件位置与格式由你定，
写不通处列需定。

## 2026-11-22 开发机：Mac 评审第一、二条已实现（缺失工号登记不隔离、src_confirmed_at）；(a) 的变小检查；(c) 契约指回 agent_no

按 Mac 两节答复（以评审那节为准）先修已上真机的第 7 步。临时库端到端验过；待真机部署（runbook 20 末节「补丁」）。

**做了什么**
- 结构变更 **0016**：`raw.icp_agent_coverage`，每个 hindex/agent 请求一行——源、归属日、观测时刻、请求的工号、返回的工号、
  缺失数；只增，按 `ingested_at` 月分区，外键到批次。两张 core 表增 `src_confirmed_at`（旧行取 `src_observed_at`）；
  区间表加 `(agent_no, date_key)` 索引
- 接入程序 **0.3.0**：每批写一行覆盖记录；缺失的工号登记、不隔离，批次行与 run_log message 写明；空数组即全部缺失，
  不隔离；响应不是记录数组仍整批隔离（不变）
- 契约 **v2**（两份）：`id` 指回 `fact_agent_*.agent_no`（check_refs 已能读 ddl/changes，校验通过）；quality 记缺失登记、
  变小检查；integration_checks 加 Q40
- dbt：两个 core 模型 post-hook `confirm_agent_facts` 推进 `src_confirmed_at`；新测试 **`agent_day_totals_decrease`**（ERROR）
  入构建与夜间 `full_checks`
- 假接口：日累计十四项只增不减（见下第 6 条）；新注入 `agent_missing`

**验证（临时库）**
- 缺失：注入 `agent_missing` → 批次 `received 58`、覆盖记录 `59 / 58 / missing 1 {101}`、无隔离、run `success`；
  dbt 后 101 的 `src_confirmed_at` 停在它上一次被返回的时刻，其余前进
- 确认时刻：冻结假接口时钟连跑两次，第二次 raw 写 0 行；dbt 后日累计 59 行、区间 118 行全部 `src_confirmed_at > src_observed_at`
- 变小：手工造一条更晚、签入时长少 600 秒的观测 → core 先写入（`INSERT 0 1`），测试随后 FAIL 1（ERROR），不挡 core；
  清掉后 PASS
- 权限验证 187 项全过（+9：覆盖表只增、外键、dbt 只读并可推进确认时刻）；接入单元测试 108、假接口测试 28 全过；
  dbt build（除 mart）只有 `mart_closing_not_behind` 因临时库数据陈旧而 FAIL，与本次无关

**开发机自定**（请 Mac 侧过目）

| # | 定了什么 | 为什么 |
| --- | --- | --- |
| 1 | 覆盖记录放 **raw**（`raw.icp_agent_coverage`），不放 meta、不加到 `meta.ingest_batch` | 它是「源端这一次给了谁」的原样记录，与 raw 同语义（只增、接入写、dbt 读、按月分区与留存）；`ingest_batch` 是各源通用的批次表，塞两千个工号的数组不合适 |
| 2 | 「返回」= 请求里有、响应里有记录的工号（值全为 null 也算）；返回了没请求的工号仍整批隔离，不计入返回 | 与评审第一条一致；多返回说明请求与响应对不上，是另一回事 |
| 3 | 区间的确认：该工号在「签入日」到「`src_observed_at` 所在日」之间某天被返回的最后时刻 | 接口只告诉我们返回了哪些工号，不告诉返回了哪些区间。跨日仍在线的区间，次日若平台不再返回（Q38），不能拿次日的返回当确认；次日若照样返回且内容在变，`src_observed_at` 已到次日，次日的返回才算 |
| 4 | `src_confirmed_at` 不计入内容哈希，推进时不改 `last_change_at` / `built_at` | 它不是内容；只在时刻确实前进时写（DD-78） |
| 5 | 变小检查**在 `fact_agent_day` 之后跑**（`depends_on`），查近两天；值变为 null 不算变小 | 挂在 staging 上，ERROR 会让 dbt 跳过 core，日累计停下来；null 是「没有这一天」（Q36）。代价：一次变小会让 build 任务红到它滑出两天窗口——监控通道建成后由 `meta.check_result` 报 page |
| 6 | 假接口日累计改为只增不减：通话、事后处理、保持按「不快于可用时间增长」平滑，空闲为余量；示忙不超过签入减休息；呼入数与呼入时长仍与假话单逐条一致。另修腿缓存：按分钟取到分钟末、再按 CALLEND 过滤，不再取决于这一分钟第一次被问的时刻 | 旧假接口的空闲是余量，一通电话结束、通话时长一次加上去，空闲就变小——新检查会天天报。缓存问题在一分钟内两次请求时出现，实测 139 号坐席空闲少 31 秒 |
| 7 | dbt 变量 **`agent_day_totals_checked_from: 20261123`**：变小检查只查此日起 | 真机 11-22 的日累计来自旧假接口，夜间全量检查会永远报这几条；生产没有更早的日累计，不受影响。按内容精确屏蔽，不放宽检查本身 |

**留给监控那一块**：「最新可信快照里仍在册而未返回」的工号数与连续未返回次数（评审第一条的指标与告警）由导出程序从
覆盖记录算，随监控第二块做。

**下一步**：Gavin 部署本补丁（40 → 30 → 45 → 50，重启假接口）；同时开工监控通道第一块。

## 第 7 步补丁真机部署：变小检查截止日更正（2026-11-23）

Gavin 已部署 19c326d（40 → 30 → 45 → 50，17:02 重启假接口）。验证结果：覆盖记录两个来源各 5 次请求、`missing`
为 0；今天的区间 `confirmed_later` = 60（上午已签出的那段，内容不变而确认时刻跟到最新请求），日累计为 0（60 人都在签入，
每次内容都变）——均符合预期。

**问题**：部署后 `build_icp_agent_day` 从 17:00 起每次失败（重试两次，约 4.5 分钟）。core 照常写入（检查排在
`fact_agent_day` 之后，设计如此），失败的是 `agent_day_totals_decrease`。原因是我定的截止日 20261123 错了：真机
11-23 当天 17:02 之前的日累计仍出自旧假接口，且 17:02 换成新假接口时空闲等项会比旧值小，所以 11-23 必然有变小记录。
上一节第 7 条「真机 11-22 的日累计来自旧假接口」的判断漏了部署当天。

**更正**：`agent_day_totals_checked_from` 改为 **20261124**（dbt_project.yml），只需重跑 50。生产不受影响。

**留给监控第一块**：检查失败时 Airflow 照默认重试两次，对确定性的测试失败没有意义，且每 10 分钟一次 ERROR 会让
构建 DAG 一直显示失败。待 check_result 落库后再定：构建任务里的测试失败是否不重试、只经监控通道上报。

## 2026-11-23 开发机：监控通道第一块已实现（DD-100）——测试结果落库、只读角色、未关联话单腿索引、日累计对账；待真机部署

**内容**：结构变更 0017；dbt `on-run-end` 宏 `macros/check_results.sql`；新测试 `agent_day_reconciles_with_legs`（WARN）；
`maintenance_partitions` 增任务 `drop_expired_check_results`；`maintenance_checks` 加入对账测试；40 在执行结构变更前建
`oss911_monitor`；权限验证 187 → 222 项；runbook 21 第一节。

**临时库验证**
- 落库：一次 `build` 写 12 行（2 模型、10 测试）；一次 FAIL 写 `fail / error / 6`；SQL 报错的测试写 `error`、`failures` 为空；
  模拟 Airflow 环境变量后 `dag_id / task_id / dag_run_id / try_number` 正确，含单引号的 run id 无误
- 对账：灌入今天的话单与日累计，临时把今天标为已封账：59 名坐席呼入合计 2382，与截至 `src_confirmed_at` 的接通坐席腿
  2382 逐人相符；容差设 0 仍 PASS；两名坐席各加 3 后 WARN 2。临时标记已删
- 保留期：建 2026-01 至 08 的分区后执行，删 7 个（1–7 月），留 8 月与本月；传 30 天被拒
- 三个构建 DAG 的选择里都不含对账测试；夜间检查一套里只有临时库数据旧导致的 `mart_closing_not_behind` 失败（已知）

**开发机自定（请 Mac 侧过目）**

| # | 自定 | 理由 |
| --- | --- | --- |
| 1 | **`oss911_monitor` 由 40 在执行结构变更前建**（同 0002 的属性检查：NOLOGIN、无特权属性，属性不符即失败、不改），0017 只授权 | `changes.yml` 规定 0003 起一律以属主角色执行（DD-74），属主角色不能建角色。另一个办法是让 0017 以超级用户执行，那要改「仅 0000–0002」这条规则，我没动 |
| 2 | `meta.check_result` **模型与测试都记**，一行一节点一次调用；带 Airflow 上下文（`dag_id`、`task_id`、`dag_run_id`、`try_number`，来自 BashOperator 给的 `AIRFLOW_CTX_*`）；parse、compile、ls 等不记 | 预期检查清单要按「构建任务」与「检查」两类逐项判；模型的 skipped（上游失败）也是一种状态。重试第几次可分辨「重试后才过」 |
| 3 | dbt 少见状态并入六种：runtime error、partial success → error；no-op、reused → skipped，原状态写在 message 开头 | 表上有 CHECK；绝不并入 pass / success |
| 4 | 报错或跳过的测试 `failures` 为空 | 评审第三条：报错、跳过不折算成 0 |
| 5 | message 截 500 字。dbt 测试的 message 只有行数；模型报错是数据库错误原文，可能引用某个值 | 表只给运维与将来 M-08 看，不进告警（告警只带条数，需定 ⑥） |
| 6 | 按 `checked_at` 月分区（UTC 月界，同 raw）；分区函数放开 meta（非分区表照样拒绝）；钩子先建当月分区再插入 | 缺分区会让钩子失败，连带整条 dbt 命令失败 |
| 7 | 保留期函数 `meta.drop_expired_check_partitions(keep_days)` **只认这一张表**，少于 90 天拒绝；整月过期才删，所以至少留 90 天；dbt 变量 `check_result_keep_days: 90` | 编排手里不该有一个能删任意分区的通用函数（raw、core 的保留期各有 DD-38 的规定） |
| 8 | 监控角色的授权：meta 十张登记表整表可读；raw 覆盖记录与 core 四表**只授列**（工号、日期、`agent_sk`、`src_confirmed_at`；话单腿另有 `device_type_code`）。第二块若要更多列，另开结构变更 | 「只读用到的」，并在列上兑现需定 ⑥（不读数据本体） |
| 9 | 对账的「已封账」= `mart.legs__day_agent__handling` 有封账版本；话单腿口径：设备类型 2、`device_no` = 工号、有 `call_begin`、方向不是 outbound / internal、`date_key`（`wait_begin` 归日）同日、`call_end` ≤ 日累计行的 `src_confirmed_at`；范围最近 7 天；容差与天数在 dbt 变量 `agent_reconcile` | 腿表封账即该日话单含回扫已定稿。腿在离开设备时可见（`call_end`），与平台「当日至今」同一截法。方向为空的（如 CALLTYPE 不在值表）算呼入 |
| 10 | 对账测试不引用模型（同 `mart_closing_not_behind`），只在 `maintenance_checks` 每晚跑 | 每 10 分钟的构建跑它没有意义，且 dbt 的间接选择会把它拉进两个构建 |

**留给第二块**
- 预期检查清单的文件与格式、新鲜度、`oss911_monitor_query_errors` 等都在导出程序里做
- 构建任务里**测试失败也被 Airflow 重试两次**（今天变小检查误报时每次 4.5 分钟）。建议：构建 DAG 的 dbt 命令区分退出原因，
  只有测试失败、没有模型报错时不重试、任务标失败，由监控通道上报。dbt 的退出码分不出这两种，要读 `check_result` 或
  dbt 的结果文件。做法放到第二块与清单一起定

**下一步**：Gavin 部署第一块（40 → 45 → 50，runbook 21 第一节）；我开工第二块。

## 2026-11-23 开发机：监控通道第二块已实现（DD-100）——导出程序 oss911-monitor、预期检查清单、告警规则；待真机部署

### 先记一件事：源日 11-15 的回扫缺口（第二块告警的实例）

第一块部署后手工跑 `maintenance_checks`，`mart_closing_not_behind` 失败 5 行：话务 5 张 mart 表封账停在 11-14。
查下来，11-16 凌晨 04:55–05:29 假接口没在跑（24 次 `Connection refused`），那天 02:00 UTC 的回扫三次尝试都失败，
源日 11-15 从此没有回扫。封账只能按日期顺序走，11-15 卡住，后面每天都封不了；`maintenance_checks` 至少 11-22、
11-23 两晚是红的，没人看到。Gavin 清掉那次失败的 run 重扫后，夜间发布一次封到 11-22。这正是「没有消息被当成没有
问题」：本块的 `OSS911ResweepMissing`（某源日到期未回扫进 core，page）当天早上就会响。

### 内容

- `src/ingest/src/oss911_ingest/monitor.py`：接入程序包第二个入口 `oss911-monitor`，包版本 0.4.0；`config.py` 加
  `monitor_settings`；单元测试 13 个（总计 121）
- `contracts/monitoring.yml`：随发布走的监控配置——业务阈值、各源阈值与模型、预期检查清单 15 项；进发布目录
- `infra/files/monitoring_install.py`：安装时合并 `deployed_at`（已有的保留、新增的记当时），并用导出程序自己的规则校验
- `infra/playbooks/55-monitor.yml`（新）：monitor.conf、监控配置、systemd 单元、启动，以 oss911mon 身份跑一轮
  `--once`、取 /metrics、查 Prometheus 已采集
- `infra/files/prometheus/alerts.yml` 22 条规则、`alerts_test.yml` 规则单元测试；22 部署前用镜像自带 promtool 检查并测试
- inventory：登录用户 `monitor`（事务池、`statement_timeout` 10s、系统账号 oss911mon）、`monitor_port` 9188；
  pgBouncer HBA 与 `db_login.yml` 把 `oss911_monitor` 归入事务池
- 顺带修三处：HAProxy 采集目标一直 down（只绑回环、没开 /metrics）；6 个 systemd unit 的 `StartLimitIntervalSec`
  写在 `[Service]` 被忽略，挪到 `[Unit]`；构建 DAG 与 `maintenance_checks` 不再重试
- runbook 21 第二节

### 临时库验证

- 以 `oss911_monitor` 角色的登录用户跑一轮：11 项查询全过；数据库连不上时 11 项全置错误、阈值照常导出
- 注入 3 条覆盖记录（一个在册工号、一个已删工号，连续 3 次未返回）：在册 1、已删 1；临时行已删
- 临时库未跑回扫：`oss911_resweep_missing_days` 7、首个缺日 11-16，与实际相符
- HTTP：/metrics 200、内容类型 0.0.4、267 个样本；/nope 404；绑一个本机没有的地址也能起（IP_FREEBIND，无需特权）
- 规则：promtool 22 条通过；单元测试 5 组通过，改坏一个期望值即报 FAILED
- DAG 导入：构建与夜间检查 retries 0，发布与分区维护仍为 2

### 开发机自定（请 Mac 侧过目）

| # | 自定 | 理由 |
| --- | --- | --- |
| 1 | **不引入 `prometheus_client`**，文本格式自己输出（约 40 行，有单元测试） | 只用到 gauge；少一个依赖就少一次联网取 wheel、许可登记与八年维护。所以本块无需登记组件（Alertmanager 在第三块登记） |
| 2 | **部署放新剧本 55-monitor**，不放 30：30 只装程序（同一 venv）；配置、单元、启动与验证在 55 | 监控配置来自发布目录，由 45 建出，晚于 30；登录与凭据由 40 建，也晚于 30 |
| 3 | 登录用户 `monitor` **走事务池**（同服务层），`statement_timeout` 10s，自己的系统账号 oss911mon | 每项查询一个短的只读事务，无会话状态 |
| 4 | **监控配置放 `contracts/monitoring.yml`**，与 pools.yml、resweep.yml 并列进发布目录；装成 `/etc/oss911/monitoring.yml`；导出程序按修改时间自动重读，换发布不用重启 | 「随发布维护」；不新开发布目录下的一级目录 |
| 5 | **源清单、周期、回扫起始日取自 DAG 清单 dags.json**，不在监控配置里再抄一份 | 一个权威 |
| 6 | 清单状态在评审六种之外**加三种**：`warned`（WARN 有差异）、`grace`（宽限期内未运行）、`disabled`；dag 类取该 DAG 最近一次 dbt 命令里**最差**的状态 | WARN 不该算失败也不该算正常；宽限与停用要能从指标上看出来 |
| 7 | 接入 DAG 不进清单，由 run_log 的源指标覆盖；`maintenance_partitions` 不进清单（run-operation 不写结果），改看 `oss911_partition_days_ahead` | 清单只收写 `check_result` 的东西；分区本身才是要看的 |
| 8 | **回扫缺日**：从回扫起始日到「源时区现在减 6 小时的前一天」，每个源日须有成功且全进 core（或已丢弃）的回扫，口径同封账宏 | 11-15 这类缺口当天可见，不等三天后的封账落后 |
| 9 | **坐席未返回**：每名坐席在最近 N 次「请求了它」的观测里都没返回才计（N = 阈值 3），按 dim_agent 当前版本分「在册 / 已删」；在册的 notify，已删的只进汇总 | 评审第一条；按请求而不是按批次计，同一工号只在一个批次里 |
| 10 | 级别：ERROR 测试失败（任何 DAG）page；dag 类 `error` page；`stale / never_run` page（for 5m）；`skipped` notify；WARN 比上一次增加 notify | 评审：「报错、跳过不折算成 0」；WARN 不按每次构建报 |
| 11 | 构建 DAG 与 `maintenance_checks` **retries 0**；发布与分区维护保留 2 次 | 第一块留下的问题。构建按源周期自然重跑；确定性的测试失败重试无益 |
| 12 | HAProxy 统计页加绑容器网关地址（`transparent`）并开 `/metrics`（HAProxy 内置的 prometheus-exporter） | 原采集目标从第 6 步起一直 down，因为没有规则所以没人发现 |

### 需定

| # | 问题 | 建议 |
| --- | --- | --- |
| ① | **封账落后的 3 天写了两处**：监控配置 `closing_lag_days` 与夜间测试 `mart_closing_not_behind` 里的 3 | 留下测试做夜间兜底，但改成读 dbt 变量，并在监控配置注明两处须一致；或者删掉测试，只靠告警。倾向前者，第三块里一起改 |
| ② | 告警现在只显示在 Prometheus 页面上，第三块的 Alertmanager 上线前不发往任何地方 | 按原定顺序，第三块紧接着做 |

**下一步**：Gavin 部署第二块（21 → 40 → 30 → 45 → 50 → 22 → 55 → 60，runbook 21 第二节）；我开工第三块
（Alertmanager、假网关、心跳定时检查、每日汇总）。

## 2026-11-24 开发机：监控通道第三块已实现（DD-100）——Alertmanager、假网关、心跳检查、每日汇总；待真机部署

第二块真机未部署前，Gavin 贴来 `meta.check_result`：落库正常，`maintenance_checks` 04:30 一次 7 项全过（11-15 缺口清掉后封账已追上）。
两条常驻 WARN 查明为老数据：坐席 102 删除后的 53 条腿（10-27、10-28，见 10-28 节）；3 通缺首腿（10-19 至 10-21，首腿一个多月未到，
不会自愈）。由此发现 `fact_call_leg_without_first_leg` 只返回一行汇总，`failures` 恒为 1，「WARN 增加」对它永远不响——本块改为每通一行。

### 内容

- **Alertmanager** v0.34.2（容器，digest 钉死，单实例、关闭集群通信）；配置 `infra/files/alertmanager/alertmanager.yml.j2`，
  22 部署前用镜像自带 amtool 检查。按级别三路：heartbeat 每分钟、page 重发 4 小时、notify 24 小时；同名告警合成一条
- Prometheus：`alerting` 指向 `alertmanager:9093`，采集 Alertmanager，外部标签加 `prometheus`（心跳按它区分来源）
- **假网关** `test/fake_gateway/server.py`（标准库，系统 python3，账号 oss911gw，56 部署为 `oss911-fake-gateway.service`）：
  接口 `/alertmanager`、`/direct`、`/heartbeat`；不发送，按收件人与渠道写 `outbox.jsonl`，原始消息写 `received.jsonl`
- **收件人配置**：inventory `alert_recipients` → `/etc/oss911/alert-recipients.json`（级别、渠道、夜间不发短信），开发机只有占位
- **`oss911-alertpath`**（接入程序包第三入口，0.5.0）：`heartbeat-check`（每分钟）与 `digest`（每日 08:00），各写状态文件，
  导出程序导出为 `oss911_alertpath_*`；规则 5 条（停跑、无状态、连不上投递端为 page；汇总缺失或失败为 notify），规则共 27 条
- **剧本 56-alerting**（新）：假网关、`alertpath.conf`、两个定时器；验证心跳到达、经 Alertmanager 的测试告警进 outbox、汇总可生成
- 需定 ①（封账 3 天写两处）：测试改读 dbt 变量 `closing_lag_days`，55 部署时核对它与监控配置的同名阈值，不等即拒绝
- `compliance/components.csv` 与 MANIFEST 登记 Alertmanager；runbook 21 第三节

### 临时库验证

rootless 起 Prometheus（真规则文件）、Alertmanager（真配置）、假网关，加 0.5.0 导出程序连临时库：
- 心跳约 1 分钟后到达假网关；测试告警经 Alertmanager 进 outbox（notify，只邮件）
- **停掉 Alertmanager**（08:44:14）：检查任务每 30 秒跑一次，08:48:47 发出 `path_failure`（page，邮件加短信）一次，之后不重复；
  重启后下一次检查发 `path_recovered`，积压告警随即补发
- 按「告警加位置」分组时一次补发了 20 条短信（5 张表封账落后、6 项检查从未运行、4 个源停滞）；改为按告警名分组后为 5 条
- 汇总：临时库数据旧，16 条 page 告警逐条列出时段与「仍在响」；待澄清最老 25.8 天标出超 7 天；覆盖率 1.4% 并注明「无法担保」。
  发现导出程序无数据时汇总写「All normal」「no open item」——已改：各节先确认导出程序对应查询当时无错，否则写 NO DATA
- 单元测试：接入程序 138 个（新增 15 个汇总与心跳、1 个状态指标、1 个封账阈值核对）；假网关 4 个；promtool 27 条规则、7 组测试
- dbt：两项改动的测试在临时库编译运行；首腿测试每通一行

### 开发机自定（请 Mac 侧过目）

| # | 自定 | 理由 |
| --- | --- | --- |
| 1 | Alertmanager **只按级别路由**，一个 webhook；谁收、哪个渠道、夜间是否发短信由**投递端**按收件人配置决定 | 收件人属投递服务（LD-01 10.2）；Alertmanager 里按人建 receiver，改人要重启它，生产投递服务接手时还得拆 |
| 2 | **同名告警合成一条**（`group_by: [alertname]`），标题写第一处并注明「另有几处」 | 演练里一次补发 20 条短信 |
| 3 | 投递端接口定为三个：`POST /alertmanager`（webhook v4）、`POST /direct`（`kind level source subject body`）、`GET /heartbeat?source=` | 假网关与将来的投递服务同一接口，上游不改；见需定 ① |
| 4 | 心跳检查与每日汇总作为接入程序包**第三入口** `oss911-alertpath`，以 oss911mon 身份由 systemd 定时器运行；不用 Airflow | 心跳检查要在 Airflow 停摆时照样跑；汇总与导出程序同属监控 |
| 5 | 心跳超过 **300 秒**未到即判断链路断，page 直发；持续时**每 4 小时**再发，恢复时发一次；数值在 inventory | 每分钟一次心跳，5 分钟容得下 Alertmanager 重启与一次投递重试 |
| 6 | 检查任务与汇总**写状态文件，由导出程序导出、规则盯住**；状态文件缺失 15 分钟也报（absent） | 「检查者」自己停了也要有人知道：链路盯检查者，检查者盯链路 |
| 7 | **汇总只读 Prometheus**，不连库；所以不新增授权，权限验证仍 222 项 | Prometheus 有当天每分钟的告警与指标（留存 15 天），一个来源 |
| 8 | 汇总五节：告警（含已恢复、时段、分钟数）、WARN（变化的列前后值，不变的一行写自何时未变，回看 14 天）、只进汇总的三项、日终不正常的预期检查、覆盖率；任一节读不到则标 INCOMPLETE；无数据写 NO DATA；同一天只发一次 | 评审第四条「含已恢复的问题」；「没有消息不等于没有问题」 |
| 9 | 汇总的首个状态在安装时写入，`OSS911DigestMissing` 从安装算起（26 小时） | 否则装好到次日 08:00 之间无法区分「未到时间」与「没跑」 |
| 10 | 告警与汇总的文字用**英文**（交付代码的规则） | 见需定 ③ |
| 11 | 56 在开发机上经 Alertmanager API 发一条测试告警（2 分钟后自动结束），确认进了 outbox；生产不发 | 「告警可送达」要有实测，不只看进程在跑 |
| 12 | 22 的重启改为**各组件只因自己的变化重启**（原来任一 Quadlet 变化就三个全重启） | 加 Alertmanager 时不该连带重启 Keycloak |

### 需定

| # | 问题 | 建议 |
| --- | --- | --- |
| ① | **统一投递服务的接口**：以假网关的三个接口为准吗？收件人配置归投递服务管（M-?），还是继续由部署剧本写文件？ | 接口照假网关；收件人将来进 M- 管理界面，现在由 inventory 写 |
| ② | **夜间短信**：默认 22:00–07:00 不发短信，page 级也不发（`sms_at_night: false`）。运维方未确认前，夜间 page 只有邮件 | 运维方确认值班安排后改 inventory |
| ③ | **告警与汇总的语言**：现为英文。是否需要阿拉伯文或中英双语？ | 先英文；若要阿拉伯文，在投递端做模板，不改规则 |
| ④ | **生产两节点心跳互查**：两台 Prometheus 各配一套 Alertmanager 还是组集群；各节点 `heartbeat_source` 指向对端 | 归 LD-08；开发机单节点，整机宕机无人发现是已知局限（runbook 21 第三节） |

**部署后预期多一封 notify 邮件**：首腿测试改为每通一行后，真机的 `failures` 由 1 变 3，`OSS911WarnIncreased` 会响一次。

**下一步**：Gavin 部署第三块（30 → 45 → 50 → 22 → 55 → 56，runbook 21 第三节），含停掉 Alertmanager 的验证；我开工坐席 mart（DD-101）。
