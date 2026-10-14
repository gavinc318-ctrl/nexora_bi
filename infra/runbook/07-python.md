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
| 取料 | `pip wheel` 把全部轮子拉进（必要时构建进）`/var/cache/build/wheelhouse` | 要 |
| 安装 | `pip install --no-index --find-links <wheelhouse>` | **不要** |

`--no-index` 是这件事的关键：它让 pip **完全不去问 PyPI**。wheelhouse 里缺东西就当场失败，
而不是默默上网取来——后者会把一个离网环境里必然出现的问题，藏到交付之后才爆。

**开发机能上网这件事，会让所有离网问题推迟到最糟的时刻才暴露。** 多一步取料
的代价，换这条链路从第一天起就是真的。

取料用的是 `pip wheel` 而不是 `pip download`，区别只在一处：少数包上游只发源码包，
`pip download` 会把 `.tar.gz` 原样存下，装机时现场编译——那等于把编译器搬进了生产网。
`pip wheel` 在取料这台有网的机器上就把它构建成轮子。取料之后有一条断言盯着
「wheelhouse 里不许有源码包」。介质化的完整做法见 `90-offline-media.md`。

## 两种依赖锁定，作用相同、维护责任不同

**Airflow 有官方 constraints，那是硬条件不是建议。**
不带它解出来的依赖组合大概率装不出一个能跑的环境。URL 由 Airflow 版本与 Python
次版本共同决定：

```
https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt
```

**两者任一变化都要重新取。** 文件的 SHA-256 钉在 inventory 的 `airflow_constraints_sha256`
（与 MANIFEST 同一个值），剧本在安装前**断言**文件与之相同，不符即失败（DD-83）。
早先的做法是剧本只打印校验和、由人填回 MANIFEST，从不比对——文件变了也照样往下装。

**dbt 没有官方 constraints，所以我们自己冻结一份 lock。** 仓库里的
`infra/dbt-requirements.lock` 是**唯一权威**：取料与安装都直接读它，wheelhouse 里不存副本
（DD-83；早先在 wheelhouse 另存一份、两份无一致性保证，剧本会清掉那份旧副本）。
升级 dbt 时 `-e dbt_relock=true` 重新解析（需要网络），结果直接写回仓库路径——
它只是候选，`git diff` 审阅后提交才生效。
自建 lock 与官方 constraints 作用相同，区别只是**维护责任在谁**——
升级 dbt 时要自己重新解析并复核，没有上游替我们验证这个组合能跑（DD-69）。

## 第三个 venv：接入程序（第 2 批起）

```
/opt/oss911/venv-ingest     oss911-ingest（仓库 ingest/）+ psycopg 3 · PyYAML（按 infra/ingest-requirements.lock）
```

接入程序是独立的命令行程序，Airflow 以 BashOperator 调用，与 dbt 相同（DD-84 · LD-01 2.5）。
不装进 Airflow 的 venv：那边受 Airflow 官方 constraints 约束，两边各自冻结、各自升级。
依赖做法与 dbt 完全相同：仓库里的 lock 是唯一权威，`-e ingest_relock=true` 重新解析（DD-83）。

驱动是 psycopg 3（LGPL-3，DD-86），按 LD-09 4.2 只 import、不修改、**不得打成单文件可执行**。
与 dbt 用的 psycopg2 并存于 wheelhouse，互不替代。

本方的包从仓库的 `ingest/` 复制到 `/opt/oss911/ingest-src` 再构建（构建会写 `build/` 与
`*.egg-info`，以 root 写进仓库会留下 root 属主的文件），只在源码变了或尚未安装时才重装。

退出码是 DAG 依赖的约定：**0 成功（含批次被隔离）· 1 运行失败 · 2 用法或配置错误**，不得出现 99。

## 全局 pool（DD-85）

| pool | 槽位 | 为什么 |
| --- | --- | --- |
| `icp_cms` | 1 | ICP 话单接口不支持并发下载 |
| `dbt_build` | 1 | 两台 ETL 同时跑 dbt，线程总数会超过 pgBouncer 会话池而卡住不报错 |

由剧本按 inventory 的 `airflow_pools` 建立并校正，**不在界面上手工建**——手工建的不进版本管理，
重建环境时会丢。pool 是元数据库里的一行，生产上 ETL-1、ETL-2 两台共用。

## 跑

```bash
# 开发机上，一律从仓库执行（~/infra 是已退役的 rsync 副本，见 CLAUDE.md 第 1 节）
cd ~/src/nexora_bi/infra
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K   # 新增 airflow 池
ansible-playbook playbooks/30-python.yml     -e ansible_connection=local -K
ansible-playbook playbooks/30-python.yml     -e ansible_connection=local -K --diff   # changed 应为 0
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
# 三个 venv 的程序都能以 airflow 身份执行；接入程序的退出码约定
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest --version
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest; echo "rc=$?   # 应为 2（用法错误）"

# pool
sudo -u airflow bash -c 'set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow pools list' 2>/dev/null

# 四个组件的状态
systemctl status airflow-api-server airflow-scheduler airflow-dag-processor airflow-triggerer \
  --no-pager | grep -E 'Active:|●'

# dbt 能连上数仓（走会话级池）
# 第 8 步之后 dbt 的库用户与口令在 /etc/oss911/dbt.env，核验命令见 08-schema.md 人工核验第 6 条。
# 第 8 步之前库里还没有 dbt 用户，这条只能临时手填：
sudo -u airflow env DBT_USER=<业务用户> DBT_PASSWORD=<口令> \
  /opt/oss911/venv-dbt/bin/dbt debug --profiles-dir /var/lib/airflow

# Airflow 的 API（只绑本机，从 Mac 看要开隧道）
# ssh -L 8081:127.0.0.1:8081 nexora-bi
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/
```

`dbt debug` 是这一步最有价值的一条人工核验——它一次走完
dbt → pgBouncer 会话池 → HAProxy → PostgreSQL，并且会把每一跳的结果分别打出来。

## 口令轮换

删掉口令的存放文件、重跑本剧本即轮换：剧本生成新口令、`ALTER ROLE`、写回文件并重启
使用方。各口令存在哪里、按什么顺序轮换，见 `91-credential-rotation.md`。

## 开发机不等于生产的三处

| | 开发机 | 生产 |
| --- | --- | --- |
| 执行器 | `LocalExecutor`，单机 | 按 LD-08 的 ETL 对部署，执行器与并发另定 |
| 认证 | Airflow 自带，尚未接 Keycloak | 经 Keycloak，与门户同一套身份（LD-07 第 6 章） |
| wheelhouse 来源 | 本机 `pip wheel`（有网） | **介质携带的 wheelhouse**（MOI 内网没有 PyPI 镜像，见 `90-offline-media.md`） |

**Airflow 的认证配置是刻意留空的。** 它要和 Keycloak 一起配，而 Keycloak 的联邦
又要等 AD FS 的元数据到位。在那之前 Airflow 的 API 只绑回环，不对外。
