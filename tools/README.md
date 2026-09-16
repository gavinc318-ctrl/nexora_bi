# tools —— LLD Markdown 的生成脚本

`LLD/` 下的 Markdown 是 `.docx` / `.xlsx` 原件的转换结果，**不是手改的副本**。
原件改了就重新跑一遍，不要直接编辑 `LLD/*.md`——手改的内容会在下次生成时被覆盖，
而且会让两边悄悄分叉。

## 用法

```bash
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

## 一条纪律

原件是权威，Markdown 是派生物。同理，`ddl/*.sql` 与 `metrics/metrics.yaml`
是权威定义，文档只是描述它们——三者不一致时，以可执行的文件为准。
