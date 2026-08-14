<p align="center">
  <img src="assets/hero.svg" alt="Competitor Census — 从公开信号到可追溯策略" width="100%" />
</p>

# Competitor Census｜竞品公开信息普查

**把任意友商分散在公开渠道中的信息，变成一份可追溯、可复用的竞品情报档案。**

Competitor Census 是面向全球市场、克隆后即可运行的 Agent Skill 与项目 CLI。输入公司和市场，它可以帮助你找到真正活跃的公开渠道，采集 TikTok 公开视频与对话、Facebook 公开主页帖子，或 YouTube 公开元数据及选定视频评论，建立结构化证据库，处理多语言内容，从完整语料中形成分类，量化内容表现与客户诉求，并生成每条关键结论都能回到数据行和原始链接的报告。当证据库包含公开对话时，还可以运行经过校验的客户声音分析模式。

> 先普查，后深挖；先证据，后结论。

## 它能实现什么

| 能力 | 结果 |
|---|---|
| **平台普查** | 先找到并核验友商真正活跃的公开渠道，再决定深挖哪些平台 |
| **可运行平台连接器** | 用统一 CLI 采集 TikTok 主页及选定对话、Facebook 主页帖子，或 YouTube 元数据及选定对话 |
| **范围内全量采集** | 统一保留稳定 ID、发布日期、正文、播放量、可见互动字段、账号信息和原始链接 |
| **多语言处理** | 原文与工作译文分开保存，不同语言进入同一套结构化数据 |
| **自下而上归类** | Agent 通读完整语料后再形成类别，不用预设关键词硬套标签 |
| **专业分析** | 对比内容供给与平均/中位传播效果，统计客户诉求、回复方式和机会缺口 |
| **客户声音模式** | 自下而上形成问题分类，区分意图、情绪与严重程度，关联可见官方回复，并对分享版用户名脱敏 |
| **可追溯交付** | 输出 CSV 证据库、分类体系、校验结果和带证据链接的 HTML 报告 |
| **协作交接** | 将审核后的证据转为多维表格式行动索引和简洁变化简报，不暴露完整原始语料 |

证据结构与分析层不绑定具体平台，因此同一流程可以跨公司、跨语言、跨地区，并接入经过批准的采集工具。

## 一套证据库，三种决策模式

| 从这里开始 | 可回答的问题 | 交付物 |
|---|---|---|
| **基线普查** | 哪些公开渠道真正重要、发布了什么、什么内容有效？ | 可追溯竞品报告 |
| **增量监测** | 与上一次审核快照相比，出现了哪些实质变化？ | 带证据链接的变化简报，而非互动数噪声 |
| **证据型角色画像** | 公开信号能够支持哪些安装商、合作伙伴或终端用户判断？ | 含证据ID、反证与置信度边界的角色画像 |

这不是一个“抓下来再总结”的工具。采集是有版本的证据层；翻译、分析、报告、监测和经批准的协作交接是独立层，可以反复运行而不覆盖原始采集结果。持续监测见 [`references/monitoring-playbook.md`](references/monitoring-playbook.md)，角色研究见 [`references/persona-research.md`](references/persona-research.md)。

## 克隆、检查、运行

离线演示无需 API Key、浏览器登录或第三方依赖：

```bash
git clone https://github.com/KayZhongyi/competitor-census.git
cd competitor-census
python3 scripts/run_demo.py
```

