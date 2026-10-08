# -*- coding: utf-8 -*-
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conv import convert
# 仓库根由脚本自身位置推出。不要写成某台机器上的绝对路径——
# 这些脚本要在 Mac 与开发机上都能跑。
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT  = os.path.join(BASE, 'LLD')
os.makedirs(OUT, exist_ok=True)

BOOKS = [
 ('LD-00_详细设计说明与文档索引.docx','LD-00-overview.md','LD-00','详细设计说明与文档索引','—',
  '先读这一份：各册分工、写作顺序、开工前必须完成的事'),
 ('LD-01_数据接入详细设计.docx','LD-01-data-ingestion.md','LD-01','数据接入详细设计','L1',
  '五类连接器、源契约、归档 SQL Server 迁移与多版本归一、校验隔离对账、参照数据维护'),
 ('LD-02_数据模型详细设计.docx','LD-02-data-model.md','LD-02','数据模型详细设计','L2',
  '七个 schema、粒度、代理键、分区与索引、缓变维、坐标系'),
 ('LD-03_指标与口径详细设计.docx','LD-03-metrics.md','LD-03','指标与口径详细设计','L3',
  '59 个指标的粒度与聚合、日切封账与修正、口径版本、对账规则'),
 ('LD-04_服务层与接口详细设计.docx','LD-04-service-layer.md','LD-04','服务层与接口详细设计','L4',
  '出口路径全集、查询 API 与缓存键、记录检索、受控 SQL、实时推送、对外服务、AI 四接口'),
 ('LD-05_展现层详细设计.docx','LD-05-presentation.md','LD-05','展现层详细设计','L5',
  '门户信息架构、双语与 RTL、元字段呈现、地图嵌入、大屏、移动端、渲染预算、组件库'),
 ('LD-06_管理界面详细设计.docx','LD-06-admin-consoles.md','LD-06','管理界面详细设计','L6',
  'M-01 至 M-11 的视图与动作、统一留痕、危险动作清单'),
 ('LD-07_安全与权限详细设计.docx','LD-07-security.md','LD-07','安全与权限详细设计','横切',
  '三层权限、出口路径×权限层、缓存上下文、受控 SQL 授权、审计、脱敏'),
 ('LD-08_高可用与部署详细设计.docx','LD-08-ha-deployment.md','LD-08','高可用与部署详细设计','横切',
  '集群参数与故障场景、RPO/RTO 与降级决策树、备份与演练、发布与回滚、十七条放置要求'),
 ('LD-09_开源许可与组件合规.docx','LD-09-oss-compliance.md','LD-09','开源许可与组件合规','横切',
  '选型标准、组件与版本清单、许可判定与传染性分析、上市年限举证'),
 ('LD-10_测试方案.docx','LD-10-test-plan.md','LD-10','测试方案','横切',
  '用例从验收标准派生、十八条纪律用例、测试层级、专项测试、准入准出'),
]
# HD-00 与 HD-03 此前没有 md 镜像，而它们恰是「怎么接手」与「哪些还是假设」
# 这两份最该先读的文件。开发机上的 Claude 读 LLD/*.md，读不到 docx。
HANDOFF = [
 ('HD-00_开发交接说明与文件索引.docx','REF-HD-00-handoff.md','HD-00','开发交接说明与文件索引','—',
  '交接包结构、四处必须先知道的事、开工前必须完成的事、六条实现纪律、完整文件索引'),
 ('HD-03_假设与信息需求登记册.docx','REF-HD-03-assumptions.md','HD-03','假设与信息需求登记册','—',
  'AS-01 至 AS-31 的假设与状态、IR-01 至 IR-49 的信息需求与紧急度、客户已答复事项'),
]

for src, dst, bid, title, layer, summary in BOOKS + HANDOFF:
    meta = {'id': bid, 'title': title, 'layer': layer, 'summary': summary,
            'source': src, 'format': 'converted from docx, content unchanged'}
    md = convert(os.path.join(BASE, src), meta)
    open(os.path.join(OUT, dst), 'w', encoding='utf-8').write(md)
    print(f'{dst:28s} {len(md):7d} 字符')
