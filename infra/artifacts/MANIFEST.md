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
---

## 待补

这些是已知会引入、但尚未落地的物料，先占位以免遗漏：

- pgBouncer、HAProxy（OS 源）
- Apache Airflow、dbt-core（PY）
- Apache Superset（PY 或 CT）
- Keycloak（CT）
- SeaweedFS（CT 或二进制）
- Prometheus（CT）
- WeasyPrint、python-docx（PY）
- Node.js 工具链、React、TypeScript（NPM）
- FreeTDS 驱动链（OS 源，迁移期组件）

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

**当前状态**：开发机上的 ISO 实测 SHA-256 为上表所记之值。
GPG 验签须在联网机器上补做一次并在此记明结果——**在补做之前，该 ISO 只能视为「未损坏」，不能视为「可信」**。