打开 `demo/output/report.html`，或查看[在线虚构案例](https://kayzhongyi.github.io/competitor-census/)。

<p align="center">
  <img src="assets/demo-preview.svg" alt="可追溯竞品报告虚构示例" width="100%" />
</p>

运行真实平台连接器安装向导，再将当前仓库安装为 Agent Skill：

```bash
./competitor-census setup
./competitor-census doctor
./competitor-census install-skill --target codex
# 或：./competitor-census install-skill --target claude
```

`setup` 可协助安装 [OpenCLI](https://github.com/jackwener/OpenCLI)、打开官方 Chrome 扩展页面、重启本地桥接服务并验证连接。根据 Chrome 的安全机制，扩展授权以及 TikTok/Facebook 登录必须由用户本人在浏览器中完成；安装助手不会索取平台密码。需要同时安装 YouTube 连接器依赖时，增加 `--with-youtube`。之后可随时用 `doctor` 做只读检查。

## 使用项目自己的 CLI

```bash
./competitor-census --help
```

同一个命令入口覆盖真实平台采集、证据分析、客户声音、增量合并和离线演示：

```text
./competitor-census setup
./competitor-census doctor
./competitor-census tiktok ...
./competitor-census tiktok-comments ...
./competitor-census facebook ...
./competitor-census youtube ...
./competitor-census youtube-comments ...
./competitor-census prepare-analysis ...
./competitor-census apply-analysis ...
./competitor-census prepare-voice ...
./competitor-census apply-voice ...
./competitor-census merge ...
./competitor-census export ...
```

## 采集 TikTok 公开主页

TikTok 连接器通过用户已有权限的 Chrome 登录态读取公开主页网格，为范围内可检索的视频保存稳定视频 ID、发布日期、原始文案、采集时播放量和原始链接。它不下载视频，也不调用 TikTok 私有接口。

使用安装向导安装 OpenCLI、打开官方扩展页面并检查浏览器桥：

```bash
./competitor-census setup
```

先做小范围字段核验：

```bash
./competitor-census tiktok \
  --company "目标公司" \
  --profile "@targethandle" \
  --max-scrolls 1 \
  --output runs/target-tiktok-check
```

确认账号身份以及日期、文案、播放量、链接字段后，再扩大范围。若程序滚动不再加载新卡片，`--manual-scroll` 会保留同一浏览器会话，请人正常滚动公开主页后完成最后一次提取：

```bash
./competitor-census tiktok \
  --company "目标公司" \
  --profile "@targethandle" \
  --max-scrolls 100 \
  --manual-scroll \
  --output runs/target-tiktok
```

从已采集视频中按播放量选择 Top 30，进一步保存公开评论、可见二级回复、父子关系、评论点赞、官方身份及可见的视频页互动数：

```bash
./competitor-census tiktok-comments \
  --bundle runs/target-tiktok \
  --top 30 \
  --owner "@targethandle" \
  --owner "目标公司"
```

TikTok 可能在评论加载前弹出拼图验证。命令会保存可续跑检查点并停止，验证只能由人手动完成，程序不会破解或绕过。人工完成后使用 `--resume` 续跑；也可以在交互式终端增加 `--wait-for-human`，让命令原地等待。字段出处、覆盖范围口径、筛选规则和限制见 [`references/tiktok-adapter.md`](references/tiktok-adapter.md)。

## 采集 Facebook 公开主页

Facebook 连接器通过用户已有权限的 Chrome 登录态读取公开帖子；每次滚动前先保存当前页面窗口，按平台帖子 ID 或永久链接去重，并在每轮完成后写入检查点。当前公开命令覆盖主页帖子；Facebook 评论与回复仍属于已记录的扩展项，不包装成已经交付的连接器。

先运行安装向导，在 Chrome 中批准官方扩展并正常登录 Facebook，然后检查浏览器桥：

```bash
./competitor-census setup
```

先用少量滚动核验账号、日期、互动数和原始链接：

```bash
./competitor-census facebook \
  --company "目标公司" \
  --page "https://www.facebook.com/TargetPage" \
  --max-scrolls 3 \
  --output runs/target-facebook-check
```

核验无误后再扩大声明范围：

```bash
./competitor-census facebook \
  --company "目标公司" \
  --page "https://www.facebook.com/TargetPage" \
  --max-scrolls 100 \
  --output runs/target-facebook
```

连接器全程只读，不发布、点赞、评论、关注或私信。遇到人机验证时会停止并保留可续跑检查点，只能由人手动完成验证，程序不会绕过。`--bind`、断点续跑、字段说明和覆盖范围表述见 [`references/facebook-adapter.md`](references/facebook-adapter.md)。

## 采集 YouTube 公开频道

仓库自带 YouTube 公开元数据连接器，不下载视频文件。建议先用少量记录核验账号和字段：

```bash
python3 -m pip install -U "yt-dlp[default]"
./competitor-census youtube \
  --company "OpenAI" \
  --channel "https://www.youtube.com/@OpenAI" \
  --tabs videos \
  --max-items-per-tab 10
```

运行后，`runs/openai/` 会同时得到证据库、基础报告、运行记录和可直接交给 Agent 的分析任务。

<p align="center">
  <img src="assets/youtube-live-demo.gif" alt="从 YouTube 公开元数据到证据库与报告" width="100%" />
</p>

核验无误后，对所选标签页中可检索的公开内容执行尽可能完整的普查：

```bash
./competitor-census youtube \
  --company "目标公司" \
  --channel "https://www.youtube.com/@TargetHandle" \
  --tabs videos,shorts,streams \
  --max-items-per-tab 0
```

随后可用同一套 `yt-dlp` 依赖，深挖已采集视频中播放量最高的视频评论。无需 Google API Key、浏览器登录态、Cookie，也不会下载媒体：

```bash
./competitor-census youtube-comments \
  --bundle runs/target-company \
  --top 30 \
  --max-comments-per-video 500
```

命令会把评论 ID、回复关系、公开显示名称、可见时间、点赞数、原始链接和可确定的官方账号标记写入 `comments.csv`，再生成已有的客户声音分析任务。只有在范围声明明确要求时，才使用 `--max-comments-per-video 0` 读取单条视频下全部可检索评论。评论采集仍是尽力获取：关闭、删除、受限、排序变化或平台未返回的评论可能缺失；遇到人机验证时命令会停止，绝不绕过。

需要持续监测时，将指定日期之后的新一轮采集写入独立目录，再按稳定ID合并：

```bash
./competitor-census youtube \
  --company "目标公司" \
  --channel "https://www.youtube.com/@TargetHandle" \
  --since 2026-07-01 \
  --output runs/target-2026-07

python3 scripts/merge_incremental.py \
  --base runs/target-baseline/content.csv \
  --incoming runs/target-2026-07/content.csv \
  --output runs/target-current/content.csv
```

合并报告会分别统计新增、更新、未变化和本轮未出现的记录，不会把“本轮未出现”直接判定为删除；同时记录具体变化字段，使监测简报优先关注内容或运营层面的实质变化，而不是日常互动数波动。

## 用任意 Agent 完成分析

每次采集都会生成模型无关的 `analysis/analysis_task.md`。让你常用的文件型 Agent 按任务完成分析，再运行校验：

```text
使用 $competitor-census 执行 runs/openai/analysis/analysis_task.md。
通读完整语料，形成分类体系，并完成每一条分析结果。
```

```bash
python3 scripts/apply_analysis.py --bundle runs/openai
```

```text
content.csv（原始证据，不改写）
  → Agent 通读完整语料
  → taxonomy.json + analysis_results.csv
  → 确定性校验
  → analyzed_content.csv + analysis_report.html
```

校验器会检查源文件指纹、ID 完整性、译文覆盖率、分类定义、置信度和代表性证据，全部通过后才生成分析数据和报告。

### AI 不能自己给自己判分

AI 输出只是候选结果，不会直接成为最终报告。确定性的**证据门**采用失败即停止设计：

| 校验门 | 拒绝的问题 |
|---|---|
| **源文件指纹锁** | 分析任务生成后，`content.csv` 被修改 |
| **ID 集合精确比对** | 漏掉原始记录、重复 ID，或编造不存在的 ID |
| **分类与覆盖校验** | 译文缺失、类别未声明、定义不完整、代表证据错配 |
| **不确定性留痕** | 置信度非法，或低置信分类没有说明原因 |

任一校验失败，系统只写出失败报告，不生成 `analyzed_content.csv` 和最终分析报告。这保证的是流程完整、结果可追溯，不代表公开平台上的每句话或 AI 的每项解释都具有绝对真实性。

## 运行客户声音分析

当证据库的 `comments.csv` 中已经包含合规采集的公开对话时，可创建独立的客户声音任务：

```bash
python3 scripts/prepare_customer_voice.py --bundle runs/target-company
```

让任意文件型 Agent 执行 `voice/voice_task.md`，再运行：

```bash
python3 scripts/apply_customer_voice.py --bundle runs/target-company
```

```text
comments.csv + content.csv（原始证据，不改写）
  → Agent 通读完整客户语料
  → voice_taxonomy.json + voice_results.csv
  → 确定性校验 + 可见官方回复关联
  → analyzed_voice.csv + customer_voice_report.html
```

该模式将**问题、意图、情绪、严重程度和置信度**分别处理，而不是把客户反馈简化为正负面情感分数。高风险记录必须提供可观察依据，分享版报告会将公开用户名替换为稳定匿名ID。

## 将审核证据带入协作流程

报告不是流程终点。完整原始证据库应保留在受控位置，再将已审核的证据 ID、来源链接、观察、负责人和状态形成协作索引。飞书多维表格可以承载这一索引；群卡片只推送需要响应的新信号、决策和行动，不在群内堆放全量语料。

本仓库提供的是协作交接规范，而不是带凭据的 SaaS 集成。飞书自动同步需要获得批准的企业自建应用、最小权限、受控目标位置和密钥管理。实施前请阅读 [`references/collaboration-handoff.md`](references/collaboration-handoff.md)。

## 安装为 Agent Skill

先克隆一次，再把当前仓库链接到所使用的 Agent：

```bash
git clone https://github.com/KayZhongyi/competitor-census.git
cd competitor-census
./competitor-census install-skill --target codex
# 或：./competitor-census install-skill --target claude
# 同时安装：重复传入 --target codex --target claude
```

也可以直接克隆到 Skill 目录：

```bash
git clone https://github.com/KayZhongyi/competitor-census.git ~/.codex/skills/competitor-census
# Claude Code：改为克隆到 ~/.claude/skills/competitor-census
```

调用示例：

```text
使用 $competitor-census 调研 [国家/地区] 的 [公司] 公开渠道。
先建立证据库，再生成可追溯的策略报告。
```

Skill 由 Markdown 流程和 Python 标准库脚本组成，其他具备终端和浏览器能力的 Agent 也可以执行。TikTok 和 Facebook 使用 OpenCLI 这一外部 Apache-2.0 浏览器桥，YouTube 使用 `yt-dlp`；本仓库提供采集规则、统一命令、检查点、证据结构、校验和报告流程。第三方归属见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 导出矢量 PDF

每份报告默认保留为带可点击证据链接的 HTML；需要便携交付时，可通过 Chrome 或 Chromium 导出矢量 PDF：

```bash
./competitor-census export \
  --html runs/target-company/analysis_report.html \
  --pdf runs/target-company/analysis_report.pdf
```

PDF 只是展示层，CSV 与 JSON 证据库仍应与其一同保留。

## 最终交付什么

| 交付物 | 用途 |
|---|---|
| `platform_census.csv` | 账号核验、活跃度与深挖决策 |
| `content.csv` | 公开内容、发布日期和互动指标等源级证据 |
| `comments.csv` | 采集到的用户对话和官方回复关系 |
| `run_manifest.json` | 调研范围、截止时间、工具、覆盖与运行记录 |
| `analysis/taxonomy.json` | 从语料中形成的类别定义与代表性证据 ID |
| `analysis/validation_report.json` | 可机器检查的完整性与一致性结果 |
| `analyzed_content.csv` | 在不改写原始证据的前提下合并译文与分类 |
| `analysis_report.html` | 带数量、分母、证据 ID 和原始链接的管理层报告 |
| `voice/voice_taxonomy.json` | 从客户语料中形成的问题定义和代表性评论 ID |
| `voice/validation_report.json` | 客户声音分析的完整性、一致性和标签校验 |
| `analyzed_voice.csv` | 含匿名作者ID和可见回复关系的客户声音分析数据 |
| `customer_voice_report.html` | 问题、意图、情绪、严重程度、回应和证据一体化报告 |

## 面向业务决策的分析方法

- **自下而上分类：** 类别来自真实语料，而不是固定模板。
- **供给—效果错位：** 同时比较发布占比、平均播放量和中位播放量。
- **客户声音分析：** 对具体问题和诉求做带分母的频次统计。
- **客户信号分诊：** 将问题、意图、情绪、严重程度和置信度分开处理。
- **回复模式分析：** 区分有效回答、模板回复、渠道引导和未公开回复。
- **机会映射：** 把高需求、低供给主题转化为可验证的内容与服务机会。
- **证据阈值：** 小样本和歧义记录保留标记，不包装成确定结论。

友商分析方法见 [`references/analysis-playbook.md`](references/analysis-playbook.md)，客户声音方法见 [`references/customer-voice-playbook.md`](references/customer-voice-playbook.md)，场景选择见 [`references/research-modes.md`](references/research-modes.md)。

## 为可信复用而设计

- 原始证据与翻译、分类、结论始终分离。
- 稳定 ID 和原始链接让每个关键数字都可复核。
- 输入指纹防止旧分析误套到已经变化的新语料。
- 客户声音分享版输出默认将公开用户名替换为稳定匿名ID。
- 账号核验、平台验证和最终业务判断保留人工确认。
- 标准 CSV/JSON 接口便于继续增加合规连接器和报告格式。
- 按日期采集与稳定ID合并支持持续监测，同时保留历次原始证据。
- 协作摘要始终保留证据 ID 和原始链接，避免群内结论再次成为不可追溯的信息。

公开信息采集规范见 [`references/collection-safety.md`](references/collection-safety.md)。仓库中的演示公司与数据全部为虚构内容。

欢迎贡献合规连接器、分析方法和报告主题，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE)
