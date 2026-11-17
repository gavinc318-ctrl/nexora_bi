# 第 19 步 —— 服务层：查询 API 上线（DD-99 · LD-04 3.5）

## 这一步在做什么

装上并启动查询 API：`oss911-svc` 服务，监听 127.0.0.1:8090。四个接口：

| 接口 | 做什么 |
| --- | --- |
| `POST /v1/query` | 按指标查数：指标、维度、过滤、日期范围（左闭右开）、粒度（日 · 周（周日起）· 月 · 季 · 年）、数据版本 |
| `GET /v1/metrics` | 指标字典：名称（按语言）、版本、是否已实现、我能不能读 |
| `GET /v1/dimensions/{维度}/members?metric=…` | 某指标所在表的维度成员，只在我获准的范围内 |
| `GET /v1/me` | 服务把我的令牌解析成了什么：角色、工号、部门、每张表我能看到的范围——排查「为什么看不到」用 |

接口的权威定义是仓库里的 `contracts/openapi/v1.json`（由代码生成，60 部署时核对两者一致，不一致就不启动新版本）。

## 先懂四件事

### 一、每个请求怎么走

验令牌（签名、aud、iss、过期）→ 定唯一的业务角色 → 读注册表与读取规则 → 解析一次权限上下文 → 按发布登记定每天读哪一版 →
读行、过滤、汇总、算比率 → **写审计并提交** → 才返回。审计写不进去，请求就失败（`audit_unavailable`），结果不发出去
（DD-99：审计不可降级）。

从读注册表到读元字段，都在**一个** REPEATABLE READ READ ONLY 事务里，读的是同一份快照：每小时的临时数发布会删掉再重写
未封账日的行，分开读会出现「已发布却没有数据」（DD-99 注 2026-11-17）。

谁：令牌的 `sub`（Keycloak 里用户的固定 id）。部门映射表和审计都按它记，用户名只作显示——改名不换人，旧用户名给了别人也
继承不到旧映射。令牌验过了（知道是谁）的请求，被拒绝也记审计，包括没有业务角色、角色不止一个（`role_code` 记 `none`）和
请求格式不对（不记请求里的取值）；令牌本身无效的只进服务日志。

### 二、拒绝都有自己的错误码，从不拿空结果代替

| 码 | 含义 |
| --- | --- |
| `unauthenticated` 401 | 没有令牌、令牌过期、签名或 aud 不对、没有 sub |
| `role_missing` · `role_ambiguous` 403 | 令牌里没有四个业务角色中的任何一个 · 有不止一个（一个用户只许一个业务角色：各角色能看的范围不嵌套） |
| `table_not_permitted` 403 | 我的角色不能读这张表（如调度员读坐席处理表，接警员读中心级的表） |
| `identity_unmapped` 403 | 要「只看本人」，但令牌里没有工号、或工号对不上坐席、或对上多个 |
| `field_not_permitted` 403 | 请求了无权看的字段（如接警员请求坐席姓名） |
| `department_unmapped` 403 | 要按部门过滤（只对调度员与主管），但令牌里没有部门、映射表里也没有（按 sub 查），或不是有效编码 |
| `metric_unknown` · `metric_not_implemented` · `metric_version_unknown` 404 | 没有这个指标 · 登记了但没有 mart · 没有这个口径版本 |
| `dimension_invalid` · `incompatible_grain` · `metric_version_boundary` · `result_too_large` · `request_invalid` 400 | 请求本身的问题 |
| `timeout` 504 · `audit_unavailable` 500 | 单次查询超过 10 秒 · 审计写不进 |

查到而确实为零，是成功响应，`meta.empty_reason = no_data`。

### 三、数据版本

`data_version`：`latest`（默认，每天最新一版，含临时数）、`as_closed`（只取封账原版，未封账的日子不返回、列在 `meta.unclosed_dates`）、
或一个整数（某个 close_version）。`meta.days` 逐日写明状态：published、not_built（还没发布过）、not_closed、version_not_found。

