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
