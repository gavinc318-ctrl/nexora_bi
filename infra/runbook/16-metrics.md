# 第 16 步 —— 指标注册表：46-metrics（DD-97）

## 这一步在做什么

`metrics/metrics.yaml` 是指标口径的唯一权威（LD-03 第 5 章）；库里的 `meta.metric_registry` 是部署时同步过去的只读副本，
服务层（第 9 步）按它回答「有哪些指标、叫什么、读哪张 mart 的哪几列」。同步只由剧本 46 做，和 45 同步接入契约是同一个模式。

结构变更 0012 同时建了两张发布登记表，供第 8 步第二段的 mart 发布使用：

| 表 | 一行是什么 |
| --- | --- |
| `meta.metric_registry` | 一个指标的一个口径版本（主键：编码 + 版本） |
| `meta.data_publication` | 一次写 mart（每小时临时数 `intraday`、夜间封账 `nightly`、封账后修正 `correction`），`publication_seq` 是缓存键用的序号 |
| `meta.mart_day_publication` | 某张 mart 表某一天写了哪个版本、多少行——**零行也登记**，服务层先据它定每天用哪一版再读数（DD-99） |

## 先懂四件事

### 一、版本只往前走，旧版本留着

| metrics.yaml 与库里比 | 结果 |
| --- | --- |
| 库里没有 | 插入；第一个版本的 `effective_from` 是 `-infinity`（覆盖全部历史） |
| 同版本、口径没变 | 只更新可以不升版本的字段：名称、维度、来源表、`depends_unverified`、是否发布、mart 映射 |
| 同版本、口径变了 | **上线前**原地改，报 `redefined`（DD-97 第五条：进生产前改口径不升版本）；**上线后**部署失败 |
| 版本更高 | 新插一行，旧版本行不动；`effective_from` 取 yaml 里写的，没写就是同步当天 |
| 版本更低 | 部署失败（库里已经更新） |

「口径」指 definition、grain、aggregation、measure、entity_date、params、retrospective 七项（LD-03 第 6 章）；改了哪一项都算。
上线前后由 inventory 的 `metrics_pre_production` 区分，现在是 `true`，上线那天改为 `false`。

### 二、阿语名：草稿照显示，确认后部署不覆盖

59 条的阿语名现在都是 `draft`。MOI 确认走 M-10 的译名流程（DD-56），确认后库里是 `confirmed`，之后部署不会拿 yaml 的草稿覆盖它。

### 三、没有 mart 的指标照样同步，标「未实现」

只有写了 `mart_table` 的指标才算已实现。计数、合计类指向一列（`mart_column`），比率、均值类指向分子分母两列（`mart_numerator`、
`mart_denominator`）——mart 只存可以相加的列，比率在服务层相除，否则按周、按全部队列汇总时会变成「均值的均值」。
中位数、P90、最小、最大这类不能相加的聚合，暂时不能指向 mart。没实现的指标，服务层会明确回答「未实现」，而不是返回空。

### 四、指向的列必须存在

同步完成前检查注册表里**所有版本**的 mart 映射：表要在 mart schema 里，列要都在。旧版本的列也查，所以删掉旧版本还在用的列
会让部署失败（DD-97：口径升级加带后缀的新列，旧列保留）。因此**先有 mart 表，再跑 46**。

## 执行（结构变更 0012）

```bash
sudo -v
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/40-schema.yml    -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
ansible-playbook playbooks/46-metrics.yml   -e ansible_connection=local -K
```

| 剧本 | 第一遍预期 | 第二遍 |
| --- | --- | --- |
| 40 | 执行 0012；`Structural changes 0000 to 0012 executed`；权限验证全过 | `changed=0` |
| 45 | 新发布目录（多了 `metrics/`）；契约 unchanged | `changed=0` |
| 46 | `metrics.yaml: 59 metrics`；59 条 `inserted`；implemented: none yet | `every metric unchanged`，`changed=0` |

0012 不碰 fact_call 以外的业务表，只给 fact_call 建一个索引，不必暂停构建 DAG。

## 核验

```bash
sudo -u postgres psql -d oss911 -P pager=off -c "
SELECT metric_code, metric_version, effective_from, aggregation, name_ar_state, is_published, mart_table IS NOT NULL AS implemented
  FROM meta.metric_registry ORDER BY metric_code LIMIT 8"
```

59 行；版本与 metrics.yaml 一致；`effective_from` 都是 `-infinity`；目前 implemented 全为 false（mart 在第 8 步第二段的下一批）。

2026-11-16 之后：话务的 12 条已指向 mart（runbook 17），46 要在 mart 表第一次发布之后跑，顺序见 runbook 17「执行」。
