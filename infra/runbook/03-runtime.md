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

```bash
# 在两个网段各起一个临时容器
sudo podman run -d --name t-int --network internal docker.io/library/alpine sleep 600
sudo podman run -d --name t-dmz --network dmz      docker.io/library/alpine sleep 600

# 同网段内按名字能解析、能通（验证 aardvark-dns 在工作）
sudo podman run --rm --network internal docker.io/library/alpine ping -c1 -W2 t-int

# 跨网段应当不通 —— 这条「失败」才是对的
sudo podman run --rm --network dmz docker.io/library/alpine ping -c1 -W2 t-int ; echo "退出码 $?（非 0 才正确）"

# 清理
sudo podman rm -f t-int t-dmz
```

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
