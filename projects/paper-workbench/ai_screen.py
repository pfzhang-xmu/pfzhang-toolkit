# -*- coding: utf-8 -*-
"""ai_screen.py — AI 痕迹预筛查（红旗标记，非最终裁决）

定位（2026-08-22 开源集成调研结论）：
- 开源困惑度检测器（GPTZero 开源实现/Binoculars 等）需下载大模型且对
  润色过的学术文本误判率高，不适合内嵌工作台；
- 本工具做轻量统计预筛查：句长突发性（burstiness 近似）、AI 套话密度、
  连接词同质化、绝对化表述，输出高风险段落清单供人工改写；
- 最终 AI 检测/查重仍以商业工具（Turnitin/iThenticate/GPTZero）交叉验证为准。

CLI:
    python ai_screen.py <file.md> [--out report.json]
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

# AI 套话黑名单（来自 premium-model-workflow.md 3.2.2，随审阅反馈扩充）
AI_FILLER_PATTERNS = [
    (r"\bin recent years\b", "in recent years"),
    (r"\bplays a (crucial|vital|pivotal) role\b", "plays a crucial role"),
    (r"\bit is worth noting that\b", "it is worth noting that"),
    (r"\bit should be noted that\b", "it should be noted that"),
    (r"\bdelve[sd]?\b", "delve"),
    (r"\bcomprehensive overview\b", "comprehensive overview"),
    (r"\ba testament to\b", "a testament to"),
    (r"\bin the ever-evolving\b", "in the ever-evolving"),
    (r"\bharnessing the power\b", "harnessing the power"),
    (r"\bsheds light on\b", "sheds light on"),
    (r"\brobust and versatile\b", "robust and versatile"),
    (r"\bparamount importance\b", "paramount importance"),
    (r"\bgarnered significant attention\b", "garnered attention"),
    (r"\bhas emerged as a\b", "has emerged as a"),
    (r"\ba wealth of evidence\b", "a wealth of evidence"),
    (r"\bthe landscape of\b", "the landscape of"),
]

ABSOLUTE_PATTERNS = [
    r"\bproves?\b", r"\bconclusively\b", r"\bwithout doubt\b",
    r"\bit is clear that\b", r"\bundoubtedly\b", r"\bguarantees?\b",
]

CONNECTIVES = ("Moreover", "Furthermore", "Additionally", "In addition", "Notably")


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 3]


def screen_paragraph(para: str) -> dict:
    """单段打分：返回 {fillers, absolutes, sent_cv, n_sents}。"""
    sents = split_sentences(para)
    lens = [len(s.split()) for s in sents if len(s.split()) >= 3]
    cv = 0.0
    if len(lens) >= 3:
        mean = statistics.mean(lens)
        cv = statistics.stdev(lens) / mean if mean else 0.0
    fillers = []
    low = para.lower()
    for pat, label in AI_FILLER_PATTERNS:
        if re.search(pat, low):
            fillers.append(label)
    absolutes = [p for p in ABSOLUTE_PATTERNS if re.search(p, low)]
    return {"fillers": fillers, "absolutes": absolutes,
            "sent_cv": round(cv, 3), "n_sents": len(sents),
            "words": len(para.split())}


def ai_screen(text: str) -> dict:
    # 段落切分（跳过标题/表格/代码）
    paras = []
    for blk in re.split(r"\n\s*\n", text):
        blk = blk.strip()
        if not blk or blk.startswith("#") or blk.startswith("|") or blk.startswith("```"):
            continue
        paras.append(re.sub(r"\s+", " ", blk))

    results = []
    connective_streak = 0
    for idx, p in enumerate(paras):
        r = screen_paragraph(p)
        risk = 0
        reasons = []
        if r["fillers"]:
            risk += 20 * len(r["fillers"])
            reasons.append("AI 套话: " + ", ".join(r["fillers"]))
        if r["absolutes"]:
            risk += 10 * len(r["absolutes"])
            reasons.append("绝对化表述")
        # 句长过于均匀（CV < 0.25 且句数 >=4）→ 突发性低
        if r["n_sents"] >= 4 and r["sent_cv"] < 0.25:
            risk += 25
            reasons.append(f"句长高度均匀（CV={r['sent_cv']}）")
        # 连续段落以模板连接词开头
        head = p.split()[0].rstrip(",") if p.split() else ""
        if head in CONNECTIVES:
            connective_streak += 1
            if connective_streak >= 3:
                risk += 15
                reasons.append("连续段落模板化连接词开头")
        else:
            connective_streak = 0
        results.append({
            "para": idx + 1, "words": r["words"], "risk": min(100, risk),
            "reasons": reasons, "preview": p[:120] + ("..." if len(p) > 120 else ""),
        })

    high = [r for r in results if r["risk"] >= 30]
    total_words = sum(r["words"] for r in results)
    doc_score = round(sum(r["risk"] * r["words"] for r in results) / max(total_words, 1), 1)
    return {"paragraphs": results, "high_risk": high,
            "doc_score": doc_score, "total_words": total_words,
            "n_paras": len(results)}


def render_report(path: str, res: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    level = "低风险" if res["doc_score"] < 10 else ("中风险" if res["doc_score"] < 25 else "高风险")
    lines = [
        "# AI 痕迹预筛查报告",
        "",
        f"> 生成: {now} | 目标: {path} | 工具: ai_screen.py（统计红旗，非最终裁决）",
        f"> 全文加权风险分: **{res['doc_score']}**（{level}）| 段落数: {res['n_paras']} | 词数: {res['total_words']}",
        "> 判定阈值: <10 低风险 / 10-25 中风险 / ≥25 高风险；最终投稿前仍需商业检测工具（≥2 种）交叉验证。",
        "",
        "## 高风险段落（风险分 ≥30，人工改写优先目标）",
        "",
    ]
    if res["high_risk"]:
        lines.append("| 段落 | 风险分 | 原因 | 预览 |")
        lines.append("|------|--------|------|------|")
        for r in res["high_risk"]:
            lines.append(f"| {r['para']} | {r['risk']} | {'; '.join(r['reasons'])} | {r['preview'][:80]} |")
    else:
        lines.append("- 未发现风险分 ≥30 的段落")
    lines += ["", "## 改写建议", "",
              "- 套话替换为具体数据/文献支撑的陈述（AI 文本的典型弱点是空泛）",
              "- 句长均匀的段落：拆分模板化长句、变换句首结构",
              "- 注入领域惯用表述与批判性评价（\"该方法的局限在于……\"）",
              "- 改写后用商业检测工具复检，两种工具均低于阈值方可投稿"]
    return "\n".join(lines) + "\n"


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="ai_screen", description="AI 痕迹预筛查（统计红旗）")
    ap.add_argument("file")
    ap.add_argument("--out", default=None, help="JSON 明细输出")
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    res = ai_screen(text)
    print(render_report(args.file, res))
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON 明细 → {args.out}")


if __name__ == "__main__":
    main()
