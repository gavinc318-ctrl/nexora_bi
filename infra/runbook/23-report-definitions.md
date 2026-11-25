# 第 23 步 —— 报表定义与两个接口（DD-102 第 ① 块）

## 这一步在做什么

门户（第 10 步）按**报表定义**通用渲染每张报表，不逐张写页面。这一步只做定义与接口，不涉前端：

- `reports/<编号>.yaml`：每张报表一份，格式见 `reports/README.md`。七张框架样例：R-01 · R-04 · R-05 · R-09 · R-21 · R-26 · D-06
- 结构变更 0018：`meta.report_definition`，只由 46 写，服务层只读
- 46 同步指标时一并校验并同步报表。校验最要紧的一条：**报表的可见角色必须是它读的每张 mart 表可读角色的子集**——
  否则目录里看得见、打开一查就 403
- 服务层 0.3.0：`GET /v1/reports`（当前角色的目录）、`GET /v1/reports/{编号}`（单张定义，指标名按语言、显示格式、
  指标参数一并给出）。无权、未发布、不存在同为 403 `report_not_permitted`，都写审计

定义只说「查什么、怎么摆」，数据仍经 `POST /v1/query`，受同一套权限约束。

本步同时带上 runbook 22「补丁二」（状态时长各用自己的分母），部署顺序合在下面一起。

## 执行

```bash
sudo -v
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/40-schema.yml    -e ansible_connection=local -K    # 结构变更 0018
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K    # 发布目录多了 reports/
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K    # 不能跳过：dbt 工程由它装

af() { sudo -u airflow bash -c "set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow $*"; }
af dags trigger publish_marts_intraday          # 状态表加六列；等 list-runs 显示 success 再往下
ansible-playbook playbooks/46-metrics.yml   -e ansible_connection=local -K    # 指标与报表
af dags trigger publish_marts_nightly
ansible-playbook playbooks/60-svc.yml       -e ansible_connection=local -K    # 服务层 0.3.0
```

40、45、50、46、60 再各跑一遍，应为 `changed=0`。

| 步骤 | 预期 |
| --- | --- |
| 40 | 0018 执行；角色检查全部通过（多了报表定义的五项） |
| 45 | 新发布目录，含 `reports/` 下七份 |
| 46 | `reports: 7`；M-R10 至 M-R15 `redefined`；七张报表 `report_inserted`；其余 unchanged |
| nightly | 状态表最近 7 个已封账日各多一个版本（runbook 22 补丁二的核验查询） |
| 60 | 服务重启；最后的断言里多三行：`ct101|reports|R-04,R-26|R-21:403:report_not_permitted`、`sup1|reports|D-06,R-01,R-04,R-05,R-09,R-21,R-26|R-21:200`、`disp1|reports||R-21:403:report_not_permitted` |

## 核验（自己调一次）

`tok` 同 runbook 19：

```bash
tok() { sudo bash -c ". /etc/oss911/keycloak-test-users.env; curl -s http://127.0.0.1:8080/realms/oss911/protocol/openid-connect/token \
  -d grant_type=password -d client_id=oss911-portal -d username=$1 --data-urlencode password=\"\$KC_TEST_$1\"" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }

curl -s http://127.0.0.1:8090/v1/reports -H "Authorization: Bearer $(tok ct101)" | python3 -m json.tool
curl -s "http://127.0.0.1:8090/v1/reports/R-04?locale=ar" -H "Authorization: Bearer $(tok ct101)" | python3 -m json.tool | head -40
curl -s http://127.0.0.1:8090/v1/reports/R-04 -H "Authorization: Bearer $(tok sup1)" | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["filters"], d["formats"])'
curl -s http://127.0.0.1:8090/v1/reports/NOPE -H "Authorization: Bearer $(tok sup1)"; echo

sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT report_id, kind, roles, published, left(definition_sha256, 12) AS sha, synced_at FROM meta.report_definition ORDER BY 1"
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT user_display, role_code, object_ref, metric_codes, row_count FROM audit.access_log
 WHERE object_ref LIKE '/v1/reports%' ORDER BY access_id DESC LIMIT 6"
```

预期：

- ct101 的目录只有 R-04、R-26；R-04 的阿语定义里标题仍是英文（`title_ar_state: draft`，阿语标题待 Mac 侧），指标名是阿语，
  `filters` 为空
- sup1 的 R-04 `filters` 为 `['agent']`，`formats` 里 M-C07 为 `duration`、其余 `integer`
- NOPE（不存在的编号）答 `{"error": "report_not_permitted", ...}`，与无权时一字不差
- 审计里每次请求一行；被拒的 `row_count` 为空，取到定义的 `metric_codes` 是该报表用到的指标

## 出错时

- 46 报 `reports/R-xx.yaml: ...`：定义有错，整次同步没做（指标也没同步）。照提示改定义，重跑 45 → 50 → 46
- 46 报 `the report is for call_taker, who may not read ...`：报表的角色超出了表的角色。要么从报表里去掉该角色，要么在
  `metrics/marts.yaml` 给表加角色（那是权限变更，先问 Mac 侧）
- 60 的报表断言失败而查询断言通过：多半是 46 还没跑，`meta.report_definition` 是空的
