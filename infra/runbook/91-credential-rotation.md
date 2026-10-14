# 口令：存在哪里、怎么轮换、引导管理员怎么换掉

> 起因：NOTES.md「2026-10-12 Mac 侧移交」第一节第 1 至 3 项。
> 本文**不含任何口令值**。所有查看口令的命令都只显示「有没有」，不把口令打到终端上。

## 一张表：每个口令的唯一存放处

| 库用户 / 账号 | 唯一存放处（0600） | 谁读它 | 由哪个剧本管 |
| --- | --- | --- | --- |
| `pgbouncer`（auth_user） | `/etc/pgbouncer/userlist.txt` | pgBouncer | 21-connection |
| `keycloak` | `/etc/oss911/keycloak.env` 的 `KC_DB_PASSWORD` | Keycloak 容器 | 22-containers |
| `airflow` | `/etc/oss911/airflow.env` 的连接串 | Airflow 四个组件 | 30-python |
| `ingest`、`dbt` | `/etc/oss911/{ingest,dbt}.env` | Airflow 调起的接入与 dbt | 40-schema |
| Keycloak 引导管理员 | `/etc/oss911/keycloak.env` 的 `KC_BOOTSTRAP_ADMIN_*` | 仅首次启动 | 22-containers（只在首次安装时生成） |
| Keycloak 正式管理员 | **不在本机任何文件里**，由管理员本人保管 | — | 手工（本文第三节） |
| `pgb_probe` | 不存放：21 每次运行临时生成，验完即删 | — | 21-connection |

**「唯一存放处」是轮换机制的基础**：剧本每次运行都先读这个文件——读得到就沿用，
读不到就生成新口令并 `ALTER ROLE`。所以：

> **轮换某个口令 = 删掉它的存放文件，重跑对应剧本。**

剧本会生成新口令、改库里的口令、写回文件、重启读这个文件的服务，再以真实使用方
的身份连一次库来验证。

## 口令为什么不会泄露出去

口令写进库只有一个入口：`infra/playbooks/tasks/role_password.yml`，四个剧本共用。
它堵三条路：

| 路径 | 以前 | 现在 |
| --- | --- | --- |
| 命令行 | `psql -c "CREATE ROLE … PASSWORD '…'"`，执行期间本机任何用户 `ps` 都看得见 | 口令只走标准输入 |
| 服务端日志 | 语句出错，或执行超过 200ms，原文连同口令写进 PostgreSQL 日志 | 同一事务内 `SET LOCAL` 关掉 `log_min_error_statement`、`log_min_duration_statement`、`log_statement` |
| Ansible 输出 | — | 这一步 `no_log`；失败原因另行输出，并先掩掉其中的 `PASSWORD '…'` |

这一套已在临时实例上实测：日志开到「记录全部语句」，成功和出错两种情况下日志里
都找不到口令；不加 `SET LOCAL` 的对照组，两种情况口令都以原文进了日志。

另一处修正：pgBouncer 验证 auth_query 用的临时用户 `pgb_probe`，口令以前是
「`probe_` + pgbouncer 口令前 12 位」，并且写在命令行上，等于把 pgbouncer 口令的
大半截暴露给了 `ps`。现在改为每次独立随机生成。

## 这一次怎么做（按顺序）

### 第 0 步  拉代码，确认新剧本不改动任何现状

```bash
cd ~/src/nexora_bi && git pull && git status --short     # 工作区应为空
cd infra
for p in 21-connection 22-containers 30-python 40-schema; do
  ansible-playbook playbooks/$p.yml -e ansible_connection=local -K --diff
done
```

`--diff` 让剧本把它改动的每个文件逐行打出差异。预期 `changed=0` 的这一遍一定要加：
万一真有变化，不加 `--diff` 就只知道「改了」不知道「改了什么」，而旧内容已被覆盖、
事后无从查起（2026-10-13 第一次执行时 22 的三个 Quadlet 单元就这样被改写，原因至今不明）。
口令相关的任务都是 `no_log`，`--diff` 不会把口令打出来。

四个剧本都应 **`changed=0`**：口令沿用文件里的、环境文件内容不变、服务不重启。
这一步验证的是新写法没有破坏现状。21 的验证用户 `pgb_probe` 这时已经在用随机口令
（它不计入 changed）。

### 第 1 步  查历史日志里有没有口令（只数，不显示内容）

```bash
sudo bash -c 'grep -l "PASSWORD '"'"'" /var/lib/postgresql/16/main/log/*.log 2>/dev/null | wc -l'
```

输出 0 就说明以前的建用户语句没有进过服务端日志（它们既没出错，也没超过 200ms）。
不是 0 时，**不要用 grep 去看内容**（那会把口令打到终端上），把数字贴回来，
轮换完成后再一起处理那几份日志文件。

### 第 2 步  换掉 Keycloak 引导管理员（手工，见第三节）

**必须在轮换 keycloak 库口令之前做。** 第 4 步要删掉 `keycloak.env`，引导管理员的
口令也存在这个文件里，会一起没掉。

### 第 3 步  轮换 pgbouncer 的口令

```bash
sudo rm /etc/pgbouncer/userlist.txt
ansible-playbook playbooks/21-connection.yml -e ansible_connection=local -K
```

预期：`pgbouncer: password updated`，`Write the userlist` 为 changed，pgBouncer **reload**（重读口令文件，不断已有连接；
2026-10-13 之前的剧本在这里 restart、要等 90 秒，见 troubleshooting 第 29 条）；
`Assert the two pools really have different pool modes` 用的就是新口令；`Assert auth_query works` 也通过。

pgbouncer 是 auth_user，**其他所有用户经 pgBouncer 登录都要靠它去库里查口令**。
所以紧接着确认其他服务能重新连上：

