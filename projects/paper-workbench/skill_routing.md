# 技能路由规则（skill_routing）

> 避免多套写作/审稿技能风格冲突。原则：**每个阶段只有一个主技能源，其余仅作参考**。
> 2026-08-22 建立（依据当日对 ~/.dsh/skills/ 的适用性评估，详见工作区《技能评估》）。

## 阶段路由表

| 工作台阶段 | 主技能/规则源 | 参考（不冲突时才用） | 禁用 |
|---|---|---|---|
| research 文献调研 | 工作台内置工具链（五源检索 + CrossRef/refs_pipeline 核验）；选题阶段可选 `wb.py litmap`（文献地图辅助选 gap，不计入门禁） | academic-researcher 的分析框架（仅作思路） | 用通用助手技能替代核验工具 |
| journal 期刊调研 | `journal/chosen.md`（官方指南摘要为唯一权威）；期刊库条目（如有） | — | 套用其他期刊技能（如 sci-* 的 Science 预算） |
| framework 框架 | `templates/outline-review.md` + `quality_rules.md` | write-scientific-manuscript/section-logic（每节回答一个问题） | — |
| draft 写作 | `quality_rules.md` + `build_stage_prompt` 内置约束；长文/多图表稿件优先走 `staged_gen`（`wb.py generate`，契约先行+段级门禁） | scientific-writing（writing_principles）；scientific-prose-style | 同时启用两套写作风格技能；长文一次性整篇生成 |
| review 审查 | `toolbox.quality_check` + review-auto 流水线（唯一裁决者） | scientific-writing/reporting_guidelines（扩充检查项时参考） | 以技能建议推翻门禁判定 |
| submit 投稿 | `journal/chosen.md` 格式要求 + `submit/checklist.md`；审稿后回复：工作台内置 `wb.py rebuttal`（主，逐条回复草稿，策略与语气由人定） | sci-submission 的清单思路（仅思路）；sci-rebuttal（仅参考） | 照搬 Science 格式预算 |

## 冲突裁决顺序

期刊官方要求（chosen.md） > 工作台门禁（quality_check） > quality_rules.md > 技能建议 > 模型默认倾向

## 期刊适配纪律（借鉴 sci-workflow 的 fit-first 思想）

- 任何写作/格式动作前先确认目标期刊要求（chosen.md 非空且已核实），否则先补期刊调研
- 期刊专属技能（如 sci-* 家族为 Science 专用）**不得跨刊套用**：其硬性预算（摘要字数/图表数/结构）只对该刊有效
- 新接入技能前在本文件登记路由位置，未登记的技能不进入生产链路

## 待办（技能资产转化）

- [ ] 从 `scientific-writing/figures_tables.md` 摘取表格/图生成规范进 `quality_rules.md`（防表格源损坏类事故）
- [ ] 从 `scientific-writing/editor-first-impression.md` 摘取要点，用于预批提案/cover letter 模板优化
- [x] 分批生成一/二/三期全部落地（2026-08-22）：一期契约+分段+门禁；二期程序化表格+摘要后置；三期 paper-staged-gen 技能已部署（~/.dsh/skills/）+ DSH Agent 委托编排（generate delegate）+ Web 段级进度面板（/api/gen/*）。draft 阶段长文写作默认走本管线


## 绘图后端路由（Origin / nature-figure）

- **数据图（XY/散点/折线）需 Origin 风格/出版级** → 走 `figure_origin.py`（COM 驱动 Origin 2019b，进程内单实例）。
  **路由分两条**：
  - **DSH 侧（论文生成时）** → 用 `figure-origin` 技能（`~/.dsh/skills/figure-origin/`），DSH agent 经 shell 调 `figure_origin.py`（DSH 不走 MCP）。
  - **Qoder 侧（交互时）** → `origin-figure` MCP 服务（已注册 mcp.json，工具 origin_status / origin_plot_xy / origin_plot_spec）。
- **通用科研绘图（matplotlib/seaborn/ggplot2）** → 走 `nature-figure` 技能（Python 后端已实测出图）。
- **示意图/机制图/图形摘要** → `nature-figure` 的 OpenRouter GPT Image 2 路由。
- 路由优先级：明确要 Origin → figure_origin；否则 nature-figure。

### 绘图路由细化（2026-08-22 补充）
- **数据图（柱/线/散点）** → matplotlib + `figure_styles.py`（默认 ggsci NPG 配色，色盲友好）
- **示意图/流程图/路线图** → **PPT 绘图路由** `figure_pptx.py`（python-pptx 绘制 → PowerPoint COM 导出 300dpi+ PNG；源 PPTX 存档可二次编辑）。开源依据：python-pptx（MIT，PyPI）+ 本机 PowerPoint。
- **要 Origin 风格的数据图** → `figure_origin.py`（Origin COM）
- 配色禁用默认 matplotlib 蓝/橙；统一走 figure_styles 色板。

### 绘图路由执行层（2026-08-23 改造）
- 三条路径统一经 `figure_router.py` 分发（CLI `--json spec.json` / Web `POST /api/figure/render`），决策规则见 `paper-figure-routing` 技能。
- 技能注入改为阶段感知（ai_client.get_stage_skills）：draft 注入写作+绘图路由技能，review 注入审稿/润色/核验技能，均附一句话描述。
- DSH 委托（generate delegate）指令自动附阶段建议技能清单。
