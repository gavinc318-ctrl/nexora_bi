# 排障手册

边搭边写。**每解决一个问题就加一条**——当时觉得「这么明显下次肯定记得」的，
三个月后一定不记得，而离网环境里没有搜索引擎。

## 条目格式

每条四段，缺一不可：

- **现象** —— 看到的原话（报错原文、日志片段），不是你对它的概括。
  概括之后就搜不到了
- **原因** —— 真正的原因，不是「重启一下就好了」
- **处置** —— 可复制粘贴的命令
- **预防** —— 有没有写进剧本让它不再发生。**没有的话这条就没完**

---

## 磁盘

### 现象：`could not extend file ... No space left on device`

- **原因** —— pgdata 卷满。注意与 pgwal 卷满区分：后者报的是
  `could not write to log file` 一类，两者处置完全不同
- **处置** —— `df -h /var/lib/postgresql` 确认是哪个卷；
  `lvextend -L +20G /dev/vg0/pgdata && resize2fs /dev/vg0/pgdata`（用预留的 80G）
- **预防** —— 监控加卷水位告警；扩容后把新尺寸回填 `inventory/dev.yml`

---

（以下边搭边加）

## 重建与漂移

### 「这台机器上有什么是剧本没覆盖的？」

- **现象** —— 怀疑有人手敲过命令，或重建出来的机器与现有的不一致
- **原因** —— 手工操作未写进剧本
- **处置** ——
  ```bash
  zgrep -h 'Commandline: apt' /var/log/apt/history.log* | sort   # 所有手敲的 apt
  ansible-playbook playbooks/99-verify.yml                        # 与剧本的差异
  ```
  APT 的 history 会记下每一条手敲的安装命令，不用凭记忆。安装器自己跑的那些
  （`grub-pc`、`linux-generic`、`openssh-server`，以及 unminimize 的大批 `--reinstall`）
  可以忽略，要看的是 `apt install` 那几条
- **预防** —— 把发现的包加进 `inventory/dev.yml` 的 `base_packages`，重跑 00-base

---

## 系统

### 现象：`locale-gen: command not found`

- **原因** —— minimal 装机不含 `locales` 包
- **处置** —— `sudo apt install -y locales`
- **预防** —— 已在 `base_packages` 中

### 现象：改了 `/etc/ssh/sshd_config` 里的 PasswordAuthentication 但不生效

- **原因** —— Ubuntu 24.04 的 `sshd_config` 末尾有 `Include /etc/ssh/sshd_config.d/*.conf`，
  该目录里的设置覆盖主文件。主文件里那些是被注释掉的默认值，grep 出来会误导
- **处置** —— 写进 `/etc/ssh/sshd_config.d/60-hardening.conf`；
  用 `sudo sshd -T | grep passwordauthentication` 看**实际生效值**，不要 grep 配置文件
- **预防** —— 99-verify 用 `sshd -T` 断言

### 现象：重启后网络配置 / 主机名被改回去了

- **原因** —— 若虚机由云镜像创建，`cloud-init` 会在每次启动时按其数据源重写
  netplan 与 hostname
- **处置** —— 确认是否装有 cloud-init（`systemctl status cloud-init`）；
  确需固定配置时按 cloud-init 的方式声明，或按官方方式禁用它，不要只改 netplan
- **预防** —— 尚未处理。若这台机器是云镜像来的，需在 00-base 中显式处置

### 现象：`sar` 查不到历史数据

- **原因** —— `sysstat` 装了但默认不采集，`/etc/default/sysstat` 里 `ENABLED="false"`
- **处置** —— 见 00-base 的 sysstat 任务
- **预防** —— 已在 00-base 中，99-verify 会断言服务为 active