### 四、服务以谁的身份运行

系统账户 `oss911svc`（40 建），库登录用户 `svc`（属 `oss911_query_svc`：读 core、mart、meta，对审计表只能写不能读），只走事务池。
非机密设置在 `/etc/oss911/svc.conf`（本剧本写），口令只在 `svc.env`。

## 执行

前提：runbook 18 已做完（40 建了 svc、46 同步了读取规则、23 建了 realm）。

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/60-svc.yml -e ansible_connection=local -K
```

第一遍要联网取 wheel（wheelhouse 里还没有 svc 的包），之后离线安装。

| 第一遍预期 | 第二遍 |
| --- | --- |
| `Fetch the service's wheels` changed；venv-svc 建出、依赖与程序装好；`contracts/openapi/v1.json matches the installed program`；svc.conf、systemd 单元写出，服务启动；`refuses a call without a token (401)`；最后三行：`ct101|200|1|101|self:101`、`sup1|200|60|…|all`、`disp1|403|table_not_permitted`，断言通过 | `changed=0`，验证照样通过 |

## 核验（自己调一次）

```bash
# 取一个测试用户的令牌（开发机才有 password grant），查 11 月各坐席的呈现量
tok() { sudo bash -c ". /etc/oss911/keycloak-test-users.env; curl -s http://127.0.0.1:8080/realms/oss911/protocol/openid-connect/token \
  -d grant_type=password -d client_id=oss911-portal -d username=$1 --data-urlencode password=\"\$KC_TEST_$1\"" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }

curl -s http://127.0.0.1:8090/v1/query -H "Authorization: Bearer $(tok ct101)" -H 'Content-Type: application/json' \
  -d '{"metrics":["M-C09","M-C10"],"dimensions":["agent"],"range":{"from":"2026-11-01","to":"2026-12-01"}}' | python3 -m json.tool | head -30

curl -s http://127.0.0.1:8090/v1/me -H "Authorization: Bearer $(tok ct101)" | python3 -m json.tool

# 审计：刚才的请求都在
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT occurred_at, user_principal, user_display, role_code, object_ref, metric_codes, row_count, duration_ms
  FROM audit.access_log ORDER BY access_id DESC LIMIT 8"
```

预期：ct101 只有一行 `"agent": "101"`，`meta.row_scope` 为 `self:101`；`/v1/me` 里 `user` 为 ct101、`subject` 是一串 id，`agent_no`
为 101，`problems` 为空，坐席处理表 `readable: true`、中心级四张 `readable: false`；审计表里 `user_principal` 是那串 id、
`user_display` 是用户名，能看到 ct101、sup1、disp1 的请求（disp1 那条 `row_count` 为空——被拒绝的）。0014 之前写的行
`user_principal` 是用户名、`user_display` 为空，审计只增不改，不回写。

口令经 `sudo bash -c` 在 root 的子进程里读取并传给 curl，不出现在你的终端历史里；curl 的参数在进程列表里短暂可见，这只在开发机的
password grant 上发生，生产不开它。

## 出错先看

```bash
systemctl status oss911-svc --no-pager
journalctl -u oss911-svc -n 80 --no-pager
```

- 启动即退出、日志里 `ConfigError`：svc.conf 或 svc.env 缺项、或 oss911svc 读不到（svc.env 必须属 oss911svc）
- 每个请求都是 401 `the token's signing key could not be obtained`：服务连不上 Keycloak 取公钥（8080 是否在、realm 是否存在）
- 401 `Invalid issuer`：取令牌用的地址与 svc.conf 的 `SVC_OIDC_ISSUER` 不同（都应是 http://127.0.0.1:8080/realms/oss911）
- 每个请求都是 500 `audit_unavailable`：审计写不进——看日志里的数据库错误（常见：当月的 audit.access_log 分区不存在，见 maintenance_partitions）
