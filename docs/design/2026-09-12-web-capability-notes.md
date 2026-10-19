# claude -p 联网能力探测

日期：2026-09-12（结论已于当日修正）

**结论：可联网（需显式开启工具）**

---

## 之前的结论是错的

最初的探测结论是「不可联网」，判断依据是：用
`ir_agent.clients.claude_cli.make_client()` 起一个默认客户端，让模型搜索
「半导体封测 稼动率」，模型回复了「不能联网」，没有给出任何数据点或网址。

问题出在探测方式本身，不在模型：`claude_cli.py` 里 `make_client()` 拼出的
命令是

```python
cmd = ["claude", "-p", "--output-format", "json"]
```

**这条命令不开启任何工具。** `claude -p` 默认不带 `--allowedTools`，也就不带
`WebSearch` / `WebFetch`，模型确实没有搜索能力可用——但这测的是「默认调用
没开工具」，不是「这个模型 / 这条调用链路没有联网能力」。这是两件事：前者是
配置选择，后者是能力上限。第一次探测把提示词写成「若不能联网，只回复『不能
联网』」，模型如实报告了它*当前这次调用*没有工具，探测者却把这句话当成了
「这条链路做不到联网」的证据——**探测的边界被提示词/调用方式框死了，属于
方法问题，不是模型问题**。

## 复测：显式开启工具后

```bash
claude -p "搜索「半导体封测 稼动率 2026」，给出一个你搜到的具体数据点与其来源网址。若确实无法搜索，只回复「不能联网」。" --output-format json --allowedTools "WebSearch,WebFetch"
```

返回（原文，未删改数据点与网址）：

```
一个具体数据点：中国大陆封测（半导体封装测试）市场规模预计2026年将达到 3750.6亿元人民币，
其中先进封装占比预计达到23.3%。

来源：[2026年全球及中国集成电路封测行业产业链…研判 - 智研咨询](https://www.chyxx.com/industry/1252972.html)
```

具体数据点（3750.6 亿元、先进封装占比 23.3%）与可访问来源网址俱全，符合
「交出可核验的搜索结果」这条判定标准——**结论应为可联网**，只是默认不开。

## 实操陷阱：`--allowedTools` 是变长参数

第一次尝试复测时写成：

```bash
claude -p --allowedTools WebSearch WebFetch "搜索……"
```

CLI 报错：

```
Input must be provided either through stdin or as a prompt argument
```

原因是 `--allowedTools` 会把后面**所有**空格分隔的 argv 项都当成工具名吃掉，
提示词字符串被当成了第三个工具名，CLI 就再也找不到 prompt 参数了。正确写法
是把工具名用逗号拼成一个字符串，作为 `--allowedTools` 后唯一的一个 argv
元素，且提示词紧跟在 `-p` 之后：

```bash
claude -p "提示词" --output-format json --allowedTools "WebSearch,WebFetch"
```

## 对后续的影响

- `ir_agent/clients/claude_cli.py` 的 `make_client()` 已加 `tools: list[str]
  | None = None` 参数（默认 `None`，不开任何工具，行为与之前完全一致；
  显式传入时按上面的逗号拼接方式加 `--allowedTools`）。见
  `tests/test_claude_cli.py::TestMakeClient` 里新增的三条用例，钉住「默认
  不开工具」与「工具名逗号拼接、不拆成多个 argv 项」这两点。
- `sources/web.py`（Task 5 待建）的 `WEB_CAPABLE` 应设为 `True`：开放网络
  采集可用，调用时需显式传 `tools=["WebSearch", "WebFetch"]` 给
  `make_client()`。绝大多数其它调用（研报正文、摘要、打分等纯推理场景）
  仍应保持默认不开工具，避免模型分心和额外延迟。
- 规格中「MEDIA 级不得单独支撑因子取值」等约束不再是「因为不能联网所以没有
  适用对象」，而是在有能力联网的前提下依然成立的约束——需要重新确认这些
  约束的措辞是否还准确对应当前场景，不能再用「反正联不上网」当作隐含前提。
