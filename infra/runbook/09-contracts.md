# 第 9 步 —— 契约同步：把发布的源契约写进库

## 这一步在做什么

`contracts/*.yaml` 是源契约的权威，`meta.source_contract` 是库里的只读副本（LD-01 2.1）。接入程序从库里读契约，
水位表 `meta.watermark_state` 以外键指着它——**契约不进库，接入跑不起来**。这一步就是把契约写进库（DD-88）。

| 文件 | 作用 |
| --- | --- |
| `contracts/release.yml` | 发布清单：本次发布哪些契约。只同步清单里的 |
| `oss911-ingest validate-contract` | **契约规则只有这一套**（DD-88 注十四）：在接入程序里，部署与 M-01 都调它。连同库里现有的热调值一起校验 |
| `infra/files/contracts_to_json.py` | 只做转换：把清单里的契约转成 JSON，不再校验 |
| `infra/files/contract_sync.sql` | 在库里按版本规则写入，一个事务，以属主角色执行，加咨询锁串行 |
| `infra/playbooks/45-contracts.yml` | 把上面串起来：复制到部署机 → 校验 → 转换 → 同步，四步都在部署机上、对同一份文件 |

前提：30-python 已装好含 `validate-contract` 的接入程序并生成了 `/etc/oss911/ingest.conf`；40-schema 已建好
`ingest` 库用户与 `ingest.env`。校验以 `airflow` 账号、`ingest` 库用户执行——接入程序真实运行的就是这个身份。

## 先懂三件事

### 一、向前不向后

每份契约对照库里的副本，五种情况：

| 情况 | 结果 |
| --- | --- |
| 库里没有 | 写入；`enabled` 取文件里的值——**只有这一次** |
| 同版本、内容相同 | 不动。比的是 jsonb，改注释、改缩进、调键序都算相同 |
| 同版本、内容不同 | **部署失败**：改了契约却没递增 `contract_version` |
| 文件版本更低 | **部署失败**：库里比这次发布更新，不回退 |
| 文件版本更高 | 更新。热调值与新版是否相容，由前面的校验判定，不相容就在校验那一步失败 |

校验查什么：必填段齐全（endpoint、credential_ref、幂等键、列定义）、版本号为正整数、窗口与回看的类型与范围、
幂等键引用的列在列定义里、**YAML 里有重复键直接拒绝**（PyYAML 默认会静默取后一个）、没有像口令的键。
热调值：回看**只能调大**，不得小于契约默认值——缩短回看只会少数据、不报错，那是口径变更，要改契约走发布。

校验与同步之间若有人在 M-01 改了热调值，同步会失败并提示重跑：同步事务比对的是「校验时看到的值」与
「库里此刻的值」，两者不同就说明校验已不覆盖现状。

### 二、部署不碰运行配置

`enabled`（启停）进库之后只由 M-01 改，`meta.source_runtime_param`（热调值）同步永不触碰。热调值与新契约
不兼容时，部署失败、由运维在 M-01 清理（留审计）后重跑——**部署步骤不得悄悄改运行配置**（DD-88）。

### 三、契约先于它的 raw 表发布是错误

清单里每一份契约的 `target_raw` 都必须已经是 raw 下的一张表（由结构变更建，第 8 步）。所以现在清单里只有
`if03a_icp_cdr.yaml`；if03b/c/d 等它们的 raw 表建好再加。

## 执行

先跑 30-python（装新版接入程序、生成 `ingest.conf` 与开发机的 `icp.env`），再跑本步：

```bash
cd ~/src/nexora_bi && git pull --rebase
cd infra
ansible-playbook playbooks/30-python.yml   -e ansible_connection=local -K
ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
```

手工校验一份契约（不连库）：

```bash
sudo -u airflow /opt/oss911/venv-ingest/bin/oss911-ingest validate-contract /opt/oss911/contracts/if03a_icp_cdr.yaml
```

第一遍：「Sync the contracts into the database」为 changed，结果显示 `icp_cdr_voice|inserted|1`。
第二遍：`icp_cdr_voice|unchanged|1`，`changed=0`。

查看库里的副本：

```bash
sudo -u postgres psql -d oss911 -c "SELECT source_id, contract_version, enabled, synced_at FROM meta.source_contract"
```

## 以后改契约

改 YAML → 递增 `contract_version` → 提交 → 重跑本剧本。忘了递增，第二种失败会拦下来。

## 开发机的 icp.env 与假 ICP 接口

`/etc/oss911/icp.env` 存 ICP 网关地址与服务工号（LD-01 2.5）。生产上口令由 ICP 运维发，运维手写此文件，剧本只
检查齐全（inventory `icp_env.generate: false`）；开发机上账号属于假接口（AS-32），30-python 生成一次随机口令并
保留。启动假接口时用同一账号：

```bash
cd ~/src/nexora_bi
eval "$(sudo grep -E '^OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT_(USER|PASSWORD)=' /etc/oss911/icp.env)"
python3 test/fake_icp/server.py --port 18080 --seed 1 \
  --account "$OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT_USER:$OSS911_CRED_ICP_CMS_SERVICE_ACCOUNT_PASSWORD"
```

口令会出现在假接口进程的命令行里（`ps` 看得到）。假接口只在开发机、只监听 127.0.0.1，可以接受；这条做法
不得照搬到任何真实服务。
