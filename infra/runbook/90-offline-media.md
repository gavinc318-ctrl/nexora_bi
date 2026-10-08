# 第 90 步 —— 离网介质：把三条供给链装进一个 U 盘

> 本步的设计结论登记为 **DD-70**（发布形态与介质自带三个仓库）与 **DD-71**
> （容器镜像用 skopeo + OCI 目录传递，复验锚点在介质）。
>
> **前提：MOI 生产网内没有任何仓库** —— 没有 apt 镜像、没有 PyPI 镜像、没有容器
> 镜像仓库，也没有到互联网的出口。所以不是「我们需要内网有仓库」，而是
> **我们自己把仓库做成介质带进去**。本文是这件事的做法。

本步在交付阶段执行，不在开发环境构建的七步之内。但**它依赖的每一个工具都已在
开发机上实测过**（2026-10-08），结论写在各节里——交付路径上的工具必须在还有
退路的时候跑一遍，这是本项目反复付过学费的一条。

---

## 1. 第一个决定：哪些东西根本不该进墙内

区分**构建期依赖**与**运行期依赖**。只有运行期依赖需要跨墙。

| 依赖 | 期 | 处置 |
| --- | --- | --- |
| Node.js / TypeScript / React 工具链 | 构建期 | **不进墙内**，墙外编译，只带 `dist/` 静态文件 |
| `build-essential`、`libpq-dev` | 构建期（取料侧） | 生产机可不装；取料在墙外的同版本机器上完成 |
| PostgreSQL、PostGIS、podman、pgBouncer、HAProxy、skopeo | 运行期 | 进 apt 闭包 |
| Airflow、dbt 及其全部依赖 | 运行期 | 进 wheelhouse（只含轮子） |
| Keycloak、SeaweedFS、Prometheus 镜像 | 运行期 | 进 OCI 目录 |

**原则：凡能在墙外完成的构建，就不要把构建能力带进墙内。** 带进去的每一样东西
都是八年的维护义务，而且现场构建失败时排查成本极高。

这一条直接消掉了一条供给链：**npm 不进生产网**，MANIFEST 的五条链在墙内只剩三条
（OS / PY / CT），Maven 视最终选型同样处理。

---

## 2. apt：自建一个签名的本地仓库

不要 `debmirror` 整个 noble 源（几百 GB，且无必要）。只取需要的包**及其依赖闭包**，
自建索引并用我方 key 签名。

```bash
# 解析闭包
apt-get install --reinstall --print-uris -y \
    postgresql-16 postgresql-16-postgis-3 pgbouncer haproxy podman skopeo ... \
  | grep -oP "(?<=')http\S+(?=')" > urls.txt
wget -i urls.txt -P pool/

# 建索引并签名
dpkg-scanpackages pool /dev/null | gzip -9c > dists/noble/main/binary-amd64/Packages.gz
apt-ftparchive release dists/noble > dists/noble/Release
gpg --default-key <我方 key> -abs -o dists/noble/Release.gpg dists/noble/Release
```

目标机上加一行源，`file:` 协议，不必起 HTTP 服务：

```
deb [signed-by=/usr/share/keyrings/oss911.gpg] file:/srv/oss911/apt noble main
```

### ⚠ 闭包必须在干净机器上解析

`--print-uris` **只列出「这台机器上还缺的」包**。若解析闭包的机器已经装了某些
依赖而目标机没有，闭包就是不完整的，到现场才会发现。

所以：**闭包必须在一台按 ISO 全新安装、未做任何改动的 24.04.5 上解析**，并且宁可多带。
如果平台方按自家基线交付虚机（**IR-47**），闭包要按**他们的基线**重算——
这是「虚机按什么基线交付」那个问题的现实后果。

---

## 3. PyPI：wheelhouse 本身就是仓库

`pip install --no-index --find-links <wheelhouse>` 不需要任何服务。做法与纪律见
`07-python.md` 与 `artifacts/MANIFEST.md`「PyPI 侧的取料与安装」，介质侧只要三样：

```
wheelhouse/airflow/*.whl
wheelhouse/dbt/*.whl
wheelhouse/constraints-airflow-3.3.2-py312.txt      # sha256 在 MANIFEST
wheelhouse/dbt-requirements.lock                    # 已入库 infra/dbt-requirements.lock
```

