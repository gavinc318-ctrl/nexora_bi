# 第 7 步 —— Python 侧：Airflow 与 dbt

## 两个 venv，不是一个

```
/opt/oss911/venv-airflow    apache-airflow[postgres]==3.3.2
/opt/oss911/venv-dbt        dbt-core + dbt-postgres（按自建 lock）
```

Airflow 与 dbt 的依赖长期相互冲突（jinja2、protobuf 等）。合在一起有两种结局：
装不上，或者装上之后某一方被**静默降级**。后者更坏——它不报错，要到运行期才显形，
而那时现象是「某个 dbt 宏突然不工作了」，没人会想到是装 Airflow 时顺手降的级。

Airflow 用 `BashOperator` 调 dbt 的可执行文件，两边只共享项目目录，不共享解释器环境。
剧本里有一条断言专门盯这件事：`venv-airflow/bin/dbt` 与 `venv-dbt/bin/airflow`
都**必须不存在**。

## 一律按离网方式安装，即使开发机有网

流程分两段，**只有第一段需要网络**：

| 段 | 做什么 | 要网络 |
| --- | --- | --- |
| 取料 | `pip download` 把全部 wheel 拉进 `/var/cache/build/wheelhouse` | 要 |
| 安装 | `pip install --no-index --find-links <wheelhouse>` | **不要** |

`--no-index` 是这件事的关键：它让 pip **完全不去问 PyPI**。wheelhouse 里缺东西就当场失败，
而不是默默上网取来——后者会把一个离网环境里必然出现的问题，藏到交付之后才爆。

**开发机能上网这件事，会让所有离网问题推迟到最糟的时刻才暴露。** 多一步 `pip download`
的代价，换这条链路从第一天起就是真的。

## 两种依赖锁定，作用相同、维护责任不同

**Airflow 有官方 constraints，那是硬条件不是建议。**
不带它解出来的依赖组合大概率装不出一个能跑的环境。URL 由 Airflow 版本与 Python
次版本共同决定：

```
https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt
```

**两者任一变化都要重新取。** 剧本会打印它的 sha256，当场填回 `artifacts/MANIFEST.md`。

**dbt 没有官方 constraints，所以我们自己冻结一份 lock。** 首次运行时解析一次依赖、
`pip freeze` 成 `dbt-requirements.lock`，此后一律按 lock 取料与安装。
自建 lock 与官方 constraints 作用相同，区别只是**维护责任在谁**——
升级 dbt 时要自己重新解析并复核，没有上游替我们验证这个组合能跑（DD-69）。

## 跑

```bash
# Mac 上
rsync -av --delete infra/ nexora-bi:~/infra/

# 开发机上
cd ~/infra
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K   # 新增 airflow 池
ansible-playbook playbooks/30-python.yml     -e ansible_connection=local -K
# 把打印出的 constraints sha256 填回 MANIFEST，并把 dbt lock 提交进仓库
ansible-playbook playbooks/30-python.yml     -e ansible_connection=local -K   # changed 应为 0
```

第一遍会下载几百兆的 wheel，慢是正常的。**第二遍必须是 `changed=0`**——
否则说明某一步每次都在重装，离网重建时那一步会失败。

## 验证：验行为

剧本里的四条断言：

1. **版本对得上** —— `airflow version` 必须正好是 inventory 里钉的版本
2. **两个 venv 确实分开** —— 互相的可执行文件都不存在
3. **Airflow 能连元数据库** —— `airflow db check`，走的是 pgBouncer 的 `airflow` 库（会话级池）
4. **dbt 的并发不超过会话池** —— `threads` 与池大小绑定

第 3 条顺带验证了一整条链路：Airflow → pgBouncer → HAProxy → PostgreSQL。

## 人工核验

```bash
# 四个组件的状态
systemctl status airflow-api-server airflow-scheduler airflow-dag-processor airflow-triggerer \
  --no-pager | grep -E 'Active:|●'

# dbt 能连上数仓（走会话级池）
sudo -u airflow env DBT_USER=<业务用户> DBT_PASSWORD=<口令> \
  /opt/oss911/venv-dbt/bin/dbt debug --profiles-dir /var/lib/airflow

# Airflow 的 API（只绑本机，从 Mac 看要开隧道）
# ssh -L 8081:127.0.0.1:8081 nexora-bi
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/
```

`dbt debug` 是这一步最有价值的一条人工核验——它一次走完
dbt → pgBouncer 会话池 → HAProxy → PostgreSQL，并且会把每一跳的结果分别打出来。

## 开发机不等于生产的三处

| | 开发机 | 生产 |
| --- | --- | --- |
| 执行器 | `LocalExecutor`，单机 | 按 LD-08 的 ETL 对部署，执行器与并发另定 |
| 认证 | Airflow 自带，尚未接 Keycloak | 经 Keycloak，与门户同一套身份（LD-07 第 6 章） |
| wheelhouse 来源 | 本机 `pip download`（有网） | 内网 PyPI 镜像，或介质携带的 wheelhouse |

**Airflow 的认证配置是刻意留空的。** 它要和 Keycloak 一起配，而 Keycloak 的联邦
又要等 AD FS 的元数据到位。在那之前 Airflow 的 API 只绑回环，不对外。
