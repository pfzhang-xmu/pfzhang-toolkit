# -*- coding: utf-8 -*-
"""register_mcp.py — 把 paper-workbench MCP 服务一键注册到本机各 agent 生态。

支持（存在即写, 不存在则打印手动配置片段）：
  - Qoder   ~/.qoder-cn/mcp.json
  - Cursor  ~/.cursor/mcp.json
  - Codex   ~/.codex/config.toml
  - Claude Code  打印 `claude mcp add` 命令（其配置由 CLI 自管理, 不直接改文件）
  - TRAE/Generic 打印 JSON 片段（TRAE 在设置界面添加 MCP）

用法: python register_mcp.py [--remove]
幂等：已注册则更新命令路径, 不产生重复条目。
"""
import json
import sys
from pathlib import Path

HOME = Path.home()
WB = Path(__file__).resolve().parent
SERVER = WB / "workbench_mcp.py"
PY = sys.executable.replace("\\", "/")
NAME = "paper-workbench"

ENTRY = {
    "command": PY,
    "args": [str(SERVER).replace("\\", "/")],
    "env": {"PYTHONIOENCODING": "utf-8"},
}


def reg_qoder(remove=False):
    p = HOME / ".qoder-cn" / "mcp.json"
    if not p.parent.exists():
        return "skip（~/.qoder-cn 不存在）"
    cfg = {}
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    servers = cfg.setdefault("mcpServers", {})
    if remove:
        servers.pop(NAME, None)
    else:
        servers[NAME] = ENTRY
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return "ok" + ("（已移除）" if remove else "")


def reg_cursor(remove=False):
    p = HOME / ".cursor" / "mcp.json"
    if not p.parent.exists():
        return "skip（~/.cursor 不存在）"
    cfg = {}
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    servers = cfg.setdefault("mcpServers", {})
    if remove:
        servers.pop(NAME, None)
    else:
        servers[NAME] = ENTRY
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return "ok" + ("（已移除）" if remove else "")


def reg_codex(remove=False):
    p = HOME / ".codex" / "config.toml"
    if not p.parent.exists():
        return "skip（~/.codex 不存在）"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    header = f"[mcp_servers.{NAME}]"
    if header in text:
        # 幂等：先移除旧块
        lines = text.splitlines()
        out, skipping = [], False
        for ln in lines:
            if ln.strip() == header:
                skipping = True
                continue
            if skipping and ln.strip().startswith("[") and not ln.strip() == header:
                skipping = False
            if not skipping:
                out.append(ln)
        text = "\n".join(out).rstrip() + "\n"
    if not remove:
        block = (
            f"\n{header}\n"
            f'command = "{PY}"\n'
            f'args = ["{str(SERVER).replace(chr(92), "/")}"]\n'
        )
        text = text.rstrip() + "\n" + block
    p.write_text(text, encoding="utf-8")
    return "ok" + ("（已移除）" if remove else "")


def main():
    remove = "--remove" in sys.argv
    print(f"paper-workbench MCP {'注销' if remove else '注册'}  server={SERVER}")
    print(f"  Qoder  : {reg_qoder(remove)}")
    print(f"  Cursor : {reg_cursor(remove)}")
    print(f"  Codex  : {reg_codex(remove)}")
    print("  Claude Code（手动执行一次）:")
    print(f'    claude mcp add {NAME} -- {PY} "{SERVER}"')
    print("  TRAE/Generic（设置界面添加 MCP, JSON 片段）:")
    print(json.dumps({"mcpServers": {NAME: ENTRY}}, ensure_ascii=False, indent=2))
    print("\n注册后重启对应 agent, 即可自主调用 search_skills/read_skill/quality_check/"
          "figure_render/process_audit 等工具与 400+ 学术技能。")


if __name__ == "__main__":
    main()
