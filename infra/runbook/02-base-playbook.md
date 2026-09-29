# 02 系统基线剧本

## 前置

- 第 01 步的七条自检全过
- 你的工作机上有 Ansible（`pipx install ansible` 或 `apt install ansible`）
- `ssh nexora-bi true` 能免密登录

## 执行

```bash
cd infra
ansible-playbook playbooks/00-base.yml --check --diff   # 先空跑看会改什么
ansible-playbook playbooks/00-base.yml                  # 实际执行
ansible-playbook playbooks/99-verify.yml                # 体检
```

**先 `--check` 再实跑**，这是剧本第一次在一台机器上运行时的规矩。空跑会列出它打算改什么，
有意外就停下来看，别让剧本在一台你还不熟的机器上直接动手。

## 幂等自检

```bash
ansible-playbook playbooks/00-base.yml        # 第二遍
```

第二遍的 `changed=` 应当为 0（`timedatectl` 与 `locale-gen` 两个 command 任务
已加条件与 changed_when，不会虚报）。**不为 0 就是剧本有问题**，要查是哪个任务
每次都在改——离网重建依赖幂等，一个每次都变的任务意味着重建结果不确定。

## 99-verify 什么时候跑

不只是构建后。**机器跑了三个月之后随时可以跑**——它只读，不改任何东西。
用它回答「这台机器还是不是剧本描述的那台」：有人手改过什么、卷满了没有、
服务掉了没有、SSH 加固还在不在。

## 这一步做了什么

| 组 | 内容 |
| --- | --- |
| 断言 | OS 版本、内存与 CPU、七个卷的挂载与容量、卷组预留空间 |
| 本地化 | 时区 Asia/Riyadh、locale en_US.UTF-8 |
| 包 | 基线包集（见 inventory 的 `base_packages`） |
| 采集 | sysstat 启用并自启 |
| 内核 | swappiness、脏页按字节、文件句柄、inotify、连接队列 |
| 纪律 | 停用 unattended-upgrades 与两个 apt 定时器 |

## 为什么停用自动升级

版本基线要可重建，而 `unattended-upgrades` 会在无人知晓的情况下改变包版本，
使机器与 `artifacts/MANIFEST.md` 悄悄分叉。

**这不是不打补丁**，是把补丁从后台动作变成受控动作：按 `03-patching.md` 执行并留痕。
这也更贴近生产——生产无外网，补丁本来就只能经内网镜像源受控导入（LD-09 第 7 章）。

代价要认：开发机有外网且不再自动打补丁，安全责任转移到了流程上。所以
`03-patching.md` 的执行周期必须真的有人执行，不能写了不做。

## 内核参数：开发与生产不是一套

`/etc/sysctl.d/90-oss911-dev.conf` 里写明了这一点，这里再说一次：

- **开发机**混跑 PG、Superset、Airflow、前端构建，`vm.overcommit_memory` 保持默认 0。
  PostgreSQL 官方建议专用库机设 2，但那几个组件都按需大额申请内存，设 2 会造成大量伪失败
- **生产的库虚机**按 RS-01：禁用超分、内存 100% 预留、禁用气球与交换，
  届时 overcommit 按 PostgreSQL 官方建议处理

把开发机的这份 sysctl 复制到生产，是可预见的错误之一，故在文件头部写了警告。
