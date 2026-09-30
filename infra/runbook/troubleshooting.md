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

### 现象：剧本说网络已创建，`podman run --network internal` 却报 network not found

- **原因** —— rootful 与 rootless 是两套独立的 podman。剧本带 `become: true`，
  网络建在 root 那套（`/etc/containers/networks/`）；不加 sudo 敲 `podman`
  走的是 rootless 那套（`~/.local/share/containers`），两者互不相通。
  一个旁证：rootless 下 `podman run` 会**重新拉一遍镜像**——若是同一套存储，
  镜像早就在了
- **处置** —— `sudo podman network ls` 确认网络在 root 那套；
  本环境所有 podman 命令一律加 `sudo`
- **附带风险** —— rootless 的镜像存储在家目录，也就是**根卷**上，
  正是「镜像存储必须落独立卷」那条断言要防的情况。误用 rootless
  拉几个大镜像就能撑满根卷。清理：`podman rmi <镜像>`（不加 sudo，清的是 rootless 那套）
- **预防** —— 已写入 runbook/03 开头。新人上手先读那一段

### 现象：`Error: netavark: No such file or directory (os error 2)`

网络明明已创建（`sudo podman network ls` 看得到），起容器却报这个。

- **原因** —— **报错信息是误导性的**。缺的不是 netavark 自己，是 netavark 配置
  网桥时要调用的 `iptables`——Ubuntu Server minimal 装机不含 `iptables` 用户态包。
  netavark 找不到它，把错误原样往上抛，于是看起来像 netavark 不存在
- **确认** ——
  ```bash
  ls -l /usr/lib/podman/netavark    # 二进制在，说明不是 netavark 的问题
  which iptables                     # 无输出即确诊
  ```
- **处置** —— `sudo apt install -y iptables`，或重跑 10-runtime（已加入 container_packages）
- **预防** —— 已在 `container_packages` 中。这类**非正式依赖**（不在 Depends 里、
  但缺了就不工作）尤其要写进剧本与 MANIFEST：离网重建时没人会想到要导入 iptables

这是本项目第二次被 minimal 装机的缺包绊住（第一次是 `locales` 缺 `locale-gen`）。
规律是：**minimal 镜像缺的不是「工具」，是「别人默认你有」的东西**，
而报错往往指向调用方而不是缺失方。

### 现象：容器名被占用，但 `podman ps -a` 里没有它，`podman rm` 也删不掉

```
Error: creating container storage: the container name "t-int" is already in use by <id>
```

- **原因** —— 容器创建分两层：先在存储层占名建层，再登记进 podman 的容器数据库。
  若在两者之间失败（本项目实测：netavark 缺 iptables，网络配置那步报错），
  **名字被存储层占住但没进数据库**。于是 `podman ps -a` 看不到、`podman rm` 删不掉，
  而创建时的重名检查却查得到
- **处置** ——
  ```bash
  sudo podman rm --storage t-int        # 专清存储层残留
  sudo podman rm --storage <报错里的 ID>  # 名字也对不上时用 ID
  ```
- **预防** —— 起临时容器时带 `--replace`（注意是 `--replace`，不是 `-- replace`；
  多一个空格会让 podman 把后面的词当镜像名去拉）
- **顺带** —— `sudo podman ps -a` 看不到不等于不存在。存储层的实况用
  `sudo podman ps -a --storage` 查

### 现象：两个 podman 网段之间居然是通的

- **原因** —— podman 的桥接网段**默认不做网段间隔离**。两个网桥都在同一台宿主机上，
  宿主机会在它们之间路由。本项目实测：未加 `isolate=true` 时，
  dmz 的容器可直接 ping 通 internal 容器的 IP
- **为什么容易漏掉** —— 跨网段按**名字**访问会失败（`ping: bad address`），
  因为 aardvark-dns 按网段划分解析范围。这看起来像隔离生效了，
  但它只证明名字查不到，不证明包过不去。**必须按 IP 验证**
- **处置** —— 网段创建时加 `--opt isolate=true`；已建的要删掉重建
  （`podman network rm -f <名>` 会断开挂在其上的容器）
- **预防** —— 10-runtime 创建时已带该选项，并对已有网段断言 `isolate` 为真。
  runbook/03 的验证步骤以按 IP 的那条为准

这一条是本项目「断言通过 ≠ 功能正确」的最好例子：剧本能断言网段存在、
名字解析正常，但这些全对的情况下，安全边界仍然可以是零。
**凡是「隔离」「拒绝」「不可达」类的设计，验证时必须让它真的失败一次。**

### 现象：`--check` 跑 20-database.yml，在「建立 WAL 目录」报 `failed to look up user postgres`

