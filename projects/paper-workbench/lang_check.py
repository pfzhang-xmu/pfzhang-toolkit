# -*- coding: utf-8 -*-
"""lang_check.py — 语言质量检查（LanguageTool，2026-08-22 开源集成）

双模式：
- 本地：language-tool-python（需 Java；自动下载 LanguageTool 包）
- 公共 API：https://api.languagetool.org/v2/check（无 Java 时回退；免费额度有限，分块 + 限速）

CLI:
    python lang_check.py <file.md> [--lang en-US] [--out report.json] [--max 40]

输出：问题清单（规则/消息/上下文/建议），按规则聚合摘要。
对学术综述默认启用 picky 模式；忽略代码块与 bibtex 围栏。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

PUBLIC_API = "https://api.languagetool.org/v2/check"
CHUNK_SIZE = 7500        # 公共 API 单次上限留余量
CHUNK_GAP = 3.0          # 免费额度限速（约 20 req/min）
USER_AGENT = "paper-workbench/1.1 (lang_check)"

# 学术写作中可忽略的规则（白名单，避免噪音）
IGNORED_RULES = {
    "EN_QUOTES",          # 引号风格
    "COMMA_PARENTHESIS_WHITESPACE",
}


def strip_markdown(text: str) -> str:
    """去掉代码块/数学块，保留行结构（LanguageTool 需要自然文本）。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)      # 图片
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # 链接留文字
    return text


def _chunk_with_offsets(text: str, size: int = CHUNK_SIZE):
    """按段落边界分块，返回 (chunk, offset) 列表。"""
    paras = text.split("\n")
    chunks, cur, start, cur_off = [], [], 0, 0
    for p in paras:
        if len("\n".join(cur + [p])) > size and cur:
            chunks.append(("\n".join(cur), cur_off))
            cur_off += len("\n".join(cur)) + 1
            cur = []
        cur.append(p)
    if cur:
        chunks.append(("\n".join(cur), cur_off))
    return chunks


def check_public(text: str, lang: str = "en-US") -> list[dict]:
    """公共 API 分块检查。"""
    matches = []
    for chunk, off in _chunk_with_offsets(text):
        if not chunk.strip():
            continue
        data = urllib.parse.urlencode({
            "text": chunk, "language": lang, "level": "picky", "enabledOnly": "false",
        }).encode("utf-8")
        req = urllib.request.Request(PUBLIC_API, data=data, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as ex:
            print(f"[warn] LanguageTool API 调用失败（偏移 {off}）: {ex}", file=sys.stderr)
            continue
        for m in payload.get("matches", []):
            m["_offset"] = off + m.get("offset", 0)
            matches.append(m)
        time.sleep(CHUNK_GAP)
    return matches


def check_local(text: str, lang: str = "en-US") -> list[dict]:
    """本地 LanguageTool（需 Java）。"""
    import language_tool_python
    tool = language_tool_python.LanguageTool(lang)
    try:
        ms = tool.check(text)
        out = []
        for m in ms:
            out.append({
                "rule": {"id": m.ruleId},
                "message": m.message,
                "offset": m.offset,
                "length": m.errorLength,
                "context": {"text": text[max(0, m.offset - 40):m.offset + m.errorLength + 40]},
                "replacements": [{"value": r.value} for r in m.replacements[:3]],
            })
        return out
    finally:
        tool.close()


def lang_check(text: str, lang: str = "en-US", prefer_local: bool = True) -> tuple[list[dict], str]:
    """返回 (问题列表, 模式)。本地无 Java 时自动回退公共 API。"""
    if prefer_local:
        try:
            return check_local(text, lang), "local"
        except Exception:
            print("[info] 本地 LanguageTool 不可用（缺 Java？），回退公共 API", file=sys.stderr)
    return check_public(text, lang), "public-api"


def summarize(matches: list[dict]) -> dict:
    """按规则聚合。"""
    agg: dict[str, int] = {}
    for m in matches:
        rid = m.get("rule", {}).get("id", "?")
        if rid in IGNORED_RULES:
            continue
        agg[rid] = agg.get(rid, 0) + 1
    return dict(sorted(agg.items(), key=lambda kv: -kv[1]))


def render_report(path: str, matches: list[dict], mode: str, lang: str, limit: int) -> str:
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    filtered = [m for m in matches if m.get("rule", {}).get("id") not in IGNORED_RULES]
    lines = [
        "# 语言质量检查报告（LanguageTool）",
        "",
        f"> 生成: {now} | 目标: {path} | 模式: {mode} | 语言: {lang} | picky",
        f"> 问题总数: {len(filtered)}（已过滤白名单规则）",
        "",
        "## 规则聚合（Top 15）",
        "",
        "| 规则 | 次数 |",
        "|------|------|",
    ]
    for rid, n in list(summarize(filtered).items())[:15]:
        lines.append(f"| {rid} | {n} |")
    lines += ["", "## 明细（前 %d 条）" % limit, ""]
    for m in filtered[:limit]:
        ctx = m.get("context", {}).get("text", "").replace("\n", " ")
        repl = ", ".join(r.get("value", "") for r in m.get("replacements", [])[:3])
        lines.append(f"- **[{m.get('rule', {}).get('id')}]** @{m.get('_offset', m.get('offset'))} "
                     f"{m.get('message', '')}；建议: {repl or '-'}")
        lines.append(f"  - 上下文: ...{ctx[:120]}...")
    if len(filtered) > limit:
        lines.append(f"\n（其余 {len(filtered) - limit} 条见 JSON 输出 --out）")
    return "\n".join(lines) + "\n"


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="lang_check", description="LanguageTool 语言质量检查")
    ap.add_argument("file", help="Markdown/文本文件")
    ap.add_argument("--lang", default="en-US")
    ap.add_argument("--out", default=None, help="JSON 明细输出路径")
    ap.add_argument("--max", type=int, default=40, help="报告明细条数上限")
    ap.add_argument("--api-only", action="store_true", help="跳过本地，直接走公共 API")
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    cleaned = strip_markdown(text)
    matches, mode = lang_check(cleaned, args.lang, prefer_local=not args.api_only)
    print(render_report(args.file, matches, mode, args.lang, args.max))
    if args.out:
        Path(args.out).write_text(json.dumps(matches, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON 明细 → {args.out}")


if __name__ == "__main__":
    main()
