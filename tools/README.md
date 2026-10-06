# tools —— LLD Markdown 的生成脚本

`LLD/` 下的 Markdown 是 `.docx` / `.xlsx` 原件的转换结果，**不是手改的副本**。
原件改了就重新跑一遍，不要直接编辑 `LLD/*.md`——手改的内容会在下次生成时被覆盖，
而且会让两边悄悄分叉。

## 用法

```bash
# ICP 枚举与指标目录（换版本接口文档后重跑并逐行比对）
pdftotext -layout "3rd API/CloudICP V100R023C00 Interface Reference.pdf" /tmp/icp.txt
python3 tools/icp_idx.py  /tmp/icp.txt "3rd API/icp_indexes.csv"
python3 tools/icp_enum.py /tmp/icp.txt "3rd API"

cd tools
python3 run.py        # 11 册 docx → LLD/LD-*.md
python3 xlsx2md.py    # LD-05A 规格册、HD-04 决策册、HD-02 追溯矩阵 → LLD/*.md
python3 hd1.py        # HD-01 需求 → LLD/REF-HD-01-requirements.md（逐条，验收标准单列）
```

依赖：`python-docx`、`openpyxl`。

## 文件

| 文件 | 作用 |
| --- | --- |
| `conv.py` | docx → Markdown 的转换函数。按文档真实顺序遍历段落与表格；单列表格转引用块 |
| `run.py` | 批量转换 11 册 LD 文档，写入各册的 frontmatter |
| `xlsx2md.py` | xlsx → Markdown，逐页签转表格 |
| `hd1.py` | HD-01 专用：拆成逐条需求，验收标准单列 |
| `icp_idx.py` | 从 CloudICP 接口文档抽 396 个平台指标目录 → `3rd API/icp_indexes.csv` |
| `icp_enum.py` | 从同一文档抽枚举值表 → `3rd API/icp_{device_type,call_type,release_cause}.csv` |
| `check_refs.py` | 全局编号与引用一致性校验。退出码非零即不通过 |

## 一条校验

```bash
python3 tools/check_refs.py
```

每一轮改完登记册、指标或契约都跑一遍。它查四件事：

1. **悬空引用** —— 正文引用了登记册里没有的编号（上一轮就是这样抓到 M-I09 与 DD-62 的）
2. **编号断号** —— 缺号必须在文档里记明，否则不通过
3. **从未被引用的条目** —— 不算错误，但一条长期没人提的 IR 多半是忘了跟进
4. **跨文件交叉校验** —— metrics 的 source_tables 是否真有这张表、
   对照表引用的平台指标编号是否存在、契约指向的列是否存在、枚举值表是否还在

`ALLOW` 名单里的例外都写了理由。这个名单一旦变成「加进去就不报了」，校验器就失去意义。

## 一条纪律

原件是权威，Markdown 是派生物。同理，`ddl/*.sql` 与 `metrics/metrics.yaml`
是权威定义，文档只是描述它们——三者不一致时，以可执行的文件为准。
