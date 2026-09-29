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

## Ansible

### 现象：某个 command 任务每次跑都是 changed

- **原因** —— `changed_when` 用「命令输出里有没有某个字符串」来判断，而猜错了字符串。
  本项目已经踩过一次：`locale-gen` 的 `changed_when` 写成
  `'up-to-date' not in stdout`，但 `locale-gen` 根本不输出这个词
  （它输出 `Generating locales... done`），条件恒为真
- **处置** —— 不要猜输出。改成**检查实际状态**：先用一个 `changed_when: false` 的
  只读命令取当前状态（`locale -a`、`timedatectl show -p Timezone --value`、
  `systemctl is-enabled`），再用 `when:` 决定要不要执行变更命令
- **预防** —— 每次改完剧本连跑两遍，第二遍 `changed=0` 才算过。
  这是 runbook/02 里那条「跑第三遍」的理由，不是手滑写重了

### 现象：时区看起来对，但换台机器就不对了

- **原因** —— 用 UTC 偏移判断时区。`+03` 是 Asia/Riyadh、Europe/Moscow、
  Asia/Baghdad、Africa/Nairobi 共用的偏移，比偏移会让设错时区的机器悄悄通过
- **处置** —— 比时区名：`timedatectl show -p Timezone --value`
- **预防** —— 已改入 00-base 与 99-verify

### 现象：`--check` 跑失败，断言里的值是空的

例如：`podman 存储在「」，不在 /var/lib/containers 卷上`——注意引号里是空的。

- **原因** —— `ansible.builtin.command` 在 `--check` 模式下**默认被跳过**
  （Ansible 无法判断一条任意命令有没有副作用，所以一律不执行）。
  跳过之后 `register` 的变量是空的，后面用它的断言就拿空串去比，必然失败。
  这不是环境的问题，是剧本的问题
- **处置** —— 给**只读**查询任务加 `check_mode: false`，让它在 check 模式下也真跑。
  只读命令这么做是安全的；会改变状态的命令**不要**加
- **额外一层** —— 若该查询依赖的东西在 check 模式下还不存在
  （例如 apt 在 check 模式没真装，`podman info` 必然失败），
  再加 `failed_when: false`，并给断言加 `when: not ansible_check_mode`
- **预防** —— 已在 00-base（3 处）、10-runtime（3 处）、99-verify（9 处）全部处理。
  **新增任何 command 任务时先问一句：它在 --check 下会怎样**

判断口诀：**只读 → `check_mode: false`；会改状态 → 什么都不加，让它在 check 下跳过。**