```bash
# Keycloak（容器经 pgBouncer 会话池连库）
curl -s -o /dev/null -w 'keycloak ready: %{http_code}\n' http://127.0.0.1:9000/health/ready
# Airflow
sudo -u airflow bash -c 'set -a; . /etc/oss911/airflow.env; set +a; /opt/oss911/venv-airflow/bin/airflow db check' 2>&1 | tail -1
# dbt
sudo -u airflow bash -c 'cd /tmp; set -a; . /etc/oss911/dbt.env; set +a;
  /opt/oss911/venv-dbt/bin/dbt debug --connection --profile oss911 --profiles-dir /var/lib/airflow' 2>&1 | tail -1
```

预期依次为：`keycloak ready: 200`、`Connection successful.`、`All checks passed!`。

再跑一遍 21，应 `changed=0`。

### 第 4 步  轮换 keycloak 的库口令

```bash
sudo rm /etc/oss911/keycloak.env
ansible-playbook playbooks/22-containers.yml -e ansible_connection=local -K
```

预期：`keycloak: password updated`，环境文件重写，**只有 Keycloak 重启**（SeaweedFS、Prometheus
不动）；`Assert Keycloak is ready` 与 `Assert Keycloak really uses PostgreSQL` 两条断言通过。
新的 `keycloak.env` 里只有 `KC_DB_PASSWORD` 与 `KC_DB_USERNAME` 两行：引导管理员已在
第 2 步去掉，剧本不会再加回来。

```bash
sudo cut -d= -f1 /etc/oss911/keycloak.env      # 只看变量名：应为 KC_DB_PASSWORD、KC_DB_USERNAME
```

再跑一遍 22，应 `changed=0`。之后用正式管理员登录一次管理控制台，确认无误。

### 第 5 步  轮换 airflow 的库口令

```bash
sudo rm /etc/oss911/airflow.env
ansible-playbook playbooks/30-python.yml -e ansible_connection=local -K
```

预期：`airflow: password updated`，环境文件重写，Airflow 四个组件全部重启；`Metadata database initialised`
（`airflow db migrate` 用的是新口令）通过。`airflow.env` 的其余内容都由模板生成，
删掉不会丢任何东西。

```bash
systemctl is-active airflow-api-server airflow-scheduler airflow-dag-processor airflow-triggerer
```

预期四个 `active`。再跑一遍 30，应 `changed=0`。

### 第 6 步  `usr` 的 sudo 口令（不经剧本）

```bash
passwd
```

（NOTES.md 移交第 4 项：该口令曾以明文出现在 Mac 侧的对话记录里。）

## 第三节  换掉 Keycloak 引导管理员（手工）

Keycloak 26 的引导管理员**设计上就是临时的**：它只用于首次启动后建出第一个正式
管理员，控制台会一直提示「temporary admin」。我们的引导管理员口令还曾以明文出现在
Mac 侧的对话记录里，所以无论如何都要换掉。

Keycloak 只监听本机回环，从 Mac 访问要开隧道：

```bash
# Mac 上（保持这个窗口开着）
ssh -L 8080:127.0.0.1:8080 nexora-bi
```

1. 取引导管理员口令（在开发机上）：
   `sudo grep '^KC_BOOTSTRAP_ADMIN_PASSWORD=' /etc/oss911/keycloak.env | cut -d= -f2-`
   复制后清屏（`clear`）
2. Mac 浏览器打开 `http://localhost:8080/admin/`，用户名 `admin`，口令用上一步取到的。
   页面上方会有「temporary admin」提示，这是正常的
3. 确认左上角 realm 是 **master**
4. **Users → Add user**（或 Create new user）：用户名用个人名，**不要再叫 `admin`**，
   例如 `gavin-admin`。点 Create
5. 进入该用户的 **Credentials** 标签 → **Set password**：设一个强口令，
   **Temporary 关掉** → Save，确认
6. **Role mapping** 标签 → **Assign role** → 过滤器选 realm roles → 勾选 **`admin`** → Assign
7. 右上角退出；**用新管理员登录**，确认：没有 temporary admin 提示，能打开
   Users 与 Realm settings。**确认这一步成功之前，不要做第 8 步**
8. Users → 找到 `admin`（引导管理员）→ 右侧 ⋮ → **Delete** → 确认。
   核对删的是 `admin`，不是你刚建的那个
9. 回到开发机，从环境文件里去掉引导管理员的四行，然后重启 Keycloak：
   ```bash
   sudo sed -i '/^KC_BOOTSTRAP_ADMIN_/d; /^KEYCLOAK_ADMIN/d' /etc/oss911/keycloak.env
   sudo cut -d= -f1 /etc/oss911/keycloak.env    # 应只剩 KC_DB_PASSWORD、KC_DB_USERNAME
   sudo systemctl restart keycloak
   sleep 30; curl -s -o /dev/null -w 'keycloak ready: %{http_code}\n' http://127.0.0.1:9000/health/ready
   ```
10. 用新管理员再登录一次，确认无误。然后跑一遍 22：应 `changed=0`，剧本不会把那四行加回来

新管理员的口令**只由你本人保管**（口令管理器），不写进本机任何文件，也不贴进任何对话。

**失败了怎么办**：第 7 步发现新管理员登不进去或权限不够时，引导管理员还在，回到第 4 步
重来即可。万一两个管理员都进不去，Keycloak 26 可以在容器里重新建一个临时管理员：
`sudo podman exec -it keycloak /opt/keycloak/bin/kc.sh bootstrap-admin user`。
这条命令要在 Keycloak 停止服务的状态下执行，做之前先把情况贴回来。
