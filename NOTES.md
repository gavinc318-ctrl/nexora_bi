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
> - 分区去重性能 → LD-10 TC-ING-01 待验证。CALLID 唯一范围**维持待答复**（IR-45 第五项 / ICP 问题单 Q21），未确认过；
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
>   `statement_timeout` / `idle_in_transaction_session_timeout`；**回扫维持必需项**；LD-10 加 TC-ING-02。
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