**wheelhouse 里不许有源码包。** 2026-10-08 实测，`pip download` 取下的 190 个包里
混着一个 `dbt_core_experimental_parser-2.0.5.tar.gz`（上游没发轮子）。取料已改用
`pip wheel`，并有断言守着。源码包等于把编译器带进墙内。

---

## 4. 容器镜像：skopeo + OCI 目录，不用 `podman save`

**`podman save` / `load` 这条路走不通**（2026-10-08 实测，`load` 直接拒绝）。
原因与完整命令见 `artifacts/MANIFEST.md`「容器镜像的三条纪律」第三条。要点：

```bash
# 墙外
skopeo copy --all docker://<repo>@<digest> oci:/media/oss911/images/<name>:<tag>

# 复验（离网可做，不需要仓库）：输出应等于 MANIFEST 记的 digest
skopeo inspect --raw oci:/media/oss911/images/<name>:<tag> | sha256sum

# 墙内（导入时不能带 --all）
skopeo copy --override-os linux --override-arch amd64 \
  oci:/media/oss911/images/<name>:<tag> containers-storage:<repo>:<tag>
```

`skopeo inspect --raw | sha256sum` 就是 digest 的复验——digest 的定义即 manifest
自身的 SHA-256，所以它不需要联网、不需要仓库、不信任任何中间环节，只看介质上
那几个字节。**这是跨墙交付里唯一还成立的验证方式。**

---

## 5. 介质的组织与体积

```
/media/oss911/
  ISO/        ubuntu-24.04.5-live-server-amd64.iso + SHA256SUMS + .gpg
  apt/        dists/ pool/ oss911.gpg
  wheelhouse/ airflow/ dbt/ constraints-*.txt dbt-requirements.lock
  images/     keycloak:26.0/ seaweedfs:3.80/ prometheus:v2.55.1/   （OCI 目录）
  dist/       门户前端的编译产物
  infra/      剧本、inventory、runbook、MANIFEST
  SHA256SUMS  + SHA256SUMS.gpg（我方签名，覆盖以上全部）
```

粗估：ISO 约 3 GB，apt 闭包 1–2 GB，wheelhouse 1–1.5 GB，镜像 1–2 GB，
**合计约 8–12 GB**。

---

## 6. 两个必须提前问清的外部条件

这两条都在关键路径上，答复未到之前介质的组织方式不能定稿。已登记为 **IR-47** 与 **IR-48**。

**一、虚机按什么基线交付（IR-47）？** 平台基线（含加固、备份代理、监控代理、域加入、
补丁通道）还是裸机？——决定 apt 闭包按什么底座解析，也决定我们能不能发自制镜像。

**二、介质导入的流程是什么（IR-48）？** 要问到：允许的介质形态、体积上限、报批提前期，
以及最关键的一条 —— **病毒扫描网关会不会改写文件**。有些 AV 网关会把压缩包解开
再重新打包，出来的 tar 字节级就变了，我方签名的 `SHA256SUMS` 会全部对不上。
这种事在现场发现就是几天的工期。

---

## 7. 介质必须是「做得出来」的，不是「做过一次」的

八年合同期里每轮补丁都要出一版新介质。所以介质由剧本生成（`90-bundle.yml`，
在**有网的机器上**按 MANIFEST 取料、建索引、出 `SHA256SUMS`），不手工攒。
**手工攒出来的介质，第一次打补丁就会散掉。**

## 8. 验收只有一种做法

拿一台**按 ISO 全新安装、网卡禁用**的虚机，只靠介质把整套东西装起来，
然后**由另一个人**重做一遍，比对版本清单与行为断言；再跑第二遍确认 `changed=0`。

- **网卡必须真的禁用。** 开发机有网，它一直在替我们兜着——`pip download` 每次
  重新解析依赖、`podman save` 不保 digest，两件事都是因为「还能上网」而没被发现
- **必须由另一个人装。** 只有一个人能装出来的东西不叫发布
- **`changed=0` 不等于幂等**，它查的是有没有重复改动，查不出这一步在目标环境里
  能不能执行。见 `troubleshooting.md`
