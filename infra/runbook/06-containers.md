# 第 6 步 —— 容器化组件：Keycloak · SeaweedFS · Prometheus

## 这一步在建什么

三件支撑组件，都跑在 podman 的 `internal` 网段上，端口只绑宿主机回环：

| 组件 | 作用 | 开发机端口（仅 127.0.0.1） |
| --- | --- | --- |
| Keycloak | 身份与令牌，后续服务层与门户的认证都走它 | 8080 应用 · 9000 健康与指标 |
| SeaweedFS | 对象存储：现场媒体、发布制品、导出文件 | 9333 master · 8888 filer · 8333 S3 |
| Prometheus | 可用性与指标采集 | 9090 |

## 两条做法上的选择

**用 Quadlet，不用 `podman run`，也不用 `podman generate systemd`。**
Quadlet 是 podman 4.4 起的受支持做法：把 `.container` 文件放进
`/etc/containers/systemd/`，systemd 自己生成服务单元。开机自启、依赖顺序、
重启策略都交给 systemd，离网重建时少一套自制脚本要维护。
`podman generate systemd` 已被上游废弃，不要再用。

**镜像按 digest 钉死，不按 tag。** tag 会被重新发布——同一个
`keycloak:26.0` 今天和半年后可能是两个不同的镜像，而离网环境里你无从发现。
digest 不会变。做法是：

1. 首次运行时 inventory 里的 `digest` 为空，剧本按 tag 拉取，并把解析出的
   digest **打印出来**
2. 当场把它填回 `inventory/dev.yml` 的 `container_images` 与
   `artifacts/MANIFEST.md`
3. 此后每次运行都按 digest 拉取；镜像被换掉会立刻暴露

第 2 步是手工的，这是故意的——写进清单这件事需要有人看一眼，自动写回就没人看了。

## 跑

```bash
# Mac 上
rsync -av --delete infra/ nexora-bi:~/infra/

# 开发机上
cd ~/infra
ansible-playbook playbooks/20-database.yml  -e ansible_connection=local -K   # pg_hba 与监听地址有改动
ansible-playbook playbooks/22-containers.yml -e ansible_connection=local -K
# 把打印出来的三个 digest 填回 inventory 与 MANIFEST，然后：
ansible-playbook playbooks/22-containers.yml -e ansible_connection=local -K   # changed 应为 0
```

第 4 步的剧本要先重跑一遍：容器连宿主机的库，需要 PostgreSQL 监听
`internal` 网段的网关地址并在 `pg_hba` 中放行该网段。这两项归数据库本体管，
所以放在 20-database 里，而不是放在需要它的第 6 步——**配置的归属跟着被配置的
东西走，不跟着「谁需要它」走**。

## 验证：验行为，不验「在运行」

剧本里的三条断言都不是「容器起来了」：

- **Keycloak** —— 健康检查通过之后，再查 `keycloak` 库里建出了多少张表。
  这一条是关键：Keycloak 连不上 PostgreSQL 时会**回落到内置 H2**，照样启动、
  照样健康检查通过，但数据在容器重建时全部消失。只有去库里看有没有表，
  才分得清这两种「成功」
- **SeaweedFS** —— 写入一个对象再读回来比对内容，而不是探活端口
- **Prometheus** —— 查它自己的 `up` 指标是否为 1。
  采不到数据的监控系统比没有监控更坏，因为看板上会是一片平静

## 人工核验

```bash
# 容器与单元状态
sudo podman ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
systemctl status keycloak seaweedfs prometheus --no-pager | head -40

# Keycloak 管理员口令（首次创建时生成）
sudo grep BOOTSTRAP_ADMIN_PASSWORD /etc/oss911/keycloak.env

# Prometheus 的采集目标
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool | grep -E '"health"|"scrapeUrl"'

# 网段隔离仍然成立（第 3 步验过，组件上线后再确认一次）
sudo podman exec prometheus sh -c 'wget -qO- --timeout=3 http://10.89.20.1 2>&1 | head -1'
```

## 开发机不等于生产的三处

| | 开发机 | 生产 |
| --- | --- | --- |
| Keycloak 启动方式 | `start-dev`，免 TLS 与 hostname 严格校验 | `start --optimized`，配证书与 hostname |
| SeaweedFS 拓扑 | 单机 server 模式（master+volume+filer+S3 一体） | 三节点，放置要求见 LD-08 PL-04 |
| Prometheus 留存 | 15 天 | 按 LD-08 的留存要求另定，且与考核期一致 |

对象键与 S3 接口形态两边一致，所以接入与服务层的代码不受这些差异影响——
差异都收在部署形态里，这正是分开记的目的。
