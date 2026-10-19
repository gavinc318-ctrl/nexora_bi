# 第 9 步 —— 契约同步：把发布的源契约写进库

## 这一步在做什么

`contracts/*.yaml` 是源契约的权威，`meta.source_contract` 是库里的只读副本（LD-01 2.1）。接入程序从库里读契约，
水位表 `meta.watermark_state` 以外键指着它——**契约不进库，接入跑不起来**。这一步就是把契约写进库（DD-88）。

| 文件 | 作用 |
| --- | --- |
| `contracts/release.yml` | 发布清单：本次发布哪些契约。只同步清单里的 |
| `infra/files/contracts_to_json.py` | 在控制节点上读清单与契约，转成 JSON；顺带查必填项、`source_id` 重复、疑似口令的键 |
| `infra/files/contract_sync.sql` | 在库里按规则写入，一个事务，以属主角色执行 |
| `infra/playbooks/45-contracts.yml` | 把上面两步串起来 |

## 先懂三件事

### 一、向前不向后

每份契约对照库里的副本，五种情况：

| 情况 | 结果 |
| --- | --- |
| 库里没有 | 写入；`enabled` 取文件里的值——**只有这一次** |
| 同版本、内容相同 | 不动。比的是 jsonb，改注释、改缩进、调键序都算相同 |
| 同版本、内容不同 | **部署失败**：改了契约却没递增 `contract_version` |
| 文件版本更低 | **部署失败**：库里比这次发布更新，不回退 |
| 文件版本更高 | 先查热调值在新版里还有没有对应的设置，有失效的就**部署失败**并列出来；没有才更新 |

### 二、部署不碰运行配置

`enabled`（启停）进库之后只由 M-01 改，`meta.source_runtime_param`（热调值）同步永不触碰。热调值与新契约
不兼容时，部署失败、由运维在 M-01 清理（留审计）后重跑——**部署步骤不得悄悄改运行配置**（DD-88）。

### 三、契约先于它的 raw 表发布是错误

清单里每一份契约的 `target_raw` 都必须已经是 raw 下的一张表（由结构变更建，第 8 步）。所以现在清单里只有
`if03a_icp_cdr.yaml`；if03b/c/d 等它们的 raw 表建好再加。

## 执行

```bash
cd ~/src/nexora_bi && git pull --rebase
cd infra && ansible-playbook playbooks/45-contracts.yml -e ansible_connection=local -K
```

第一遍：「Sync the contracts into the database」为 changed，结果显示 `icp_cdr_voice|inserted|1`。
第二遍：`icp_cdr_voice|unchanged|1`，`changed=0`。

查看库里的副本：

```bash
sudo -u postgres psql -d oss911 -c "SELECT source_id, contract_version, enabled, synced_at FROM meta.source_contract"
```

## 以后改契约

改 YAML → 递增 `contract_version` → 提交 → 重跑本剧本。忘了递增，第二种失败会拦下来。