- **原因** —— check 模式没有真的 `apt install postgresql-16`，
  而 `postgres` 这个系统用户是**装包时由 postgresql-common 的 postinst 建的**。
  包没装 → 用户不存在 → 下一步 `owner: postgres` 找不到人。
  同理不存在的还有 `/etc/postgresql/16/main/`、`postgresql@.service` 单元
- **不要逐个打补丁** —— 这已经是本项目 check 模式第三次绊人
  （前两次：`command` 任务被跳过导致断言读到空串；`locale -a` 查询同理）。
  本质是 **`--check` 无法模拟「装包 → 包建用户/目录/服务 → 下一步配置它们」这条链**。
  对一个「先装软件再配置软件」的剧本，check 模式**天生只能验到安装之前**
- **处置** —— 装包之后的任务整段放进
  ```yaml
  - name: 集群创建与配置
    when: not ansible_check_mode
    block:
  ```
  装包之前的任务（createcluster.conf、包列表）不加护栏，让 check 真正发挥作用
- **预防** —— 写剧本时先问一句：这个任务依赖的用户 / 路径 / 服务，
  是不是同一个 play 里前面某个包装出来的？是就必须加护栏。
  硬凑只会让人学会忽略 `--check` 的失败，那比没有 check 更糟
- **代价说明** —— 因此 20-database.yml 的 `--check` 只能告诉你
  「配置文件会写成什么、包列表对不对」，**不能告诉你集群能不能建起来**。
  集群是否正确，靠真实执行后 runbook/04 的人工核验（`\l` 三列 + ICU 排序实测）

### 现象：`下发 PostgreSQL 参数` 报 conf.d 不存在；补了目录后又发现 postgresql.conf 里没有 include_dir

- **根因（一个，不是两个）** —— 本剧本在装包前整份写了
  `/etc/postgresql-common/createcluster.conf`，只放了 `create_main_cluster = false`。
  发行版自带的同名文件里还有 `add_include_dir = 'conf.d'`，
  **整份覆盖等于把它一起抹掉**。于是 `pg_createcluster` 既不建 conf.d 目录，
  也不往 postgresql.conf 里写 `include_dir`
- **为什么不是装完就报错** —— 参数文件没地方放时才会暴露。
  更糟的分支是：目录被手工建出来、文件也写进去了，但 `include_dir` 仍然缺，
  于是参数**静默不生效**，`\l` 正常、服务正常、一切看起来对。
  与网段隔离那次同一性质
- **处置** ——
  1. createcluster.conf 补回 `add_include_dir = 'conf.d'`（对**将来**重建的集群有效）
  2. 剧本补 `建立 conf.d 目录` 与 `确保 postgresql.conf 引入 conf.d`（lineinfile，幂等），
     救**已经建好**的集群——集群一旦创建，改 createcluster.conf 不会回头生效
  3. 下发参数后 `flush_handlers` 重启，再 `SHOW shared_buffers` 断言实际值为 8GB，
     验的是「加载了」而不是「写成功了」
- **通用教训** —— **凡整份覆盖发行版自带的配置文件，必须先看原文件里还有什么仍然需要的项。**
  离网重建时这类坑没有网可查，只能靠这份记录
- **排查用** ——
  ```bash
  sudo -u postgres psql -tAc "SELECT setting, sourcefile FROM pg_settings WHERE name='shared_buffers'"
  ```
  `sourcefile` 指向哪个文件，哪个文件才是真正生效的

### 现象：`启用扩展` 每次都 changed，剧本永远不收敛到 changed=0

- **原因** —— `CREATE EXTENSION IF NOT EXISTS postgis` 即使**什么都没做**，
  也照样返回命令标签 `CREATE EXTENSION`（"没做"只体现在 stderr 的 NOTICE 里）。
  原来的 `changed_when: "'CREATE EXTENSION' in _ext.stdout"` 于是恒为真
- **处置** —— 改成先查后建：`SELECT extname FROM pg_extension` 取现状，
  再用 `when: item not in (...)` 决定是否执行
- **通用教训** —— **SQL 的命令标签只说明语句执行成功，不说明状态发生了变化。**
  凡 `IF NOT EXISTS` / `OR REPLACE` / `CREATE ... IF NOT EXISTS` 这类幂等语句，
  都不能靠输出判断 changed，只能先查现状。
  后面写 DDL 部署剧本时会大量遇到这个问题
- **为什么要在意** —— 一个永远 changed 的任务会让「第二遍 changed=0」这条
  验收标准失效，而那是离网重建时判断"环境是否已就位"的主要手段
