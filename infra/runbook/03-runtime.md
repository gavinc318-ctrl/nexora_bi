# 03 容器运行时与网络

## 执行

```bash
# Mac 上同步
rsync -av --delete infra/ nexora-bi:~/infra/

# 开发机上
cd ~/infra
ansible-playbook playbooks/10-runtime.yml -e ansible_connection=local -K --check --diff
ansible-playbook playbooks/10-runtime.yml -e ansible_connection=local -K
ansible-playbook playbooks/10-runtime.yml -e ansible_connection=local -K   # changed 应为 0
ansible-playbook playbooks/99-verify.yml  -e ansible_connection=local -K
```

## 本环境的 podman 命令一律加 sudo

**这是本步最容易踩的坑，先读完再往下。**

剧本带 `become: true`，所有容器与网络都建在 **rootful** podman 下
（`/etc/containers/networks/`、`/var/lib/containers/storage`）。

直接敲 `podman`（不加 sudo）走的是 **rootless** podman——它有自己的存储
（`~/.local/share/containers`）、自己的网络配置，与 root 那一套**完全不相通**。
两者都叫 podman，`podman --version` 也都能跑，但看到的东西是两个世界。

典型症状：剧本明明创建成功，`podman run --network internal` 却报
`unable to find network with name or ID internal`。此时 `sudo podman network ls`
能看到，`podman network ls` 看不到。

还有一层代价：rootless 的镜像存储在家目录下，也就是**根卷**上——
正是 10-runtime 那条「镜像存储必须在独立卷」断言要防的情况。
误用 rootless 拉几个大镜像就能把根卷撑满。

一句话规矩：**本环境的 podman 命令一律 `sudo podman`。**

```bash
sudo podman ps
sudo podman network ls
sudo podman images
```

为什么不改用 rootless：这是共用开发机，rootful 意味着全队看到同一套容器；
rootless 则每人一套，`podman ps` 各看各的，排障时说不清在说哪个。见本文末尾的取舍说明。

## 手工验证网络确实隔离

剧本的断言只确认网段「存在」。**存在不等于隔离有效**，跑一次真实验证：

**关键：必须按 IP 验证，按名字验证会骗人。**

跨网段按名字访问一定失败（aardvark-dns 按网段划分解析范围），看起来像隔离生效，
其实只是名字查不到。而 podman 的桥接网段**默认不做 IP 层隔离**——两个网桥都在
同一台宿主机上，宿主机在它们之间路由。本项目实测确认过：未加 `isolate=true` 时，
dmz 的容器能直接 ping 通 internal 的 IP。

```bash
# 起一个容器并取它的 IP
sudo podman run -d --name t-int --network internal docker.io/library/alpine sleep 600
IP=$(sudo podman inspect t-int --format '{{ .NetworkSettings.Networks.internal.IPAddress }}')
echo "t-int 的 IP：$IP"

# 一、同网段按名字能通（验证 aardvark-dns 在工作）
sudo podman run --rm --network internal docker.io/library/alpine ping -c1 -W2 t-int

# 二、跨网段按名字不通 —— 只证明名字隔离，不证明网络隔离
sudo podman run --rm --network dmz docker.io/library/alpine ping -c1 -W2 t-int ; echo "退出码 $?（非 0 正确）"

# 三、★ 跨网段按 IP 不通 —— 这条才是隔离的真正判据
sudo podman run --rm --network dmz docker.io/library/alpine ping -c1 -W2 "$IP" ; echo "退出码 $?（非 0 才正确）"

# 清理
sudo podman rm -f t-int
```

**第三条通了就是问题。** 此时 DMZ 边界是假的：服务层本该只能经反向代理访问，
而 dmz 里任何容器都能直连 internal 的 IP。移动端（IF-40）的约束、LD-07 的边界，
测出来都会「通过」，因为根本没有边界。

处置：确认网段带 `isolate=true`。剧本创建时已带，若是早期建的旧网段则需重建：

```bash
sudo podman network rm -f internal dmz     # 会断开挂在其上的容器
ansible-playbook playbooks/10-runtime.yml -e ansible_connection=local -K
sudo podman network inspect internal --format '{{ .Options.isolate }}'   # 应为 true
```

剧本会校验已有网段的 `isolate`，不为真时报错并给出重建命令——但**不自动重建**，
因为重建会断掉挂在上面的容器，那种事必须由人决定。

第三条命令**必须失败**。若它成功了，说明两个网段实际是连通的，
LD-04 第 9 章与 LD-07 关于移动端的约束在开发环境就是空的——
移动端受众拒绝记录级接口这类规则会「看起来实现了」但从未被真正穿过代理验证。

## 这一步做了什么

| 项 | 内容 |
| --- | --- |
| 运行时 | podman · netavark · aardvark-dns · catatonit · uidmap · slirp4netns |
| 存储 | 断言 graphroot 落在 `/var/lib/containers` 独立卷上 |
| 网络 | `internal` 10.89.10.0/24 · `dmz` 10.89.20.0/24 |
| 镜像源 | `/etc/containers/registries.conf.d/10-oss911.conf`，含离网切换的注释模板 |
| 纪律 | 停用 `podman-auto-update.timer` |

## 为什么显式装 netavark 与 aardvark-dns

这两个在 Ubuntu 是 `Recommends` 而不是 `Depends`。任何人用
`--no-install-recommends` 装 podman，容器之间就无法按名字互相解析——
而**报出来的错是「连接被拒绝」，看不出是 DNS 的问题**，能查半天。
写进 `container_packages` 让它不会发生。

99-verify 单独断言 `aardvark-dns` 已安装，就是为了让这类「装了 podman 但网络不对」
的状态一眼可见。

## 镜像源：现在不改，但路铺好

开发机有外网，现在用上游默认源。`10-oss911.conf` 里写好了离网切换的模板：
取消三行注释、填入内网仓库地址，全部拉取即改道内网，**业务容器的镜像引用不用改**。

现在建这个文件的价值在于：到离网那天不用从头研究 `registries.conf` 的写法，
也不用逐个容器改镜像地址。

## rootful 还是 rootless

当前是 **rootful**（容器以 root 身份由 systemd 管理）。选它的理由是简单：
bind mount 到 LVM 卷不需要处理 subuid/subgid 映射，权限行为可预测，
团队共用时不依赖某个人的 user lingering。

**代价要认**：rootless 的隔离更好。生产部署时应重新评估这一项——
`uidmap` 已经装上，改造的前置条件具备。此项列为待决，不要默认沿用开发环境的选择。
