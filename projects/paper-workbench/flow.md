# 论文写作八步编排协议（flow.md）

> 工作台的标准编排路径。任何 agent 用工作台写作，必须走这条流程；每一步有明确的
> 输入 → 动作 → 工具 → 门禁 → 输出 → 推进条件。流程与 `wb.py` 六阶段状态机、
> `staged_gen` 分段生成管线一一对应。
> 版本：2026-08-23（用户确认的八步流程 + 断点①②补齐；阶段5 增补：子代理派发通道、并行派发模式、整合质检、主控职责纪律；阶段D 增强：契约「执行者」列 + 执行者容错路由）

## 总览

```
①确定方向 → ②确定期刊 → ③检索文献 → ④确定框架
→ ⑤文献↔章节匹配 → ⑥skill 分段写作 → ⑦逻辑校验 → ⑧拼装审核
```

推进总原则：**每步门禁通过才进下一步**；断点①（skill 方法论）在 ⑥ 内联强制，
断点②（逻辑一致性）在 ⑦ 半自动校验。

---

## 总纲：AI 干 80% 体力活，人守 20% 核心判断

> **可投稿的文章 = 人的判断 + AI 的体力。** AI 是效率倍增器，不是自动出稿机：
> 检索、搭架、起草、渲染、润色、排版、拆审稿意见这些体力活交给工作台，
> 而方向取舍、故事线、学术观点、图表结论、术语把关、投稿核对、回复策略
> 这些核心判断始终由人守住。下文 ①–⑧ 各步的「【人判】」标注即本节的落点。

### 分工表（环节 | AI 做什么 | 人做什么）

| 环节 | AI 做什么（工作台能力） | 人做什么（判断点） |
|---|---|---|
| 文献调研 | 多源检索 + 去重 + DOI 核验（`literature_search` / `build_references` / refs_pipeline） | gap 判断：哪个研究缺口值得做 |
| 框架搭建 | 大纲脚手架（期刊范文拆解 → `framework/outline.md`） | 故事线：章节如何讲成一个连贯故事 |
| 初稿撰写 | 分段生成 + 段级门禁（`generate section` / parallel_gen） | 学术观点与 insight：论断、解读与创新点 |
| 图表制作 | 程序化渲染（figure_render 三路路由 / data2paper） | 图表结论：每张图表说明什么、不放什么 |
| 语言润色 | mechanical_fix 定点修复（术语归一/衔接/格式） | 术语准确：领域术语的取舍与定名 |
| 格式排版 | CSL 渲染 + Word 导出（refs_pipeline format / `export_docx`） | 投稿要求核对：期刊 author guidelines 逐项确认 |
| 审稿回复 | rebuttal 拆解草稿（`wb.py rebuttal` 逐条回复草稿） | 回复策略与语气：接受/反驳/补充实验的决策 |

### 八步人工判断点（【人判】）

八步流程中，以下推进条件处各设一个人工判断点（详见各节「推进条件」行的【人判】标注）：
① 方向四问 · ④ 框架确认 · ⑤ 契约锁定（S0）· ⑥ `--accept --reason` ·
⑦ 逻辑提示人工评估 · ⑧ 投稿裁决。门禁管体力活的质量，人判管方向与责任。

### 工具分工定位

| 工具 | 定位 | 职责说明 |
|---|---|---|
| DSH | 文献调研机 + 任务管理器 | 批量检索 / 对比矩阵 / 进度跟踪 |
| Codex | 草稿生成器 + 图表工具 | 按大纲写段落草稿、配图流程图；待订阅，未启用前路由自动降级（见 ⑥-a/⑥-b 执行者路由降级链） |
| ZCode | 长文编辑器 | 整篇一致性检查、跨章节引用核对的深加工位；机械核对由 integration_qc / `_assemble_logic_check` 兜底 |
| WorkBuddy | 格式工厂 | 期刊模板排版 / 参考文献格式化 / 导出——注意工作台已有确定性管线（refs_pipeline / `export_docx`）覆盖大部分，WorkBuddy 只接代码管线覆盖不了的排版杂活；写作能力按基准数据（`data/benchmark-scores.json`）可随时经契约「执行者」列调回 |

