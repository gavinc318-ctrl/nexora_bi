# 外部物料清单

离网重建的物料依据。**每引入一个外部依赖就加一行，当场加。**

## 记录规矩

- **当场加，不事后补。** 事后补一定漏，而漏的恰恰是当时觉得「这个很常见」的那些
- **校验和必填。** 没有校验和的物料在离网导入时无法验证完整性，等于来源不明
- **「为什么是这个版本」必填。** 两年后有人问「能不能升」，这一列是唯一的答案来源。
  写「最新版」等于没写——要写清是被什么约束定住的：与 OS 默认仓库一致、
  与某组件的兼容矩阵、还是踩过某个坑
- **不删行。** 换版本是**加一行新的并把旧行标为「已替换」**，不是改原行。
  历史要可追溯——出问题时第一个要查的就是「上次是什么版本，什么时候换的」

## 类别

| 代号 | 供给链 | 离网导入方式 |
| --- | --- | --- |
| OS | Ubuntu 包源 | 内网 apt 镜像 |
| CT | 容器镜像 | 内网镜像仓库 |
| PY | PyPI | 内网 PyPI 镜像 |
| NPM | npm | 内网 npm 镜像 |
| MVN | Maven | 内网 Maven 镜像 |
| ISO | 安装介质 | 离线介质直接携带 |

五条供给链对应 LD-09 第 7 章。**任一条断供都会使补丁停滞**，
所以每条都要有明确的维护责任人（在 LD-08 中落实到人）。

---

## 清单

