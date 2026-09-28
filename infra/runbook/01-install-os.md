# 01 装系统

## ISO

见 `../artifacts/MANIFEST.md`。下载与校验命令见本项目对话记录或下方。

## 安装器选项

| 项 | 选择 | 理由 |
| --- | --- | --- |
| 安装类型 | Ubuntu Server (minimal) | 少装少维护，离线补丁面也小 |
| 网络 | 静态 IP | DHCP 变址会让 inventory 失效 |
| 存储 | **Custom storage layout → LVM** | 默认布局是单个大卷，不可用 |
| 时区 | Asia/Riyadh | 日切、封账、归日口径依赖它 |
| 语言 | English / en_US.UTF-8 | 系统英文；阿语只在数据与界面层 |
| SSH | 安装 OpenSSH，导入公钥，**禁用口令登录** | |
| Snap | 不额外勾选任何 snap | |
| Docker | **不要勾** | 本项目用 Podman（DD-11） |

## 分卷

在 Custom storage layout 里建一个卷组（例如 `vg0`），按下表分逻辑卷。
**留 80 GB 不分配。**

| 逻辑卷 | 大小 | 挂载点 | 文件系统 |
| --- | --- | --- | --- |
| root | 80 G | `/` | ext4 |
| containers | 80 G | `/var/lib/containers` | ext4 |
| pgdata | 350 G | `/var/lib/postgresql` | ext4 |
| pgwal | 40 G | `/var/lib/postgresql/wal` | ext4 |
| seaweedfs | 60 G | `/var/lib/seaweedfs` | ext4 |
| buildcache | 60 G | `/var/cache/build` | ext4 |
| pgbackup | 50 G | `/var/backups/pg` | ext4 |
| （未分配） | 80 G | — | — |

**WAL 单独成卷**是为了排障：WAL 涨满与数据涨满是两种不同的故障，
合在一个卷里看到的现象都是「磁盘满」，查因要多花一小时。

## 装完后的自检

```bash
lsblk                       # 卷是否都在
df -h                       # 挂载点与容量是否符合上表
vgs && lvs                  # 卷组是否还有 ~80G 空闲
timedatectl                 # Time zone 必须是 Asia/Riyadh
locale                      # LANG=en_US.UTF-8
ssh -o PasswordAuthentication=no <user>@<host> true   # 密钥登录可用
grep -E '^PasswordAuthentication' /etc/ssh/sshd_config # 应为 no
```

七条全过才进第 2 步。任一条不对，现在改比装完一堆东西之后改便宜得多。