> 本表为默认策略，每篇论文以契约「执行者」列为准。

---

## ① 确定方向

- **输入**：研究方向一句话（可含目标期刊/中英文/类型）
- **动作**：`wb.py init "方向" [--journal J] [--lang zh|en] [--type review|article]`
- **工具**：wb.py（状态机）
- **门禁**：项目目录 + state.json 创建成功
- **输出**：`<papers>/<slug>/`（含空模板）
- **推进条件**：方向已澄清（Phase 0 四问：方向/期刊/语言/类型）
  **【人判】**方向四问的最终取舍由人定——AI 只整理选项，不代替决策。
- **可选增强**：可用 `wb.py litmap` 生成选题文献地图（聚类/热点/缺口可视化），辅助选 gap；不计入门禁。

## ② 确定期刊

- **输入**：研究方向 + 候选期刊意愿
- **动作**：期刊调研——`journal` 阶段
- **工具**：`literature_search` / `web_search` / `web_extract`（查 author guidelines）
- **门禁**：`journal/chosen.md` 已生成（含 CRIB 要求：字数/参考文献格式/图表规范）
- **输出**：`journal/chosen.md` + `journal/shortlist.md`
- **推进条件**：目标期刊确定，其要求已写入 chosen.md

## ③ 检索文献

- **输入**：研究方向 + 期刊范围
- **动作**：多源检索 → 去重 → DOI 核验 → 生成文献池
- **工具**：`literature_search`（OpenAlex/arXiv/Crossref/PubMed）、`build_references`、
  `fetch_doi`、`web_search`（灰色文献/中文库验证）
- **门禁**：文献池 `framework/references.md` ≥ 期刊要求条数；近 5 年占比 ≥ 40%
  （同行评议条目口径）；每篇含 DOI/作者/年份
- **输出**：`framework/references.md` + `research/search-log.md`（可复现检索记录）
- **推进条件**：检索覆盖与文献池达标，检索日志可复现
- **可选增强**：可用 `wb.py litmap` 对文献池做聚类核对覆盖（主题簇有无遗漏/冗余）；不计入门禁。

## ④ 确定框架

- **输入**：文献池 + 期刊范文结构
- **动作**：按期刊范文拆解结构 → 生成逐节框架 + 图表规划
- **工具**：`read_skill`（读期刊 skill / study-exemplars）
- **门禁**：`framework/outline.md`（每节目标/要点/字数）+ `framework/figures.md` 规划完整
- **输出**：`framework/outline.md` / `framework/figures.md`
- **推进条件**：框架与图表规划经用户确认 **【人判】**框架确认：故事线与章节骨架由人拍板，AI 只产脚手架。

## ⑤ 文献↔章节匹配

- **输入**：文献池 + 框架大纲
- **动作**：`generate contract`——生成契约，含引文分配表（每条文献 → 支撑章节）
- **工具**：`generate contract`（可 `--ai` 起草）
- **门禁**：**契约锁定**（S0 人工检查点，不可跳过）；引文分配覆盖全部文献池
- **输出**：`draft/contract.md`（范围/大纲/图表契约/引文分配/术语）
- **推进条件**：契约已锁定 → 段落生成才放行 **【人判】**契约锁定（S0 人工检查点）：范围/引文分配/术语一经锁定不可跳过。

## ⑥ skill 分段写作

- **输入**：锁定契约 + 文献池 + 章节方法论
- **动作**：逐段 `generate section --sid Sx`
- **工具**：`generate section`（工作台组装 prompt：契约范围 + 文献池 + 写作约束 +
  **本节方法论内联**——按章节类型自动注入 Results/Discussion/Methods/Intro 论证纪律）
