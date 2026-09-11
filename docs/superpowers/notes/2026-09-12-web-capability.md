# claude -p 联网能力探测

日期：2026-09-12

**结论：不可联网**

**探测方式：** 通过 `ir_agent.clients.claude_cli.make_client()` 起一个 `claude -p
--output-format json` 子进程客户端，发送提示词，让模型搜索「半导体封测
稼动率」并要求给出具体数据点与来源网址；若不能联网则只回复「不能联网」。
只探测一次，不重复诱导。

命令：

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && PYTHONPATH="$PWD" python -c "
from ir_agent.clients.claude_cli import make_client
c = make_client()
out = c('请用一句话回答：你现在能否联网搜索？若能，搜索「半导体封测 稼动率」'
        '并给出一个你搜到的具体数据点与其来源网址；若不能，只回复「不能联网」。')
print(out[:600])
"
```

**原始回复（截断）：**

```
不能联网
```

**判定依据：** 回复未给出任何网址，也未给出任何具体数据点（如「2026Q2 稼动率
78%」一类），直接回复了「不能联网」，符合 brief 判定标准 (b)。注意：判定不依赖
模型「说自己能不能」这句话本身，而是看它有没有交出可核验的搜索结果——这次它
既没说能，也确实没有交出任何数据点或网址，两者一致，结论清楚。

**影响：** `sources/web.py` 的开放网络采集不可用，定向采集退回年报叙述章节 /
巨潮公告 / 研报语料库三源；规格中「MEDIA 级不得单独支撑因子取值」的约束相应
失去适用对象，但保留代码以备能力变化。`WEB_CAPABLE = False`（写入 Task 5 的
`ir_agent/sources/web.py`）。
