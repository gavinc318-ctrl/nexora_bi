# 报表定义（DD-102）

每张报表或仪表板一份 `<编号>.yaml`。门户按定义通用渲染，**新增或下架报表不改前端代码**。

- 46-metrics 部署时校验并同步进 `meta.report_definition`（结构变更 0018），与 `metrics/` 同一次、同一事务。
  一份有错，整次同步都不做
- 服务层 `GET /v1/reports` 给当前角色可见的目录，`GET /v1/reports/{编号}` 给单张定义。无权、未发布、不存在三种情况
  同为 403 `report_not_permitted`，都写审计——拒绝的答复里看不出有哪些报表
- 定义只说「查什么、怎么摆」。数据仍由 `POST /v1/query` 取，受同一套权限约束：拿到定义不等于拿到数据
- 注释写中文（口径侧的 YAML），键与取值用英文

## 字段

| 键 | 说明 |
| --- | --- |
| `id` | `R-nn`（报表）或 `D-nn`（仪表板），与文件名一致；编号即 LD-05A 的编号 |
| `kind` | `report` 或 `dashboard`，与编号首字母一致 |
| `domain` | `calls` · `incidents` · `resources` · `performance` · `operations`，目录分组用；显示名在门户的语言文件里 |
| `title` | `{en, ar}`。阿语未出时 `ar: null`，`title_ar_state: draft`，界面先显示英文并标草稿 |
| `title_ar_state` | `draft` 或 `confirmed` |
| `roles` | 可见角色。**必须是它读的每张 mart 表可读角色的子集**（`metrics/marts.yaml`），否则 46 拒绝：目录里看得见、一查就 403 是不允许的 |
| `published` | `false` 时目录里没有、打开即 403，定义留在库里 |
| `defaults` | `range`（`today` · `yesterday` · `last_7_days` · `last_30_days`）、`data_version`（`latest` · `as_closed`）。`today` 只能配 `latest` |
| `refresh` | `{mode: on_open}`，或 `{mode: interval, seconds: n}`（n ≥ 60，读临时数，须 `latest`） |
| `print` | `{orientation: portrait \| landscape}`。打印 A4，表头跨页重复 |
| `source_note` | `{en, ar}` 或 `null`：页面常驻、随页脚打印的来源标注 |
| `filters` | `[{dimension, roles}]`：用户可筛选的维度。每个查询的表都要有这个维度；`roles` 省略为报表的全部角色，给了只对这些角色出现（R-04 的坐席筛选只给主管） |
| `formats` | 指标编码 → `integer` · `decimal` · `percent` · `duration`（秒）。没写的按聚合方式：count / sum 为 integer，ratio 为 percent，avg 为 decimal |
| `queries` | 名 → `{metrics, dimensions, granularity}`，各是一次 `POST /v1/query`：一个查询的指标必须在同一张 mart 表；维度是该表的维度，表的必选维度（`required_dimensions`）必须在内；`granularity` 默认 `day` |
| `views` | 按顺序显示的表与图，见下 |

### 表（`kind: table`）

`queries`：一个或几个查询，按它们**相同的**维度拼成一张表（R-01 是四个查询按日拼起来）；`columns`：指标列的顺序，
省略为各查询的全部指标。维度列在前，由门户按维度显示。

### 图（`kind: chart`）

| 键 | 说明 |
| --- | --- |
| `query` | 一个查询 |
| `chart` | `bar` 或 `line` |
| `stack` | `none`（默认）· `stacked` · `percent`，只用于柱图 |
| `horizontal` | 横向条图 |
| `x` | 横轴维度 |
| `split` | 可选：按这个维度的成员分出多条系列（R-09 按队列、R-05 按挂断方）；给了 `split` 只能有一个指标 |
| `series` | 指标，省略为查询的全部指标 |
| `sort` | `{by: 指标, order: asc \| desc}` |

`x` 加 `split` 必须恰好是该查询的维度。每个查询至少被一个视图用到。`title` 可选，`{en, ar}`。

## 现有定义

七张框架样例（DD-102 第七条，LD-05A 2026-11-25 规格）：R-01 · R-04 · R-05 · R-09 · R-21 · R-26 · D-06。