- **门禁**：段级五件套——机械检查 / 引文⊆分配池(P1) / 字数预算 / 引用密度 / 段落主题句
  - 失败分级：L0 规则修复(0 token) → L1 定点修补(~1/10 token) → L2 整段重写(保底)
  - `--accept` 必须带 `--reason`（唯一旁门已留痕）
- **输出**：`draft/sections/<sid>-*.md`
- **推进条件**：全部段落 `done`（门禁通过或带理由接受） **【人判】**`--accept --reason` 是唯一旁门，理由由人写、留痕可追溯。

### ⑥-a 可选通道：子代理派发写作（subagent_writer，已实现）

- **触发**：`generate section --sid Sx --via subagent`（默认 `--via normal` 为 ai_client 直连，原路径逐字节不变）
- **任务卡自包含**：`staged_gen.build_section_prompt` 渲染与 gen_section 完全一致的任务卡（契约范围 + 本节文献池 + 术语规约 + 前文摘要 + 方法论内联），末尾追加输出协议：子代理只回纯 Markdown 正文落盘为章节文件，主控按结构化 envelope（`{ok, sid, status, words, file, gate_passed, issues, executor, msg}`）回收，不读对话过程
- **门禁回收**：子代理产出无任何信任豁免——统一过 `staged_gen.gate_section` 段级五件套；门禁失败先 L0 `toolbox.mechanical_fix`（0 token）重判，仍未过则重试（任务卡尾部附「上一轮未通过门禁」问题清单，不累积）
- **派发台账**：`draft/orchestration/dispatch-log.jsonl` 逐次追加（sid/尝试次数/耗时/词数/问题数）；dsh 离线时明确报错不静默（回退 = 去掉 `--via subagent` 重跑同一命令）
- **执行者路由**（与 ⑥-b 共享 `runner.dispatch_with_fallback` 容错路由）：派发执行者取自契约章节大纲表的可选「执行者」列（最末列；留空=默认执行者 dsh；旧契约无此列照常解析，行为不变）。降级链顺序：① 章节指定执行者（仅当 `runner.list_executors` 判定 available 时）→ ② 失败/不可用换默认执行者 dsh 重试一次 → ③ 仍失败返回失败标记，该节由调用方降级单体路径（或明确报错）。每次派发与切换都写 `draft/orchestration/dispatch-log.jsonl` 结构化日志，条目含 `executor`/`fallback_from` 字段。执行者映射建议（方法段优先 workbuddy、结果段优先 dsh、引言段两者皆可、总体兜底 dsh）见 `data/benchmark-scores.json`——依据 2026-08-23 基准测试（3 节 × 350 词任务卡，dsh/workbuddy 均 3/3 一次过门禁，codex 待订阅未参测，traework 仅交互式不参测）；不虚构未测执行者的能力。

### ⑥-b 并行派发模式（parallel_gen，已实现）

- **入口**：`wb.py generate parallel [--concurrency N] [--timeout S]`（等价 `python parallel_gen.py <项目>`；
  前置检查与 gen_section 一致：gen_state 已初始化、契约存在、**契约已锁定**——S0 人工检查点不可跳过）。