| 类别 | 物料 | 版本 | 来源 | SHA-256 | 引入 | 为什么是这个版本 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ISO | ubuntu-24.04.5-live-server-amd64.iso | 24.04.5 | releases.ubuntu.com/24.04/ | 97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8 | 2026-09-28 | 24.04 LTS 的当前点版本。选 24.04 而非 26.04：默认仓库即 PG 16，与已验证的 DDL 一致，零返工；生态成熟两年半（DD-62） | 在用 |
| OS | 基线包集（见 inventory/dev.yml 的 base_packages） | 随 24.04 仓库 | Ubuntu noble 官方源 | 由 apt 源签名保证 | 2026-09-29 | 四组：基础操作、编辑器、卷管理、排障工具、Ansible 运行时。版本不单独钉——跟随 OS 源，由 24.04 的冻结版本保证一致性 | 在用 |
| OS | ansible-core | 2.16.3-0ubuntu2 | Ubuntu noble 官方源 | 由 apt 源签名保证 | 2026-09-29 | 控制节点装在开发机自身而非 Mac：Ubuntu 源是离线环境唯一有对应物的供给链，Homebrew/pipx 在离网环境没有。用 ansible-core 而非 ansible 全家桶——后者含约百个 collection，与「只用 ansible.builtin」的纪律冲突 | 在用 |
| OS | swap: /swap.img | 8 GB 文件 | 安装器创建 | — | 2026-09-29 | 装机默认。保留作兜底，但 vm.swappiness=10 使其不成为常规内存层；生产的库虚机按 RS-01 禁用交换 | 在用 |
| OS | podman | 4.9.3+ds1-1ubuntu0.2 | Ubuntu noble 官方源 | 由 apt 源签名保证 | 2026-09-30 | 随 24.04 仓库，不单独钉版本。rootful 运行（共享机器，见 runbook/03）；网段隔离依赖 netavark 的 isolate 选项，该选项在本版可用并已实测生效 | 在用 |
| OS | postgresql-16 | 16.15-0ubuntu0.24.04.1 | Ubuntu noble 官方源 | 由 apt 源签名保证 | 2026-09-30 | 24.04 默认仓库即 PG 16，与已验证的 DDL 一致，零返工且少一条离网供给链（DD-62）。注意 PG 16 的上游支持到 2028-11，八年合同期内必然经历一次大版本升级，演练规程在 LD-08 | 在用 |
| OS | postgresql-16-postgis-3 | 3.4.2+dfsg-1ubuntu3 | Ubuntu noble 官方源 | 由 apt 源签名保证 | 2026-09-30 | 随 PG 16 的配套版本。空间聚合（网格化、点在面内判定）依赖它；升级 PG 大版本时 PostGIS 须同步评估，二者的兼容矩阵是硬约束 | 在用 |
| OS | ICU（随 PostgreSQL 引入） | 随 noble 基线 | Ubuntu noble 官方源 | 由 apt 源签名保证 | 2026-09-30 | DD-63 的列级 collation 依赖 ICU。ICU 版本变化会改变排序结果，升级时须检查 PostgreSQL 的 collation version 并按需 REINDEX——这是记在这里的原因 | 在用 |
| CT | Keycloak | 26.0.8 | quay.io/keycloak/keycloak:26.0 | sha256:09a381c715ab0b111835b70f2905955274843a219c6f27efb348e4d9f4086858 | 2026-10-07 | 身份与令牌（LD-07 第 6 章）。26.x 是 KC_BOOTSTRAP_ADMIN_* 环境变量与独立管理端口 9000 的版本线；开发机用 start-dev，生产用 start --optimized。按 digest 钉死——tag 会被重新发布，digest 不会 | 在用 |
| CT | SeaweedFS | 3.80 | docker.io/chrislusf/seaweedfs:3.80 | sha256:1055999e08eed1789b0ae45d235126e4495e23d3fb9d6396293fd42539b1ae6a | 2026-10-07 | 对象存储：现场媒体、发布制品、导出文件。选它而非 MinIO 的理由见 DD-46（上游健康度优先于许可）。开发机单机 server 模式，生产三节点（PL-04） | 在用 |
| CT | Prometheus | v2.55.1 | docker.io/prom/prometheus:v2.55.1 | sha256:2659f4c2ebb718e7695cb9b25ffa7d6be64db013daba13e05c875451cf51b0d3 | 2026-10-07 | 可用性与指标采集。不用 Grafana（AGPL，见组件选型），看板由本方门户承担。开发机留存 15 天，生产按 LD-08 的留存要求另定 | 在用 |
| PY | Airflow 官方 constraints 文件 constraints-3.3.2/constraints-3.12.txt | 对应 Airflow 3.3.2 / Python 3.12 | raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt | 821effd0f491975682f2774fca3427d7bc2f59ca6de68dcb413ad2c8f890e58c | 2026-10-08 | 这一行是整个 Airflow 依赖树的冻结依据（DD-69）。上游为每个版本发布经 CI 验证的约束集；不用它就等于让 pip 在装机时自行解析数百个包，同一份剧本在不同时间装出的环境会不同。文件名中的 py 版本必须与目标机的 python3 次版本一致（本机 3.12），换 OS 时要同步换 | 在用 |
| PY | apache-airflow（含 postgres / celery 等 extras） | 3.3.2 | PyPI，经 constraints 解析后下载至 wheelhouse | 由 constraints 文件（上一行）与 wheelhouse 内各 wheel 自身的哈希保证 | 2026-10-08 | 调度。3.2.0 起支持 Python 3.10–3.14，组件为 api-server / scheduler / dag-processor / triggerer（不再是 webserver），剧本与 systemd 单元按此编排。版本钉在 3.3.2 而非跟随最新：constraints 是按版本发布的，升级意味着换一整套约束并重新验证 | 在用 |
| PY | dbt-core 与 dbt-postgres | 见 infra/dbt-requirements.lock | PyPI，由本方解析一次后冻结 | 由 lock 文件内各包的钉死版本保证 | 2026-10-08 | 模型构建。dbt 没有上游 constraints，故由本方在有网环境解析一次、pip freeze 冻结入库（DD-69）。**这是一项长期运维义务**：升级 dbt 时要自己重新解析并复核组合可用，没有上游替我们验证 | 在用 |
---

## 待补

这些是已知会引入、但尚未落地的物料，先占位以免遗漏：

- pgBouncer、HAProxy（OS 源）— 已安装，待补本清单
- Apache Superset（PY 或 CT）
- WeasyPrint、python-docx（PY）
- Node.js 工具链、React、TypeScript（NPM）
- FreeTDS 驱动链（OS 源，迁移期组件）
---

## 容器镜像的两条纪律

**一、按 digest 钉死，不按 tag。** tag 会被重新发布——同一个 `keycloak:26.0`
今天和半年后可能是两个不同的镜像，而离网环境里你无从发现。digest 不会变。
剧本首次按 tag 拉取时会把解析出的 digest 打印出来，**当场填回 inventory 与本清单**。
这一步刻意保持手工：自动写回就没人看了，而「这是不是同一个镜像」正是要有人看一眼的事。

