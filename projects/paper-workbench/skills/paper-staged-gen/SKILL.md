---
name: paper-staged-gen
description: Use when generating or regenerating a full manuscript with the DSH Paper Workbench staged pipeline — contract-first, per-section generation with citation-pool gates, programmatic tables, post-hoc abstract. Routes the `wb.py generate` subcommands; never one-shot a long manuscript.
---

# Paper Staged Generation (paper-staged-gen)

分批生成编排协议。配套引擎：`~/.dsh/papers/workbench/staged_gen.py` + `wb.py generate`。
设计见工作区《分批生成方案》。**核心信条：长文/多图表稿件绝不一次性整篇生成。**

## 何时触发

- 用户要"写一篇综述/长文"或"重写 main.md"，且目标稿 > 3000 词或含 ≥2 张图表
- 一次性生成的稿件出现表格损坏、图文分离、幻觉引用、摘要漂移，需要重写
- 已有 `draft/contract.md`，要继续/恢复分段生成

## 不可违反的纪律

1. **契约先行，锁定才写**：`draft/contract.md` 未"已锁定"前，禁止生成任何正文段落。锁定是人工检查点（S0），你只能提示用户去锁定，不能替用户锁定。
2. **引文池硬约束**：每段只能引用契约「引文分配」中分给该段的文献编号。段级门禁会自动拦截池外引用——被拦时不要硬塞，回头检查分配是否合理。
3. **表格只产数据不产排版**：表格一律 `generate tables`（JSON→管道表）。你只负责产出行数据（或 `--extract` 迁移存量），绝不手写表格排版。
4. **摘要后置**：摘要在全部正文段落定稿后用 `generate abstract` 生成，基于各段摘要而非全文；对齐校验失败就修，不要绕过。
5. **失败重试有上限**：单段门禁失败自动重试 1 次，仍失败则停下报告用户，不要无限循环。

## 标准编排序列（S0→S8）

```
wb.py generate init                       # 初始化 draft/（仅首次）
wb.py generate contract [--ai]            # 生成契约 → 交用户补全并锁定（S0 人工检查点）
   ── 等待用户把「契约状态：待锁定」改为「已锁定」──
wb.py generate status                     # 查看段级进度，确定下一个待写段
wb.py generate section --sid S<N>         # 逐段生成（自动门禁+重试）；重复直到全部 done
wb.py generate tables [--extract|--gen]   # 表格：迁移存量 / 渲染 / 委托产数据
wb.py generate abstract                   # 摘要后置生成 + 对齐校验
wb.py generate assemble                   # 拼装（自动插图插表、前置摘要）→ manuscript/main.md
```

每段生成后必须跑 `generate status` 确认 `done` 再进下一段；出现 `failed` 先看 issues 再决定重试或人工。

## 段级门禁三件套（自动，勿绕过）

- 占位符/双标点/超长句（复用 `toolbox.mechanical_check` 的 P0/P1）
- 引文范围 ⊆ 本节分配池（幻觉引用段级拦截）
- 字数预算 ±20%

## 决策快捷

- "这段引用了不在分配池的文献" → 查契约引文分配，缺则补分配并提醒用户重新锁定，不要删引用硬凑
- "表格又乱了" → 一定是有人手写了表格；改走 `generate tables` JSON 渲染
- "摘要说了正文没有的" → 对齐校验会拦；让摘要回退到各段摘要重写，不要从全文"发挥"
- "能不能一次生成完" → 不能。除非稿件 < 2500 词且无图表，否则坚持分段

## 与其它技能的关系

- 写作风格仍从 `quality_rules.md` + `build_stage_prompt` 取；本技能只管**生成编排与门禁**，不改写作风格源
- 审查仍走 `review-auto` 门禁，本技能不替代审查
- 期刊格式以 `journal/chosen.md` 为准，跨刊不得套用其它期刊预算