- **触发条件**：契约锁定且章节大纲非空即并行；`parse_contract` 的 `dependency_graph` 决定波次结构（「依赖」列填了用所填值，未填默认线性前驱链，退化为逐段串行波次）。
- **任务卡自包含**：每节任务卡携带写该节所需的全部上下文（契约范围/本节文献池/术语规约/前驱节摘要锚点/方法论内联/语言），子代理无需读其他章节全文；输出协议：子代理把正文写入 `draft/sections/<sid>-*.md`（已存在则覆盖）并回报单行 JSON，主控回收时优先取文件、兜底取响应文本剥围栏。
- **波次调度与摘要锚点**：`_topo_waves` 按 `dependency_graph` 拓扑分波（Kahn 分层：前驱全部完成的节才入波；环依赖不死锁，降级为按契约顺序整体入最后一波）；波内线程池并发（`--concurrency` 默认 4，与 `dsh_bridge` 会话池容量一致），波间串行；会话从会话池 `acquire_session`/`release_session` 取还（长期复用，不每任务新建——dsh 无 session.close RPC）。每节完成后尾部 ≤200 词摘要写入 gen_state 该段 `anchor` 字段，供下游波次拼接「所有前驱 sid 的摘要锚点集合」注入任务卡前文摘要位（续跑时预载已完成段的锚点）。
- **门禁与失败降级**：子代理产出无信任豁免，统一过 `gate_section`；失败先 L0 `mechanical_fix`（0 token）重判，仍未过重试 1 次（任务卡尾附问题清单，不累积）；单段失败不阻塞同波；**波次结束仍失败的节降级到单体路径**（`staged_gen.gen_section` ai_client 直连）重跑，降级记入 `downgraded`；派发日志复用 `draft/orchestration/dispatch-log.jsonl`（追加 `wave`/`via` 字段）。
- **主控纪律**：波次裁决只依据汇总报告（`{ok, msg, waves, sections, downgraded, failed}`）与问题清单（见 ⑦「主控职责纪律」）。
- **执行者路由**：每节派发同样走 `runner.dispatch_with_fallback`（降级链与 ⑥-a 一致：章节指定执行者 → 默认 dsh 重试一次 → 仍失败返回失败标记，波末仍失败的节照旧降级单体路径）。**会话池路径仅用于 dsh 执行者**；外部执行者（如 workbuddy）走 `runner.dispatch` 子进程路径，产物回收用响应文本 + 既有门禁（无信任豁免），不依赖会话池。执行者列用法与映射建议同 ⑥-a「执行者路由」（依据 2026-08-23 基准测试，见 `data/benchmark-scores.json`）。

## ⑦ 确定逻辑合理

- **输入**：全部已完成段落
- **动作**：逻辑一致性校验——跨章节矛盾 / 段间衔接 / 过度宣称
- **工具**：`generate assemble` 内置 `_assemble_logic_check`
  （跨章节矛盾 P0：引言 vs 正文矛盾断言；段间衔接 P2：相邻段首句无过渡；
  过度宣称 P1：结论强度超出证据）
- **门禁**：逻辑校验**提示**（不硬拦截——启发式，人工确认；P0 跨章节矛盾必须修复）
- **输出**：`manuscript/main.md` + 逻辑校验提示清单 + `manuscript/qc_report.json`（整合质检报告）
- **推进条件**：P0 跨章节矛盾已修复，其余提示已人工评估 **【人判】**逻辑提示为启发式，是否成立由人工评估；整合质检报告已经主控裁决留痕

### 整合质检（integration_qc.py，纯机械/确定性，零 LLM）

拼装（`generate assemble`）后运行 `python integration_qc.py <项目> [--apply-refs] [--json]`，
三个确定性函数全部走代码，**禁止调用 LLM**：

- `qc_references`：按正文首现顺序对引用编号确定性重编号（区间如 4–6 展开后参与首现排序；
  引用组只匹配纯数字方括号/圆括号，Figure/Table 编号、菌株号、度量值天然排除）；
  默认只报告，`--apply-refs` 才写回 main.md（先备份）；有编号列表时参考文献章节同步重排并删除未引用条目（收窄文献池）
- `qc_terminology`：契约术语表是唯一权威，输出归一化报告（命中计数/行号/代码块与参考文献章节内的可疑位置）；
  默认只报告不改写，apply 仅允许归一术语的空白变体，不动语义内容；修补指令走定点补丁（只换违规片段，禁止整段重写）
- `qc_transitions`：包装 `_assemble_logic_check` 输出 P0（跨章节矛盾）/P1（过度宣称）结构化清单；
  P2 衔接缺口逐处标注相邻两段行号与开头摘录，供定点修补（下一段首句补衔接词/复现关键词），禁止整段重写；
  报告写入 `manuscript/qc_report.json`，人类可读摘要供主控阅读后裁决（P0/P1 是否处理由主控决定）