**二、本机上不该有本清单之外的镜像。** 未登记的镜像在重建时不会被带过去，
而依赖它的东西会悄悄失败。定期用下面这条对账，出现清单外的镜像就追一下来源，
要么登记要么删除：

```bash
sudo podman images --digests --format '{{.Repository}}:{{.Tag}} {{.Digest}}'
```

---

## ISO 的校验方式

`sha256sum` 算出来的值只能证明「这个文件没有在复制过程中损坏」，
**不能证明它就是 Canonical 发布的那个文件**——两者差一个签名。离网环境里拿不到签名就只能靠这一步，
所以必须在**还能联网的机器上**做完，并把结论记在这里。

```bash
# 在联网机器上，与 ISO 放在同一目录
curl -LO https://releases.ubuntu.com/24.04/SHA256SUMS
curl -LO https://releases.ubuntu.com/24.04/SHA256SUMS.gpg

# 1) 验签名：SHA256SUMS 确实由 Ubuntu CD 签名密钥签发
gpg --keyid-format long --keyserver hkps://keyserver.ubuntu.com \
    --recv-keys 0x843938DF228D22F7B3742BC0D94AA3F0EFE21092
gpg --verify SHA256SUMS.gpg SHA256SUMS

# 2) 再比对本地 ISO 的哈希
sha256sum -c SHA256SUMS 2>/dev/null | grep live-server
```

密钥指纹 `8439 38DF 228D 22F7 B374  2BC0 D94A A3F0 EFE2 1092`（Ubuntu CD Image Automatic Signing Key (2012)）
已记在本清单中，离网环境中以此为准。

顺序不能颠倒：先验签名再比哈希。反过来做等于用一个未经验证的清单去验文件。

**当前状态：已验证（2026-10-06）。**

- `gpg --verify` 结果 `Good signature`，签发时间 2026-09-15，
  主密钥指纹 `8439 38DF 228D 22F7 B374  2BC0 D94A A3F0 EFE2 1092`，与上述记录一致
- `sha256sum -c` 结果 `ubuntu-24.04.5-live-server-amd64.iso: OK`，
  与清单中记录的 `97f3d7ff…` 一致

关于那条 `WARNING: This key is not certified with a trusted signature`：
它说的不是签名无效，而是**本机的信任网中没有人为这把密钥背书**——
密钥是刚从 keyserver 取来的，本地没有给它签过信任。
判定依据因此落在**指纹比对**上：指纹与本清单中独立记录的值逐字相同，即可接受。
离网环境中没有 keyserver，这一步只能靠清单里的指纹，所以那一行必须留着。

输出里另外两行 `FAILED open or read` 是 SHA256SUMS 中列出的 24.04.3 与 24.04.4 的 ISO
在本地不存在，不是校验失败。只有列出的文件存在时才会被实际比对。

---

## PyPI 侧的取料与安装

Python 依赖不在装机时解析，分两步（DD-69）：

```bash
# 取料：需要联网，只在更新 wheelhouse 时做
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K -e wheelhouse_refresh=true

# 安装：不联网，pip --no-index --find-links 从 wheelhouse 读
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K
```

`wheelhouse_refresh` 默认为 **false**，这是刻意的。`pip download` 虽然不会重复
下载已存在的字节，但**仍会每次连 PyPI 重新解析依赖树**——若不加这个开关，
剧本在真正离网的机器上会卡在取料阶段跑不起来。开发机有网，会把这个问题盖住。

离网重建时，把介质上的 `wheelhouse/` 整个目录连同 `constraints-airflow-3.3.2-py312.txt`
与 `dbt-requirements.lock` 放到 `/var/cache/build/wheelhouse/` 下，直接跑安装那一条。
wheelhouse 为空而本次又没取料时，剧本会断言失败并把这段话打出来，不会让 pip 抛
一句难读的 `no matching distribution found`。

读取这个目录需要 root：它是 `0750 root:root`。注意 `sudo sha256sum .../constraints-*.txt`
这种写法不行——通配符由调用者那个非 root 的 shell 展开，`sudo` 只作用于命令本身。
要让展开也发生在 root 下：`sudo bash -c 'sha256sum .../constraints-*.txt'`。
