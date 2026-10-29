# 第 15 步 —— 通话级：core.fact_call（DD-96）

## 这一步在做什么

`fact_call_leg` 是每张话单（每通电话经过的每个设备）一行。指标大多按「一通电话」算，所以由 dbt 把同一 CALLID 的腿
聚成一行 `core.fact_call`（DD-66：通话级不独立接入，只从腿聚）。规则全文在 LD-03 2.4，这里只讲操作上要懂的。

| 模型 | 什么时候跑 |
| --- | --- |
| `fact_call` | `build_icp_cdr_voice`（新腿进来之后）与 `build_icp_reference`（维度变了、腿重新关联之后）各跑一次 |

## 先懂四件事

### 一、首腿到了才有通话行，末腿到了才算完整

没有首腿（CALLIDNUM = 1）就没有「呼叫进来的时刻」，不建行；腿照留在 `fact_call_leg`，测试 `fact_call_leg_without_first_leg`
（WARN）报告有多少这样的 CALLID。末腿（-1）没到的通话照建，`is_complete = false`，结果码、结束时刻、挂断方都为空——
发布的指标只算完整的通话，没完整的单独算「进行中」。

### 二、四种结果，先看谁挂的

| 顺序 | 结果 | 条件 |
| --- | --- | --- |
| 1 | `answered` | 有接通的坐席腿 |
| 2 | `abandoned` | 有队列腿或坐席腿，末腿由主叫释放（不论当时在排队还是坐席在振铃） |
| 3 | `unanswered_system` | 有坐席腿、都没接通，不是主叫挂的 |
| 4 | `ivr_only` | 其余 |

转接与恶意呼叫是标志（`is_transferred`、`is_malicious`），不是结果。坐席那一侧的「未接」看腿级（M-C12）。

### 三、只重算腿变了的通话

每次构建只看「上次写入以来腿有变化」（腿被改写或被维度模型重新关联）的通话，再多看一天的余量；重算后只写内容真的变了的行。
同一 CALLID 各腿的 CALLTYPE 不一致时，通话取首腿的，另开一条待澄清（每个 CALLID 一条）。

### 四、每一列都能从腿重算

测试 `fact_call_recomputes_from_legs` 用另一种写法把每一列从腿重算一遍再比；`fact_call_result_rules` 检查结果码的规则；
`fact_call_for_every_first_leg` 检查有首腿的通话都有行。三条都是 ERROR 级：通话级与腿级一旦对不上，报表就会自相矛盾。

## 执行（结构变更 0010）

0010 给 `fact_call` 加 `is_complete`，往 `dim_call_result` 写入四种结果，给腿表加 `last_change_at` 索引。没有新扩展，20 不必先跑；
dbt 与 DAG 有改动，所以 45 在 50 之前：

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/40-schema.yml    -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/50-dags.yml      -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 |
| --- | --- |
| 40 | 执行 0010；`Structural changes 0000 to 0010 executed`；权限验证全过 |
| 45 | 新发布目录；契约跳过 |
| 50 | 安装新的 dbt 项目与 DAG |

第二遍都应 `changed=0`。之后第一次 `build_icp_cdr_voice` 会把已有的全部通话建出来（腿全都「近一天内变过」或表为空）。

## 核验

```bash
# 结果分布：完整的四种之一，未完整的为空
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT r.code, f.is_complete, count(*)
  FROM core.fact_call f LEFT JOIN core.dim_call_result r USING (call_result_sk)
 GROUP BY 1, 2 ORDER BY 1, 2"

# 有首腿的 CALLID 数应等于通话行数
sudo -u postgres psql -d oss911 -tAc "
SELECT (SELECT count(DISTINCT call_sk) FROM core.fact_call_leg WHERE leg_seq = 1), (SELECT count(*) FROM core.fact_call)"
```

两个数相等；构建日志里 `fact_call_recomputes_from_legs`、`fact_call_result_rules`、`fact_call_for_every_first_leg` 都是 PASS。