- **分工边界**：整合质检是机械质检（确定性代码）；⑦ 顶部的 `_assemble_logic_check` 提示仍由 `generate assemble` 内联产出，两者口径一致，整合质检额外给出可定点修补的位置信息。

### 主控职责纪律（orchestrator，适用于 ⑥-b 与 ⑦）

- 主控**只读**各章首尾摘要 + 问题清单（dispatch envelope / gate issues / qc_report 摘要）做裁决，
  不读各章全文（token 纪律）；子代理产出无信任豁免，一切以门禁与质检的结构化结果为准。
- 需要改写时一律经 `toolbox.mechanical_fix` 留痕，修复后**重跑门禁/质检**确认；
  禁止主控直接手改正文绕过工具链（process_audit 会抓 bypassed_toolchain/unverified_edit）。
- 裁决（接受/重试/降级/定点补丁）逐条记入 `draft/orchestration/qc-log.jsonl`
  （每行 JSON：ts/sid/action/issues/reason），与 `dispatch-log.jsonl` 配套可追溯。

## ⑧ 拼装审核

- **输入**：逻辑校验通过的 `manuscript/main.md`
- **动作**：全量审查——`review-auto`（引用核验/统计/原创性/格式/结构/PaperSpine/
  学术规范）+ 模拟审稿（simulate-reviewers）
- **工具**：`quality_check`（全量门禁 30+ 规则）、`mechanical_fix`、`export_docx`
- **门禁**：P0/P1 全部关闭；`process_audit` clean；质量分达期刊可投线
- **输出**：`submit/cover-letter.md` + `export/manuscript.docx` + `review/final-report.md`
- **推进条件**：门禁 P0/P1=0，投稿材料齐备 **【人判】**投稿裁决：投不投、投哪刊由人定，门禁只给证据。
- **可选增强**：审稿意见到手后用 `wb.py rebuttal` 生成逐条回复草稿（策略与语气由人定）；不计入门禁。

---

## 断点①：skill 方法论内联（⑥ 强制，不依赖 agent 自觉）

`build_section_prompt` 按章节类型自动内联方法论条款（而非让 agent 去 read_skill）：
- Results → 证据→结论分级、因果断言须有对照、不把相关当因果
- Discussion → 四段式（发现→对比→局限→展望）、预判审稿人反对意见
- Methods → 可复现性、统计方法说明
- Intro → 漏斗结构、事实后跟引用
- 默认 → evidence-claim 分级、段落主题句先行

## 断点②：逻辑一致性校验（⑦ 半自动）

`_assemble_logic_check` 在 assemble 拼装后自动执行，返回 warnings：
- 跨章节矛盾（P0，词库已扩展单复数形式，toolbox E39 兜底）
- 段间衔接（P2，相邻段首句无衔接词/关键词重复 ≥3 处）
- 过度宣称（P1，复用 overstatement_check）

## 工具纪律（叠加在工作台 MCP 之上）

- 检索必须走工具（literature_search/web_search），禁止凭记忆编造文献
- 写作前 search_skills + read_skill + record_skill_use（台账留痕）
- 修改后必须 quality_check 复检（process_audit 抓未验证修改）
- 图表 figure_render、导出 export_docx，禁止手写绕过

---

## 环境与修复注记（2026-08）

- start-dsh.bat 已修 node 路径探测：此前 PATH 缺 node 时直接调 `node` 会静默失败；现在优先使用完整路径 `C:\Program Files\nodejs\node.exe`，缺失时回退 `where node` 探测 PATH，两者都找不到则明确报错并 pause，不再静默启动失败。
- 语言传导为有意修复：中文契约的 `generate section`（含 `--via subagent`）任务卡从恒给英文指令改为按契约语言字段给指令（中文契约 → 「请用中文写作。」）；旧契约头部无有效语言字段时维持 en，行为不变。
