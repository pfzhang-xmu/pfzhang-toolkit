# -*- coding: utf-8 -*-
"""origin_mcp_server.py — Origin 绘图 MCP 服务（stdio）。

把 figure_origin 桥暴露为 MCP 工具，供 Qoder/DSH Agent 通过 MCP 路由调用：
  - origin_status      检测 Origin 是否可驱动
  - origin_plot_xy     XY 散点/折线出图并导出文件
  - origin_plot_spec   用 JSON 规格出图（多曲线/样式）

注册（~/.qoder-cn/mcp.json）：
  { "mcpServers": { "origin-figure": {
      "command": "python",
      "args": ["C:/path/to/workbench/origin_mcp_server.py"] } } }

兼容 Origin 2019b（COM+LabTalk，不依赖 originpro）。
"""
from __future__ import annotations

import json
import os
import sys

# 让服务能 import 同目录的 figure_origin
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("origin-figure")


@mcp.tool()
def origin_status() -> str:
    """检测本机 Origin 是否可被 COM 驱动（返回连接状态）。"""
    try:
        import figure_origin
        figure_origin.OriginBridge()
        return json.dumps({"ok": True, "msg": "Origin COM 连接成功，可驱动出图"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "msg": str(e)}, ensure_ascii=False)


@mcp.tool()
def origin_plot_xy(xs: list, ys: list, out: str, kind: str = "scatter",
                   xlabel: str = "", ylabel: str = "", legend: bool = False) -> str:
    """用 Origin 画 XY 图并导出。

    xs: X 轴数值数组；ys: Y 轴数值数组（或二维数组=多条曲线）；
    out: 输出文件完整路径(.png/.tiff/.svg/.pdf)；
    kind: scatter(散点)/line(折线)/line+symbol；xlabel/ylabel 轴标题；
    legend 是否保留图例。返回 {ok, file}。
    """
    import figure_origin
    try:
        r = figure_origin.OriginBridge().plot_xy(
            xs, ys, out, kind=kind, xlabel=xlabel or None,
            ylabel=ylabel or None, legend=legend)
        return json.dumps(r, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def origin_plot_spec(spec_json: str) -> str:
    """用 JSON 规格出图。spec_json 形如:
    {"xs":[..],"ys":[..] 或 "series":[[..],[..]],"out":"路径","kind":"scatter",
     "xlabel":"..","ylabel":"..","title_font":20,"tick_font":18,"legend":false}
    返回 {ok, file}。"""
    import figure_origin
    try:
        spec = json.loads(spec_json)
        r = figure_origin.render_from_spec(spec)
        return json.dumps(r, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
