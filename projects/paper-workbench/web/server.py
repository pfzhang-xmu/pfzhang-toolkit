#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench Web - 本地论文工作台(桌面风格 Web UI)
启动: python server.py [端口,默认 8123]
打开: http://127.0.0.1:8123
"""
import base64
import html as html_lib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _engine_root():
    """引擎根目录；PyInstaller 打包后指向解压资源目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path.cwd()))
    return Path(__file__).resolve().parent.parent


def _port_from_argv():
    if len(sys.argv) > 1:
        try:
            return int(sys.argv[1])
        except (TypeError, ValueError):
            return 8123
    return 8123


ENGINE = _engine_root() / "wb.py"
PAPERS = Path.home() / ".dsh" / "papers"
SKILLS_ROOT = Path.home() / ".dsh" / "skills"
STATE_FILE = "state.json"
PORT = _port_from_argv()

ALLOWED_ACTIONS = ["init", "recommend", "status", "next", "stage", "new", "check", "review-book", "review-auto", "summary", "doctor"]
TEXT_SUFFIX = (".md", ".txt", ".json", ".bib", ".tex", ".yml", ".yaml", ".csv")
IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

# 延迟导入,避免缺依赖时整个服务起不来
sys.path.insert(0, str(_engine_root()))
import wb
import ai_client
import charts
import tables
import toolbox
import data2paper
import imagegen_bridge
import dsh_bridge

STAGE_LABEL = wb.STAGE_LABEL


# ─────────────────────────── 基础工具 ───────────────────────────

def run_wb(args, timeout=120):
    if getattr(sys, "frozen", False):
        # 打包后无独立 python 解释器，直接函数调用 wb.main()（重定向输出）
        import contextlib
        import io
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["wb.py"] + args
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                wb.main()
        except SystemExit:
            pass
        except Exception as e:
            return f"(工作台引擎错误: {e})"
        finally:
            sys.argv = old_argv
        return buf.getvalue()
    try:
        r = subprocess.run(
            [sys.executable, str(ENGINE)] + args,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=str(ENGINE.parent),
        )
        return r.stdout + (r.stderr or "")
    except Exception as e:
        return f"(工作台引擎错误: {e})"


def project_roots():
    """返回项目根目录列表（默认 ~/.dsh/papers + 用户桌面 paper 目录）。"""
    cfg = ai_client.load_config()
    roots = cfg.get("project_roots") or []
    if not roots:
        roots = [str(PAPERS)]
        default_root = Path.home() / "Desktop" / "paper"
        if default_root.exists():
            roots.append(str(default_root))
        cfg["project_roots"] = roots
        ai_client.save_config(cfg)
    # Expand user-relative roots such as ~/.dsh/papers before checking them.
    # Path('~') is literal on Python and otherwise silently hides projects
    # stored under the user's home directory.
    expanded = [Path(str(r)).expanduser() for r in roots]
    return [p for p in expanded if p.exists()]


def list_projects():
    """扫描所有项目根目录下含 state.json 的项目。"""
    out = []
    seen = set()
    for root in project_roots():
        if not root.exists():
            continue
        for d in root.iterdir():
            st = d / STATE_FILE
            if not d.is_dir() or not st.exists():
                continue
            key = str(d.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                data = json.loads(st.read_text(encoding="utf-8"))
                out.append({
                    "path": str(d),
                    "name": d.name,
                    "topic": data.get("topic", d.name),
                    "journal": data.get("journal", ""),
                    "type": data.get("type", "article"),
                    "stage": data.get("stage", ""),
                    "stageLabel": STAGE_LABEL.get(data.get("stage", ""), ""),
                    "updated": data.get("updated", ""),
                })
            except Exception:
                continue
    out.sort(key=lambda p: p["updated"], reverse=True)
    return out


def read_project_file(proj_path, rel):
    """读取项目内产物文件(路径白名单);图片返回 base64。"""
    root = Path(proj_path).resolve()
    if not root.exists():
        return None
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        return None
    if not target.exists() or not target.is_file():
        return None
    if target.suffix.lower() in IMAGE_SUFFIX:
        try:
            raw = target.read_bytes()
            suffix = target.suffix.lower().lstrip(".")
            mime = "image/svg+xml" if suffix == "svg" else f"image/{suffix}"
            return {"image": base64.b64encode(raw).decode("ascii"), "mime": mime}
        except Exception:
            return None
    if target.suffix.lower() not in TEXT_SUFFIX:
        return None
    try:
        return {"text": target.read_text(encoding="utf-8", errors="replace")[:200000]}
    except Exception:
        return None


def read_standalone_file(rel):
    """Read an image from the project-independent figure store only."""
    base = imagegen_bridge.STANDALONE_FIGURES_ROOT.resolve()
    target = (Path(_engine_root()) / str(rel or "")).resolve()
    if base not in target.parents or target.suffix.lower() not in IMAGE_SUFFIX:
        return None
    if not target.exists() or not target.is_file():
        return None
    try:
        suffix = target.suffix.lower().lstrip(".")
        mime = "image/svg+xml" if suffix == "svg" else f"image/{suffix}"
        return {"image": base64.b64encode(target.read_bytes()).decode("ascii"), "mime": mime}
    except Exception:
        return None


def project_tree(proj):
    root = Path(proj).resolve() if proj else None
    if root is None or not (root / STATE_FILE).exists():
        return {"error": "项目不存在"}
    result = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        # 防止 junction/symlink 逃逸项目目录
        try:
            dres = d.resolve()
        except Exception:
            continue
        if dres != root and root not in dres.parents:
            continue
        files = []
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in TEXT_SUFFIX + IMAGE_SUFFIX:
                try:
                    size = f.stat().st_size
                except Exception:
                    size = 0
                files.append({"name": f.name, "size": size})
        result.append({"dir": d.name, "files": files})
    return {"project": str(root), "tree": result}


def load_state_detail(proj):
    """返回项目状态 + 各阶段检查点/产物,供 UI 直接渲染。"""
    root = Path(proj).resolve()
    st_path = root / STATE_FILE
    if not st_path.exists():
        return None
    st = json.loads(st_path.read_text(encoding="utf-8"))
    checklists = {}
    for stage in ["research", "journal", "framework", "draft", "review", "submit"]:
        ck_path = _engine_root() / "checklists" / f"{stage}.md"
        items = []
        if ck_path.exists():
            for line in ck_path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*-\s*\[ \]\s+(.*)$", line)
                if m:
                    items.append(m.group(1).strip())
        checked = set(st.get("stages", {}).get(stage, {}).get("checked", []))
        checklists[stage] = {
            "items": items,
            "checked": sorted(checked),
            "done": st.get("stages", {}).get(stage, {}).get("done", False),
        }
    return {
        "path": str(root),
        "topic": st.get("topic", ""),
        "journal": st.get("journal", ""),
        "lang": st.get("lang", ""),
        "type": st.get("type", ""),
        "stage": st.get("stage", ""),
        "created": st.get("created", ""),
        "updated": st.get("updated", ""),
        "stages": checklists,
        "logs": st.get("logs", [])[-10:],
    }


def project_health(proj):
    """轻量健康检查：已完成的阶段是否缺产物 / 检查点未全勾（doctor 的批量版）。"""
    root = Path(proj).resolve()
    st_path = root / STATE_FILE
    if not st_path.exists():
        return []
    try:
        st = json.loads(st_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    issues = []
    for s in ["research", "journal", "framework", "draft", "review", "submit"]:
        done = st.get("stages", {}).get(s, {}).get("done", False)
        if not done:
            continue
        for rel in wb.STAGE_ARTIFACTS.get(s, []):
            if not (root / rel).exists():
                issues.append(f"{s}: 缺产物 {rel}")
        ck = wb.read_checklist(s)
        checked = set(st.get("stages", {}).get(s, {}).get("checked", []))
        if ck and len(checked) < len(ck):
            issues.append(f"{s}: 检查点 {len(checked)}/{len(ck)}")
    return issues


_DASH_TTL = 30.0
_dash_cache = {"t": 0.0, "data": None}
_dash_lock = threading.Lock()


def dashboard_data(force=False):
    """聚合所有项目进度，供总览面板使用。

    每次全量计算需对每个项目跑 quality_check(秒级, 随项目数线性增长),
    故加 TTL 缓存; 锁保证并发冷请求只计算一次、其余复用结果,
    避免高频短超时轮询把 ThreadingHTTPServer 拖入请求堆积雪崩。
    ?refresh=1 强制刷新。"""
    with _dash_lock:
        now = time.time()
        if (not force and _dash_cache["data"] is not None
                and now - _dash_cache["t"] < _DASH_TTL):
            return _dash_cache["data"]
        data = _compute_dashboard_data()
        _dash_cache["t"] = now
        _dash_cache["data"] = data
        return data


def _compute_dashboard_data():
    try:
        import toolbox
    except Exception:
        toolbox = None
    projects = list_projects()
    out = []
    for p in projects:
        detail = load_state_detail(p["path"])
        if not detail:
            continue
        stages = detail.get("stages", {})
        total_checks = sum(len(s.get("items", [])) for s in stages.values())
        done_checks = sum(len(s.get("checked", [])) for s in stages.values())
        item = {
            **p,
            "totalChecks": total_checks,
            "doneChecks": done_checks,
            "stageDone": {k: v.get("done", False) for k, v in stages.items()},
            "currentStage": detail.get("stage", ""),
            "health": project_health(p["path"]),
        }
        # 质量摘要: 每条项目附带 quality_check 结果(分/级别/P0P1P2 + 问题数), 供首页直接展示
        if toolbox is not None:
            try:
                qres = toolbox.quality_check(p["path"])  # 被动读取不记录历史
                qscore = toolbox.quality_score(qres)
                item["quality"] = qscore or {}
                # 质量收敛历史(显式质检时由 toolbox 落盘), 供首页趋势曲线
                try:
                    hf = Path(p["path"]) / "review" / "quality-history.json"
                    if hf.exists():
                        item["quality"]["history"] = json.loads(hf.read_text(encoding="utf-8"))
                except Exception:
                    pass
                item["issueCount"] = {
                    "P0": sum(1 for i in qres if i.get("severity") == "P0"),
                    "P1": sum(1 for i in qres if i.get("severity") == "P1"),
                    "P2": sum(1 for i in qres if i.get("severity") == "P2"),
                }
                item["topIssues"] = [
                    {"severity": i.get("severity"), "type": i.get("type", ""), "msg": i.get("msg", ""), "line": i.get("line")}
                    for i in qres[:6]
                ]
            except Exception:
                pass
        out.append(item)
    return out


# ─────────────────────────── 技能管理 ───────────────────────────

def _parse_skill_description(skill_dir):
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return ""
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    return ""


def list_skills():
    cfg = ai_client.load_config()
    disabled = set(cfg.get("skills", {}).get("disabled", []))
    out = []
    if SKILLS_ROOT.exists():
        for d in sorted(SKILLS_ROOT.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                out.append({
                    "name": d.name,
                    "description": _parse_skill_description(d),
                    "enabled": d.name not in disabled,
                    "path": str(d),
                })
    return out


def set_skill_enabled(name, enabled):
    # 校验技能存在, 避免前端误传或历史残留名导致静默"成功"
    if not (SKILLS_ROOT / name / "SKILL.md").exists():
        return {"error": f"技能不存在: {name}", "name": name}
    cfg = ai_client.load_config()
    disabled = list(cfg.setdefault("skills", {}).setdefault("disabled", []))
    if enabled:
        if name in disabled:
            disabled.remove(name)
    else:
        if name not in disabled:
            disabled.append(name)
    cfg["skills"]["disabled"] = disabled
    ai_client.save_config(cfg)
    return {"name": name, "enabled": enabled}


def md_with_embedded_images(md_path, text):
    """把正文图片相对路径替换为 data URL，供 HTML 预览直接渲染。"""
    md_path = Path(md_path)
    base = md_path.parent
    candidates = [base, base.parent, base.parent.parent]

    def _fix(m):
        alt, rel = m.group(1), m.group(2)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", rel.strip()):
            return m.group(0)
        for cand in candidates:
            p = (cand / rel.strip()).resolve()
            if p.exists() and p.is_file():
                try:
                    raw = p.read_bytes()
                    suffix = p.suffix.lower().lstrip(".")
                    mime = "image/svg+xml" if suffix == "svg" else f"image/{suffix}"
                    return f"![{alt}](data:{mime};base64,{base64.b64encode(raw).decode('ascii')})"
                except Exception:
                    return m.group(0)
        return m.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _fix, text)


# ─────────────────────── 项目级 API 数据层 (任务40 新增) ───────────────────────
# charts.judge_figure_routes / charts.generate_all_figures 由图表核心层并行开发,
# 落盘前一律 getattr 防护: 函数不存在 → 501 {"error": "图表路由核心层未就绪"},
# 不影响 server 启动与既有路由。

_FIG_ROUTE_NOT_READY = (501, {"error": "图表路由核心层未就绪"})


def _fig_stem(fig_id):
    """图编号(如 图1)规范化为安全文件名词干。"""
    return re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", str(fig_id)).strip("._-")


def project_figures_data(proj):
    """GET /api/project/figures: 图表路由 + 每项产物状态(存在性/rel/大小)。"""
    func = getattr(charts, "judge_figure_routes", None)
    if not callable(func):
        return _FIG_ROUTE_NOT_READY
    root = Path(proj)
    try:
        routes = func(str(root))
    except Exception as e:
        return 400, {"error": f"图表路由判定失败: {e}"}
    # 产物采集: 同时扫 data/charts(data 路线) 与 data/figures(imagegen 路线), 记录 rel 前缀
    files = []  # (Path, rel 前缀)
    for sub in ("charts", "figures"):
        d = root / "data" / sub
        if not d.exists():
            continue
        try:
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in IMAGE_SUFFIX:
                    files.append((f, f"data/{sub}/"))
        except Exception:
            continue
    out = []
    for item in (routes or []):
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        fid = str(item.get("编号") or "").strip()
        matched = None
        if fid:
            stem = _fig_stem(fid)
            for f, _pre in files:
                # 严格分段匹配: 词干全等, 或按 "_" 分段后首段全等(避免 图1 命中 图10_xxx)
                if f.stem == fid or f.stem == stem or (stem and f.stem.split("_")[0] == stem):
                    matched = f
                    break
        if matched is not None:
            try:
                size = matched.stat().st_size
            except Exception:
                size = 0
            pre = next((p for f2, p in files if f2 == matched), "data/charts/")
            entry["artifact"] = {"exists": True, "rel": pre + matched.name, "size": size}
        else:
            entry["artifact"] = {"exists": False, "rel": "", "size": 0}
        # imagegen 路线: 按 关键视觉 的缓存键定位 fig_imagegen_<key>.{png,jpg,webp}
        if str(item.get("route") or "").strip() == "imagegen":
            ig_art = {"exists": False, "rel": "", "size": 0}
            key_visual = str(item.get("关键视觉") or "").strip()
            ck_func = getattr(imagegen_bridge, "cache_key_for", None)
            if key_visual and callable(ck_func):
                try:
                    ckey = ck_func(key_visual)
                    fig_dir = root / "data" / "figures"
                    for ext in (".png", ".jpg", ".webp"):
                        cand = fig_dir / f"fig_imagegen_{ckey}{ext}"
                        if cand.exists() and cand.is_file():
                            try:
                                sz = cand.stat().st_size
                            except Exception:
                                sz = 0
                            ig_art = {"exists": True, "rel": f"data/figures/{cand.name}", "size": sz}
                            break
                except Exception:
                    ig_art = {"exists": False, "rel": "", "size": 0}
            entry["artifact"] = ig_art
        out.append(entry)
    # 附 imagegen 配置状态, 供 UI 引导条(写入仍走既有 /api/tools/sci_figure/settings)
    switch = _image_switch_enabled()
    img_status = {"enabled": False, "api_key_set": False, "switch": switch}
    try:
        get_settings = getattr(imagegen_bridge, "get_image_settings", None)
        if callable(get_settings):
            s = get_settings() or {}
            img_status = {
                "enabled": bool(switch) and bool(s.get("base_url")) and bool(s.get("model")) and bool(s.get("api_key_set")),
                "api_key_set": bool(s.get("api_key_set")),
                "switch": switch,
            }
    except Exception:
        pass
    return 200, {"project": str(root), "figures": out, "imagegen": img_status}


def project_pipeline_data(proj):
    """GET /api/project/pipeline: stage/六阶段 checked 数与总数/logs 尾20/各阶段产物存在性。"""
    root = Path(proj)
    if not (root / STATE_FILE).exists():
        return 404, {"error": "项目不存在或缺少 state.json"}
    detail = load_state_detail(str(root))
    if detail is None:
        return 404, {"error": "项目不存在或缺少 state.json"}
    stages_out = {}
    total_checks = done_checks = 0
    for s, info in detail.get("stages", {}).items():
        n_total = len(info.get("items", []))
        n_checked = len(info.get("checked", []))
        total_checks += n_total
        done_checks += n_checked
        artifacts = [{"rel": rel, "exists": (root / rel).exists()}
                     for rel in wb.STAGE_ARTIFACTS.get(s, [])]
        stages_out[s] = {"checked": n_checked, "total": n_total,
                         "done": info.get("done", False), "artifacts": artifacts}
    logs = detail.get("logs", [])
    try:  # load_state_detail 只带尾 10 条, 此处放宽到尾 20 条
        st = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        logs = (st.get("logs") or [])[-20:]
    except Exception:
        pass
    return 200, {
        "project": str(root),
        "stage": detail.get("stage", ""),
        "stageLabel": STAGE_LABEL.get(detail.get("stage", ""), ""),
        "progress": {"checked": done_checks, "total": total_checks},
        "stages": stages_out,
        "logs": logs,
    }


def project_history_data(proj):
    """GET /api/project/history: quality-history 全量 + qc-current 明细(存在则附); 均无→空数组。"""
    root = Path(proj)
    history = []
    hf = root / "review" / "quality-history.json"
    if hf.exists():
        try:
            data = json.loads(hf.read_text(encoding="utf-8-sig"))
            history = data if isinstance(data, list) else [data]
        except Exception:
            history = []
    qc = None
    qf = root / "research" / "qc-current.json"
    if qf.exists():
        try:
            qc = json.loads(qf.read_text(encoding="utf-8-sig"))
        except Exception:
            qc = None
    return 200, {"project": str(root), "history": history, "qcCurrent": qc}


def _mask_api_key(key):
    """api_key 掩码: 保留 sk- 前缀与末 4 位, 形如 sk-***abcd。"""
    key = (key or "").strip()
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 4 else ""
    head = key[:3] if key.startswith("sk-") else ""
    return f"{head}***{tail}"


def _guess_image_provider(base_url, model):
    u = (base_url or "").lower()
    m = (model or "").lower()
    if "openai.com" in u or "dall-e" in m or "gpt-image" in m:
        return "openai"
    if "kling" in u:
        return "kling"
    if "dashscope" in u or "aliyun" in u:
        return "tongyi"
    if "zhipu" in u or "cogview" in m:
        return "zhipu"
    return "openai-compatible" if u else ""


def _image_switch_enabled():
    """防护式读取 app_config image.enabled 开关, 缺省 True。"""
    try:
        load_cfg = getattr(ai_client, "load_config", None)
        if callable(load_cfg):
            img = (load_cfg() or {}).get("image", {}) or {}
            return bool(img.get("enabled", True))
    except Exception:
        pass
    return True


def images_settings_status():
    """GET /api/images/settings: 只读图片生成配置状态(掩码回显), 供 UI 引导条。
    写入复用既有 POST /api/tools/sci_figure/settings, 此处不做重复写逻辑。"""
    cfg = {}
    try:
        load_cfg = getattr(ai_client, "load_config", None)
        if callable(load_cfg):
            cfg = load_cfg() or {}
    except Exception:
        cfg = {}
    img = cfg.get("image", {}) or {}
    env_key = os.environ.get(getattr(imagegen_bridge, "ENV_IMAGE_KEY", "PAPER_WORKBENCH_IMAGE_KEY"), "").strip()
    api_key = (img.get("api_key") or "").strip()
    api_base = img.get("base_url", "")
    model = img.get("model", "")
    key_set = bool(api_key or env_key)
    switch = _image_switch_enabled()
    return 200, {
        "enabled": bool(switch and api_base and model and key_set),
        "switch": switch,
        "provider": _guess_image_provider(api_base, model),
        "api_base": api_base,
        "api_key_masked": _mask_api_key(api_key),
        "api_key_set": key_set,
        "model": model,
        "size": img.get("size", ""),
    }


# ─────────────────────────── HTTP Handler ───────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, "")

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        qs = urllib.parse.parse_qs(url.query)
        if path == "/" or path == "/index.html":
            html = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
            return self._send(200, html, "text/html; charset=utf-8")
        if path == "/figureforge" or path == "/figureforge/":
            html = (Path(__file__).parent / "figureforge.html").read_text(encoding="utf-8")
            return self._send(200, html, "text/html; charset=utf-8")
        if path.startswith("/figureforge/assets/") or path in ("/figureforge/figureforge.js", "/figureforge/figureforge.css"):
            rel = path[len("/figureforge/"):]
            fpath = (Path(__file__).parent / "static" / "figureforge" / rel).resolve()
            base = (Path(__file__).parent / "static" / "figureforge").resolve()
            if base not in fpath.parents or not fpath.is_file():
                return self._send_json(404, {"error": "FigureForge 资源不存在"})
            ext = fpath.suffix.lower()
            mime = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
            return self._send(200, fpath.read_bytes(), mime)
        if path == "/api/projects":
            return self._send_json(200, list_projects())
        if path == "/api/dashboard":
            force = qs.get("refresh", [""])[0] in ("1", "true")
            return self._send_json(200, {"projects": dashboard_data(force=force)})
        if path == "/api/state":
            proj = qs.get("proj", [""])[0]
            data = load_state_detail(proj)
            if data is None:
                return self._send_json(404, {"error": "项目不存在"})
            return self._send_json(200, data)
        if path == "/api/gen/status":
            proj = qs.get("proj", [""])[0]
            try:
                sys.path.insert(0, str(_engine_root()))
                import staged_gen as sg
                return self._send_json(200, sg.gen_status_detail(proj))
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if path == "/api/file":
            proj = qs.get("proj", [""])[0]
            rel = qs.get("rel", [""])[0]
            res = read_standalone_file(rel) if not proj and rel.startswith("data/figures/_standalone/") else read_project_file(proj, rel)
            if res is None:
                return self._send_json(404, {"error": "文件不可读或不存在"})
            return self._send_json(200, res)
        if path == "/api/download":
            fpath = qs.get("path", [""])[0]
            project = qs.get("project", [""])[0].strip()
            rel = qs.get("rel", [""])[0]
            if project and rel:
                root = Path(project).resolve()
                candidate = (root / rel).resolve()
                if not (root / "state.json").exists() or (candidate != root and root not in candidate.parents):
                    return self._send_json(403, {"error": "项目文件路径越界"})
                fpath = os.path.normpath(str(candidate))
            elif rel and rel.startswith("data/figures/_standalone/"):
                base = imagegen_bridge.STANDALONE_FIGURES_ROOT.resolve()
                candidate = (Path(_engine_root()) / rel).resolve()
                if base not in candidate.parents:
                    return self._send_json(403, {"error": "独立素材文件路径越界"})
                fpath = os.path.normpath(str(candidate))
            else:
                if not fpath:
                    return self._send_json(400, {"error": "缺少 path 参数"})
                fpath = os.path.normpath(fpath)
                base_charts = os.path.normpath(os.path.join(str(_engine_root()), "data", "charts"))
                base_tables = os.path.normpath(os.path.join(str(_engine_root()), "data", "tables"))
                base_figures = os.path.normpath(os.path.join(str(_engine_root()), "data", "figures"))
                if not (fpath.startswith(base_charts) or fpath.startswith(base_tables) or fpath.startswith(base_figures)):
                    return self._send_json(403, {"error": "路径越界"})
            if not os.path.isfile(fpath):
                return self._send_json(404, {"error": "文件不存在"})
            ext = os.path.splitext(fpath)[1].lower()
            mime_map = {".pdf": "application/pdf", ".csv": "text/csv",
                        ".tex": "application/x-tex", ".md": "text/markdown",
                        ".png": "image/png", ".svg": "image/svg+xml",
                        ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
            mime = mime_map.get(ext, "application/octet-stream")
            with open(fpath, "rb") as f:
                raw = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(fpath)}"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/tree":
            proj = qs.get("proj", [""])[0]
            return self._send_json(200, project_tree(proj))
        if path == "/api/skills":
            return self._send_json(200, {"skills": list_skills()})
        if path == "/api/agent/status":
            return self._send_json(200, dsh_bridge.host_info() if dsh_bridge.is_available() else {"available": False})
        if path == "/api/ai/settings":
            return self._send_json(200, ai_client.get_ai_settings())
        if path == "/api/charts":
            proj = qs.get("proj", [""])[0]
            charts_dir = Path(proj) / "data" / "charts"
            out = []
            if charts_dir.exists():
                for f in sorted(charts_dir.iterdir()):
                    if not f.is_file() or f.suffix.lower() not in IMAGE_SUFFIX:
                        continue
                    try:
                        raw = f.read_bytes()
                        suffix = f.suffix.lower().lstrip(".")
                        mime = "image/svg+xml" if suffix == "svg" else f"image/{suffix}"
                        out.append({
                            "name": f.name,
                            "rel": f"data/charts/{f.name}",
                            "data_url": "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii"),
                        })
                    except Exception:
                        continue
            return self._send_json(200, {"charts": out})
        if path == "/api/preview":
            proj = qs.get("proj", [""])[0]
            rel = qs.get("file", ["manuscript/main.md"])[0]
            root = Path(proj).resolve()
            md = (root / rel).resolve()
            if root != md and root not in md.parents:
                return self._send_json(400, {"error": "file 必须在项目目录内"})
            if not md.exists() or not md.is_file():
                return self._send_json(404, {"error": f"缺少 {rel}"})
            text = md.read_text(encoding="utf-8", errors="replace")
            text = md_with_embedded_images(md, text)
            try:
                import pypandoc
                html = pypandoc.convert_text(text, "html", format="markdown", extra_args=["--mathjax"])
            except Exception:
                html = "<pre>" + html_lib.escape(text) + "</pre>"
            return self._send_json(200, {"html": html, "file": rel})
        if path == "/api/intake":
            proj = qs.get("proj", [""])[0]
            intake = Path(proj) / "intake.md"
            text = intake.read_text(encoding="utf-8", errors="replace") if intake.exists() else ""
            return self._send_json(200, {"text": text})
        if path == "/api/tools/sci_figure/settings":
            return self._send_json(200, imagegen_bridge.get_image_settings())
        if path == "/api/tools/sci_figure/versions":
            proj = qs.get("project", [""])[0].strip()
            asset_id = qs.get("asset_id", [""])[0].strip()
            if not asset_id:
                return self._send_json(400, {"error": "asset_id 不能为空"})
            try:
                return self._send_json(200, imagegen_bridge.list_asset_versions(proj, asset_id) if proj else imagegen_bridge.list_standalone_versions(asset_id))
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if path == "/api/tools/sci_figure/figureforge/load":
            proj = qs.get("project", [""])[0].strip(); aid = qs.get("asset_id", [""])[0].strip(); version = qs.get("version", [""])[0].strip()
            if not aid or not version:
                return self._send_json(400, {"error": "asset_id 与 version 不能为空"})
            try:
                return self._send_json(200, imagegen_bridge.load_figureforge_version(proj or None, aid, version))
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if path == "/api/project/figures":
            proj = qs.get("project", [""])[0].strip()
            if not proj:
                return self._send_json(400, {"error": "缺少 project 参数"})
            if not Path(proj).exists():
                return self._send_json(404, {"error": "项目不存在"})
            code, payload = project_figures_data(proj)
            return self._send_json(code, payload)
        if path == "/api/project/pipeline":
            proj = qs.get("project", [""])[0].strip()
            if not proj:
                return self._send_json(400, {"error": "缺少 project 参数"})
            code, payload = project_pipeline_data(proj)
            return self._send_json(code, payload)
        if path == "/api/project/history":
            proj = qs.get("project", [""])[0].strip()
            if not proj:
                return self._send_json(400, {"error": "缺少 project 参数"})
            code, payload = project_history_data(proj)
            return self._send_json(code, payload)
        if path == "/api/images/settings":
            # 只读状态查询(掩码回显); 写入走既有 POST /api/tools/sci_figure/settings
            code, payload = images_settings_status()
            return self._send_json(code, payload)
        return self._send_json(404, {"error": "not found"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            body = {}
        if url.path == "/api/init":
            topic = (body.get("topic") or "").strip()
            if not topic:
                return self._send_json(400, {"error": "研究方向不能为空"})
            args = ["init", topic]
            if body.get("journal"):
                args += ["--journal", body["journal"].strip()]
            ptype = (body.get("type") or "article").strip()
            if ptype in ("article", "letter", "review", "thesis"):
                args += ["--type", ptype]
            base_dir = (body.get("dir") or "").strip() or str(PAPERS)
            args += ["--dir", base_dir]
            text = run_wb(args, timeout=180)
            return self._send_json(200, {"output": text})
        if url.path == "/api/cmd":
            action = (body.get("action") or "").strip()
            if not action:
                return self._send_json(400, {"error": "action 不能为空"})
            if action not in ALLOWED_ACTIONS:
                return self._send_json(400, {"error": f"不支持的 action: {action}"})
            args = [action]
            if body.get("topic"):
                args.append(str(body["topic"]))
            if body.get("journal"):
                args += ["--journal", str(body["journal"])]
            if body.get("dir"):
                args += ["--dir", str(body["dir"])]
            if body.get("stage"):
                args.append(str(body["stage"]))
            if body.get("n") is not None:
                args.append(str(body["n"]))
            if body.get("force") and action in ("stage", "check"):
                reason = str(body.get("reason") or "").strip()
                if not reason or reason == "未填原因":
                    return self._send_json(400, {"error": "强制操作必须提供 reason（说明为何跳过门禁）"})
                args.append("--force")
                args += ["--reason", reason]
            if body.get("uncheck") and action == "check":
                args.append("--uncheck")
            text = run_wb(args, timeout=180)
            return self._send_json(200, {"output": text})
        if url.path == "/api/skills/toggle":
            name = (body.get("name") or "").strip()
            if not name:
                return self._send_json(400, {"error": "name 不能为空"})
            enabled = bool(body.get("enabled", True))
            return self._send_json(200, set_skill_enabled(name, enabled))
        if url.path == "/api/ai/settings":
            try:
                res = ai_client.save_ai_settings(
                    base_url=body.get("base_url"),
                    api_key=body.get("api_key"),
                    model=body.get("model"),
                    temperature=body.get("temperature"),
                )
                return self._send_json(200, res)
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/ai/chat":
            messages = body.get("messages")
            if not messages:
                prompt = (body.get("prompt") or "").strip()
                if not prompt:
                    return self._send_json(400, {"error": "messages 或 prompt 不能为空"})
                messages = [{"role": "user", "content": prompt}]
            use_tools = bool(body.get("tools"))
            try:
                if use_tools:
                    reply, trace = ai_client.chat_with_tools(messages, temperature=body.get("temperature"))
                    return self._send_json(200, {"reply": reply, "trace": trace})
                reply = ai_client.chat(messages, temperature=body.get("temperature"))
                return self._send_json(200, {"reply": reply})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/ai/generate":
            proj = (body.get("project") or "").strip()
            stage = (body.get("stage") or "").strip()
            if not proj or stage not in STAGE_LABEL:
                return self._send_json(400, {"error": "project 和 stage 必填"})
            try:
                detail = load_state_detail(proj)
                if detail is None:
                    return self._send_json(404, {"error": "项目不存在"})
                items = detail["stages"][stage]["items"]
                artifacts = {
                    "research": ["research/literature.md", "research/search-log.md"],
                    "journal": ["journal/chosen.md", "journal/shortlist.md"],
                    "framework": ["framework/outline.md", "framework/data-requirements.md", "framework/references.md", "framework/figures.md"],
                    "draft": ["manuscript/main.md"],
                    "review": ["review/final-report.md"],
                    "submit": ["submit/cover-letter.md", "submit/checklist.md"],
                }.get(stage, [])
                missing = [a for a in artifacts if not (Path(proj) / a).exists()]
                if not missing:
                    return self._send_json(200, {"message": "该阶段产物已齐全,无需生成", "files": []})
                generated = []
                for target_rel in missing[:4]:
                    existing = ""
                    tp = Path(proj) / target_rel
                    if tp.exists():
                        try:
                            existing = tp.read_text(encoding="utf-8", errors="replace")[:6000]
                        except Exception:
                            existing = ""
                    content = ai_client.generate_artifact(
                        proj, stage,
                        detail.get("topic", ""),
                        detail.get("journal", ""),
                        items, [target_rel], existing,
                    )
                    tp.parent.mkdir(parents=True, exist_ok=True)
                    tp.write_text(content, encoding="utf-8")
                    generated.append({"file": target_rel, "content": content[:2000]})
                # 生成后自动核验(占位符/章节/BibTeX 条数),回显给用户
                auto_checks = []
                for f in generated:
                    rel = f["file"]
                    tp = Path(proj) / rel
                    content = tp.read_text(encoding="utf-8", errors="replace") if tp.exists() else ""
                    checks = []
                    for marker in ("[TBD]", "[DATA REQUIRED]"):
                        if marker in content:
                            checks.append(f"含占位符 {marker}")
                    if "___" in content:
                        checks.append("含 ___ 占位符")
                    if rel.endswith("references.md"):
                        m = re.search(r"```bibtex\s*(.*?)```", content, re.S)
                        bib = m.group(1) if m else content
                        try:
                            parsed = toolbox.parse_bibtex(bib)
                            if parsed and "error" not in parsed[0]:
                                checks.append(f"BibTeX {len(parsed)} 条")
                            else:
                                checks.append("BibTeX 解析失败")
                        except Exception:
                            checks.append("BibTeX 解析失败")
                    if rel.endswith("main.md"):
                        for sec in ("Introduction", "Methods", "Results", "Discussion", "Data Availability", "References"):
                            if sec not in content:
                                checks.append(f"缺章节 {sec}")
                    auto_checks.append({"file": rel, "checks": checks if checks else ["OK"]})
                return self._send_json(200, {"files": generated, "generated": len(generated), "auto_checks": auto_checks})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/data/columns":
            filename = (body.get("filename") or "data.csv").strip()
            content = body.get("content") or ""
            if not content:
                return self._send_json(400, {"error": "数据内容不能为空"})
            try:
                columns, rows = charts.parse_data(filename, content)
                return self._send_json(200, {"columns": columns, "sample": rows[:5]})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/search":
            query = (body.get("query") or "").strip()
            if not query:
                return self._send_json(400, {"error": "query 不能为空"})
            raw_sources = body.get("sources")
            if isinstance(raw_sources, list):
                sources = [str(s).strip() for s in raw_sources if str(s).strip()]
            else:
                sources = [s.strip() for s in str(raw_sources or "openalex,arxiv,crossref").split(",") if s.strip()]
            limit = int(body.get("limit") or 10)
            try:
                return self._send_json(200, {"results": toolbox.search_literature(query, sources, limit)})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/build_refs":
            query = (body.get("query") or "").strip()
            if not query:
                return self._send_json(400, {"error": "query 不能为空"})
            project = (body.get("project") or "").strip()
            raw_sources = body.get("sources")
            if isinstance(raw_sources, list):
                sources = [str(s).strip() for s in raw_sources if str(s).strip()]
            else:
                sources = [s.strip() for s in str(raw_sources or "openalex,arxiv,crossref,pubmed").split(",") if s.strip()]
            limit = int(body.get("limit") or 30)
            min_total = int(body.get("min") or 80)
            existing = ""
            out = None
            if project:
                ref_file = Path(project) / "framework" / "references.md"
                if ref_file.exists():
                    existing = ref_file.read_text(encoding="utf-8", errors="replace")
                out = str(ref_file)
            try:
                res = toolbox.build_refs(query, sources, limit, min_total, out_file=out, existing_bib=existing)
                return self._send_json(200, res)
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/fetch":
            doi = (body.get("doi") or "").strip()
            if not doi:
                return self._send_json(400, {"error": "doi 不能为空"})
            return self._send_json(200, toolbox.fetch_doi(doi))
        if url.path == "/api/tools/verify_bib":
            bibtex = body.get("bibtex") or ""
            if not bibtex.strip():
                return self._send_json(400, {"error": "bibtex 不能为空"})
            try:
                return self._send_json(200, {"results": toolbox.verify_bibtex(bibtex)})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/stats":
            content = body.get("content") or ""
            if not content.strip():
                return self._send_json(400, {"error": "content 不能为空"})
            try:
                return self._send_json(200, toolbox.stats_csv(content))
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/data2paper/fill":
            proj = (body.get("project") or "").strip()
            if not proj:
                return self._send_json(400, {"error": "project 不能为空"})
            try:
                text = data2paper.fill_project(proj)
                return self._send_json(200, {"output": text})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/export":
            proj = (body.get("project") or "").strip()
            fmt = (body.get("format") or "docx").strip()
            if not proj:
                return self._send_json(400, {"error": "project 不能为空"})
            if fmt not in ("latex", "docx", "pdf", "html"):
                return self._send_json(400, {"error": "format 不支持"})
            md = Path(proj) / "manuscript" / "main.md"
            if not md.exists():
                return self._send_json(404, {"error": "缺少 manuscript/main.md"})
            # 入口清理：删除超过 1 小时的消毒副本残留（崩溃/断电遗留），删除失败静默跳过
            try:
                _now = time.time()
                for _stale in md.parent.glob("_export_sanitized_*.md"):
                    try:
                        if _now - _stale.stat().st_mtime > 3600:
                            _stale.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
            out_dir = Path(proj) / "export"
            out_dir.mkdir(parents=True, exist_ok=True)
            suffix = ".tex" if fmt == "latex" else f".{fmt}"
            out = out_dir / f"manuscript{suffix}"
            # 统一前置：导出前文本消毒（四种格式一致；mechanical_fix 由 toolbox 提供，缺失/异常时回退原 md）
            export_src = str(md)
            tmp_md = None
            sanitized = False
            mechanical_fix = getattr(toolbox, "mechanical_fix", None)
            if callable(mechanical_fix):
                try:
                    raw = md.read_text(encoding="utf-8")
                    fixed = mechanical_fix(raw)
                    if isinstance(fixed, str) and fixed != raw:
                        # 副本与 main.md 同目录：保证相对图片以项目目录为基点解析
                        # 唯一文件名：ThreadingHTTPServer 并发导出互不覆盖
                        tmp_md = md.parent / f"_export_sanitized_{uuid.uuid4().hex[:8]}.md"
                        tmp_md.write_text(fixed, encoding="utf-8")
                        export_src = str(tmp_md)
                        sanitized = True
                except Exception:
                    # 消毒失败不影响导出，回退用原 md
                    export_src = str(md)
                    tmp_md = None
                    sanitized = False
            try:
                if fmt == "pdf":
                    try:
                        import pdf_export
                        pdf_export.generate_pdf(export_src, str(out), proj)
                        return self._send_json(200, {"file": str(out), "sanitized": sanitized})
                    except Exception as e:
                        return self._send_json(400, {"error": f"PDF生成失败: {e}"})
                export_kwargs = {}
                if fmt == "docx":
                    # D1: docx 导出套用参考模板（TNR/行距/标题层级/图注样式）
                    tpl = _engine_root() / "templates" / "reference.docx"
                    if tpl.exists():
                        export_kwargs["template"] = str(tpl)
                res = toolbox.export_markdown(export_src, fmt, str(out), **export_kwargs)
                return self._send_json(200, {"file": res, "sanitized": sanitized})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
            finally:
                if tmp_md is not None:
                    try:
                        tmp_md.unlink()
                    except Exception as _e:
                        print(f"⚠ 消毒副本删除失败 {tmp_md}: {_e}")
        if url.path == "/api/intake":
            proj = (body.get("project") or "").strip()
            content = body.get("content") or ""
            if not proj:
                return self._send_json(400, {"error": "project 不能为空"})
            try:
                intake = Path(proj) / "intake.md"
                intake.parent.mkdir(parents=True, exist_ok=True)
                intake.write_text(content, encoding="utf-8")
                return self._send_json(200, {"ok": True, "file": str(intake)})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/format_refs":
            bibtex = body.get("bibtex") or ""
            style = body.get("style") or "springer-numeric"
            if not bibtex.strip():
                return self._send_json(400, {"error": "bibtex 不能为空"})
            try:
                refs = toolbox.format_references_text(bibtex, style)
                return self._send_json(200, {"references": refs})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/audit_stats":
            content = body.get("content") or ""
            if not content.strip():
                return self._send_json(400, {"error": "content 不能为空"})
            return self._send_json(200, {"issues": toolbox.audit_stats(content)})
        if url.path == "/api/tools/originality":
            content = body.get("content") or ""
            corpus = body.get("corpus") or ""
            if not content.strip():
                return self._send_json(400, {"error": "content 不能为空"})
            try:
                res = toolbox.originality_check(content, corpus or None)
                return self._send_json(200, {"results": res})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/quality_check":
            proj = (body.get("project") or "").strip()
            if not proj:
                return self._send_json(400, {"error": "project 不能为空"})
            try:
                res = toolbox.quality_check(proj, record=True)  # 显式质检: 落盘收敛历史
                score = toolbox.quality_score(res)
                try:
                    hf = Path(proj) / "review" / "quality-history.json"
                    if hf.exists():
                        score["history"] = json.loads(hf.read_text(encoding="utf-8"))
                except Exception:
                    pass
                return self._send_json(200, {"issues": res, "score": score})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/qfix":
            proj = (body.get("project") or "").strip()
            itype = (body.get("issue_type") or "").strip()
            if not proj or not itype:
                return self._send_json(400, {"error": "project 与 issue_type 不能为空"})
            try:
                return self._send_json(200, toolbox.qfix(proj, itype))
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/import_pdf":
            pdf_path = (body.get("pdf") or "").strip()
            proj = (body.get("project") or "").strip()
            if not pdf_path:
                return self._send_json(400, {"error": "pdf 路径不能为空"})
            if not Path(pdf_path).exists():
                return self._send_json(400, {"error": f"pdf 文件不存在: {pdf_path}"})
            try:
                target_dir = Path(proj) / "manuscript" if proj else Path(pdf_path).parent
                target_dir.mkdir(parents=True, exist_ok=True)
                out = str(target_dir / "imported_pdf.md")
                f, stats = toolbox.import_pdf(pdf_path, out, body.get("max_pages") or None)
                return self._send_json(200, {"file": f, **stats})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/import_docx":
            docx_path = (body.get("docx") or "").strip()
            proj = (body.get("project") or "").strip()
            if not docx_path:
                return self._send_json(400, {"error": "docx 路径不能为空"})
            if not Path(docx_path).exists():
                return self._send_json(400, {"error": f"docx 文件不存在: {docx_path}"})
            try:
                target_dir = Path(proj) / "manuscript" if proj else Path(docx_path).parent
                target_dir.mkdir(parents=True, exist_ok=True)
                out = str(target_dir / "imported_docx.md")
                images = str(target_dir / "imported_docx_images")
                f, stats = toolbox.import_docx(docx_path, out, images)
                return self._send_json(200, {"file": f, **stats})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/data/analyze":
            proj = (body.get("project") or "").strip()
            filename = (body.get("filename") or "data.csv").strip()
            content = body.get("content") or ""
            chart_type = (body.get("chart_type") or "bar").strip()
            x_col = body.get("x") or None
            y_col = body.get("y") or None
            title = (body.get("title") or "").strip()
            if not content:
                return self._send_json(400, {"error": "数据内容不能为空"})
            out_dir = None
            if proj:
                out_dir = Path(proj) / "data" / "charts"
            try:
                res = charts.generate_chart(filename, content, chart_type, x_col, y_col, title, out_dir)
                return self._send_json(200, res)
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/table/generate":
            proj = (body.get("project") or "").strip()
            filename = (body.get("filename") or "data.csv").strip()
            content = body.get("content") or ""
            table_type = (body.get("table_type") or "desc").strip()
            group_col = body.get("group_col") or None
            metric_cols = body.get("metric_cols") or None
            title = (body.get("title") or "").strip()
            if not content:
                return self._send_json(400, {"error": "数据内容不能为空"})
            out_dir = None
            if proj:
                out_dir = Path(proj) / "data" / "tables"
            try:
                res = tables.generate_table(filename, content, table_type,
                                            group_col, metric_cols, title, out_dir)
                return self._send_json(200, res)
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/manuscript/insert":
            proj = (body.get("project") or "").strip()
            if not proj or not Path(proj).exists():
                return self._send_json(400, {"error": "项目路径无效"})
            kind = (body.get("kind") or "chart").strip()
            rel_path = (body.get("path") or "").strip()
            caption = (body.get("caption") or "").strip()
            section = (body.get("section") or "").strip()
            md_content = body.get("markdown") or ""
            main_md = Path(proj) / "manuscript" / "main.md"
            if not main_md.exists():
                return self._send_json(400, {"error": "manuscript/main.md 不存在"})
            text = main_md.read_text(encoding="utf-8", errors="replace")
            if kind == "chart":
                if not rel_path:
                    return self._send_json(400, {"error": "缺少图表路径"})
                img_md = f'\n\n![{caption}]({rel_path})\n'
                if caption:
                    img_md += f'\n*{caption}*\n'
                insert_block = f'\n<!-- INSERT-FIG -->{img_md}<!-- /INSERT-FIG -->\n'
            elif kind == "table":
                if not md_content:
                    return self._send_json(400, {"error": "缺少表格内容"})
                insert_block = f'\n<!-- INSERT-TAB -->\n{md_content}\n<!-- /INSERT-TAB -->\n'
            else:
                return self._send_json(400, {"error": "kind 必须是 chart 或 table"})
            import re as _re
            inserted = False
            if section:
                pat = _re.compile(r'^(#{1,4}\s+.*' + _re.escape(section) + r'.*)$', _re.M | _re.I)
                m = pat.search(text)
                if m:
                    insert_pos = m.end()
                    text = text[:insert_pos] + insert_block + text[insert_pos:]
                    inserted = True
            if not inserted:
                tbd_pat = _re.compile(r'\[TBD:[^\]]*\]')
                m2 = tbd_pat.search(text)
                if m2:
                    text = text[:m2.start()] + insert_block.strip() + text[m2.end():]
                    inserted = True
            if not inserted:
                text = text.rstrip() + '\n' + insert_block
                inserted = True
            main_md.write_text(text, encoding="utf-8")
            return self._send_json(200, {"ok": True, "inserted": True, "kind": kind})
        if url.path == "/api/tools/sci_figure/settings":
            # 保存图片生成 API 配置
            try:
                img = imagegen_bridge.save_image_settings(
                    base_url=body.get("base_url"),
                    api_key=body.get("api_key"),
                    model=body.get("model"),
                    size=body.get("size"),
                    quality=body.get("quality"),
                )
                return self._send_json(200, imagegen_bridge.get_image_settings())
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/sci_figure":
            # 科研绘图: 文生图或整图参考编辑。新 asset_id 路线保存不可变版本；
            # 不带 asset_id 的旧客户端继续使用 legacy ai_figure_* 落盘。
            prompt = (body.get("prompt") or "").strip()
            proj = (body.get("project") or "").strip()
            asset_id = (body.get("asset_id") or body.get("name") or "").strip()
            mode = (body.get("mode") or "generate").strip().lower()
            name = (body.get("name") or "").strip() or None
            size = (body.get("size") or "").strip() or None
            quality = (body.get("quality") or "").strip() or None
            if not prompt:
                return self._send_json(400, {"error": "提示词不能为空"})
            if mode not in ("generate", "edit"):
                return self._send_json(400, {"error": "mode 必须是 generate 或 edit"})
            reference_path = None
            if mode == "edit":
                if not asset_id:
                    return self._send_json(400, {"error": "编辑模式需要素材名称 / ID"})
                try:
                    resolver = imagegen_bridge.resolve_asset_image if proj else imagegen_bridge.resolve_standalone_image
                    reference_path = resolver(proj, asset_id, version=(body.get("source_version") or "").strip(), reference_id=(body.get("reference_id") or "").strip()) if proj else resolver(asset_id, version=(body.get("source_version") or "").strip(), reference_id=(body.get("reference_id") or "").strip())
                except Exception as e:
                    return self._send_json(400, {"error": str(e)})
            res = imagegen_bridge.generate_image(prompt, size=size, quality=quality,
                                                 reference_path=str(reference_path) if reference_path else None)
            if not res.get("ok"):
                return self._send_json(400, res)
            if proj and Path(proj).exists() and asset_id:
                try:
                    saved = imagegen_bridge.save_version(proj, asset_id, res["image_b64"], res.get("format", "png"))
                    res.update(saved)
                    res["mode"] = mode
                except Exception as e:
                    return self._send_json(400, {"error": str(e)})
            elif not proj:
                try:
                    saved = imagegen_bridge.save_standalone_version(asset_id or "scientific-figure", res["image_b64"], res.get("format", "png"))
                    res.update(saved); res["mode"] = mode
                except Exception as e:
                    return self._send_json(400, {"error": str(e)})
            elif proj and Path(proj).exists():
                try:
                    rel = imagegen_bridge.save_figure(proj, res["image_b64"], res.get("format", "png"), name)
                    res["file"] = rel
                except Exception as e:
                    res["save_error"] = str(e)
            return self._send_json(200, res)
        if url.path == "/api/tools/sci_figure/reference":
            proj = (body.get("project") or "").strip()
            asset_id = (body.get("asset_id") or "").strip()
            filename = (body.get("filename") or "reference.png").strip()
            data_b64 = body.get("data") or ""
            if not asset_id:
                return self._send_json(400, {"error": "素材名称 / ID 不能为空"})
            if isinstance(data_b64, str):
                data_b64 = data_b64.strip()
                if data_b64.startswith("data:"):
                    m = re.match(r"^data:[^;]*;base64,(.*)$", data_b64, re.S)
                    data_b64 = m.group(1) if m else ""
            if not isinstance(data_b64, str) or len(data_b64) > 28 * 1024 * 1024:
                return self._send_json(413, {"error": "参考图数据超过 20MB 上限"})
            try:
                raw = base64.b64decode(data_b64, validate=True)
                saved = imagegen_bridge.save_reference(proj, asset_id, raw, filename) if proj else imagegen_bridge.save_standalone_reference(asset_id, raw, filename)
                return self._send_json(200, {"ok": True, **saved, "size": len(raw)})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/sci_figure/figureforge/save":
            proj = (body.get("project") or "").strip(); aid = (body.get("asset_id") or "").strip()
            image_b64 = body.get("image_b64") or ""; project_json = body.get("project_json")
            if isinstance(image_b64, str) and image_b64.startswith("data:"):
                m = re.match(r"^data:[^;]+;base64,(.*)$", image_b64, re.S); image_b64 = m.group(1) if m else ""
            if not aid or not image_b64 or project_json is None:
                return self._send_json(400, {"error": "asset_id、image_b64 与 project_json 不能为空"})
            try:
                saved = imagegen_bridge.save_figureforge_version(proj or None, aid, image_b64, project_json, (body.get("format") or "png"))
                return self._send_json(200, {"ok": True, **saved})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/sci_figure/figureforge/import":
            proj = (body.get("project") or "").strip(); aid = (body.get("asset_id") or body.get("name") or "").strip()
            image_b64 = body.get("image_b64") or ""; project_json = body.get("project_json")
            if not aid: aid = "figureforge-" + uuid.uuid4().hex[:8]
            if isinstance(image_b64, str) and image_b64.startswith("data:"):
                m = re.match(r"^data:[^;]+;base64,(.*)$", image_b64, re.S); image_b64 = m.group(1) if m else ""
            if not image_b64 or project_json is None:
                return self._send_json(400, {"error": "image_b64 与 project_json 不能为空"})
            try:
                saved = imagegen_bridge.save_figureforge_version(proj or None, aid, image_b64, project_json, "png")
                return self._send_json(200, {"ok": True, **saved})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/tools/sci_figure/current":
            proj = (body.get("project") or "").strip()
            asset_id = (body.get("asset_id") or "").strip()
            version = (body.get("version") or "").strip()
            if not asset_id or not version:
                return self._send_json(400, {"error": "素材名称 / ID 与 version 不能为空"})
            try:
                result = imagegen_bridge.set_current_version(proj, asset_id, version) if proj else imagegen_bridge.set_standalone_current(asset_id, version)
                return self._send_json(200, {"ok": True, **result})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/images/settings":
            # 写入已收敛到既有 /api/tools/sci_figure/settings(app_config.json image 节)
            return self._send_json(405, {"error": "写入请使用 POST /api/tools/sci_figure/settings"})
        if url.path == "/api/project/figures/generate":
            proj = (body.get("project") or "").strip()
            if not proj:
                return self._send_json(400, {"error": "project 不能为空"})
            if not Path(proj).exists():
                return self._send_json(404, {"error": "项目不存在"})
            mode = (body.get("mode") or "auto").strip() or "auto"
            if mode not in ("auto", "all"):
                return self._send_json(400, {"error": "mode 必须是 auto 或 all"})
            ids = None
            raw_ids = body.get("ids")
            if isinstance(raw_ids, list):
                ids = [str(i).strip() for i in raw_ids if str(i).strip()]
            elif raw_ids:
                ids = [s.strip() for s in str(raw_ids).split(",") if s.strip()]
            func = getattr(charts, "generate_all_figures", None)
            if not callable(func):
                return self._send_json(501, {"error": "图表路由核心层未就绪"})
            try:
                res = func(proj, mode=mode, ids=ids)
            except Exception as e:
                return self._send_json(400, {"error": f"图表生成失败: {e}"})
            if not isinstance(res, dict):
                res = {"results": res}
            return self._send_json(200, res)
        if url.path == "/api/project/figures/upload":
            proj = (body.get("project") or "").strip()
            fig_id = (body.get("fig_id") or "").strip()
            data_b64 = body.get("data") or ""
            if not proj or not fig_id:
                return self._send_json(400, {"error": "project 与 fig_id 不能为空"})
            if not Path(proj).exists():
                return self._send_json(404, {"error": "项目不存在"})
            if isinstance(data_b64, str):
                data_b64 = data_b64.strip()
                if data_b64.startswith("data:"):  # 容忍 data URL 前缀
                    m = re.match(r"^data:[^;]*;base64,(.*)$", data_b64, re.S)
                    data_b64 = m.group(1) if m else ""
            # 体积上限: base64 长度 ≤ 20MB, 超限拒绝(避免超大请求占满内存/磁盘)
            if not isinstance(data_b64, str) or len(data_b64) > 20 * 1024 * 1024:
                return self._send_json(413, {"error": "图片数据超过 20MB 上限"})
            try:
                raw = base64.b64decode(data_b64, validate=True)
            except Exception:
                return self._send_json(400, {"error": "base64 解码失败"})
            if not raw:
                return self._send_json(400, {"error": "图片数据为空"})
            stem = _fig_stem(fig_id)
            if not stem:
                return self._send_json(400, {"error": "fig_id 规范化后为空"})
            # Windows 保留设备名黑名单(con/aux/nul/prn/com1-9/lpt1-9)
            _reserved = {"con", "aux", "nul", "prn"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
            if stem.lower() in _reserved:
                return self._send_json(400, {"error": f"fig_id 命中 Windows 保留设备名: {stem}"})
            fname = (body.get("filename") or "").strip()
            fname = re.sub(r"[\\/:*?\"<>|]", "_", fname)
            if not fname or not fname.lower().endswith(IMAGE_SUFFIX):
                fname = f"{stem}_upload.png"
            fname_stem = Path(fname).stem.lower()
            if fname_stem in _reserved:
                return self._send_json(400, {"error": f"filename 命中 Windows 保留设备名: {fname_stem}"})
            charts_dir = Path(proj) / "data" / "charts"
            try:
                charts_dir.mkdir(parents=True, exist_ok=True)
                target = charts_dir / fname
                # 同名防覆盖: 已存在则追加递增序号(fig1_upload_2.png), 返回实际 rel
                if target.exists():
                    p_stem = Path(fname).stem
                    p_ext = Path(fname).suffix
                    n = 2
                    while True:
                        target = charts_dir / f"{p_stem}_{n}{p_ext}"
                        if not target.exists():
                            break
                        n += 1
                target.write_bytes(raw)
                rel = f"data/charts/{target.name}"
            except Exception as e:
                return self._send_json(400, {"error": f"保存失败: {e}"})
            return self._send_json(200, {"ok": True, "rel": rel, "size": len(raw)})
        if url.path == "/api/figure/render":
            try:
                sys.path.insert(0, str(_engine_root()))
                import figure_router
                r = figure_router.route(body)
                return self._send_json(200 if r.get("ok") else 400, r)
            except Exception as e:
                return self._send_json(400, {"ok": False, "error": str(e)})
        if url.path == "/api/gen/action":
            proj = (body.get("proj") or "").strip()
            action = (body.get("action") or "").strip()
            if not proj or not action:
                return self._send_json(400, {"error": "proj/action 不能为空"})
            try:
                sys.path.insert(0, str(_engine_root()))
                import staged_gen as sg
                if action == "init":
                    r = sg.gen_init(proj, force=bool(body.get("force")))
                elif action == "contract":
                    st = None
                    try:
                        stf = Path(proj) / "state.json"
                        if stf.exists():
                            st = json.loads(stf.read_text(encoding="utf-8"))
                    except Exception:
                        st = None
                    r = sg.gen_contract(proj, use_ai=bool(body.get("ai")), project_state=st)
                elif action == "section":
                    if not body.get("sid"):
                        return self._send_json(400, {"error": "section 需要 sid"})
                    r = sg.gen_section(proj, str(body["sid"]), dry_run=bool(body.get("dry_run")),
                                       accept=bool(body.get("accept")), retry=int(body.get("retry", 1)))
                    r.pop("prompt", None)
                elif action == "tables":
                    if body.get("extract"):
                        r = sg.tables_extract(proj, md_path=body.get("src"))
                    else:
                        r = sg.gen_tables(proj, tid=body.get("tid"), gen=bool(body.get("gen")))
                elif action == "abstract":
                    r = sg.gen_abstract(proj, dry_run=bool(body.get("dry_run")),
                                        retry=int(body.get("retry", 1)), max_words=int(body.get("max_words", 250)))
                    r.pop("prompt", None)
                elif action == "assemble":
                    r = sg.gen_assemble(proj)
                elif action == "delegate":
                    r = sg.gen_delegate(proj, timeout=int(body.get("timeout", 1800)))
                else:
                    return self._send_json(400, {"error": f"不支持的 action: {action}"})
                return self._send_json(200, r)
            except Exception as e:
                return self._send_json(400, {"error": str(e)})
        if url.path == "/api/agent/delegate":
            instruction = (body.get("instruction") or "").strip()
            if not instruction:
                return self._send_json(400, {"error": "instruction 不能为空"})
            if not dsh_bridge.is_available():
                return self._send_json(503, {"error": "DSH Agent 不可达,请确认 dsh 已启动 (端口 3080)"})
            cwd = (body.get("cwd") or "").strip() or None
            preset = (body.get("preset") or "").strip() or dsh_bridge.DEFAULT_PRESET
            timeout = int(body.get("timeout") or 300)
            try:
                result = dsh_bridge.delegate_task(instruction, cwd=cwd, preset=preset, timeout=timeout)
                return self._send_json(200, result)
            except Exception as e:
                return self._send_json(200, {"text": "", "error": str(e)})
        return self._send_json(404, {"error": "not found"})


def _port_serving(port):
    """探测端口是否已有服务在监听。
    Windows 上 HTTPServer 的 SO_REUSEADDR 允许第二个进程重复绑定同一端口，
    造成连接被随机分发到两个进程（请求劫持/页面交互异常）。
    故启动前先探测：已有服务则不再重复启动。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    if _port_serving(PORT):
        print(f"端口 {PORT} 已有工作台服务在运行，本进程退以避免双实例。")
        print(f"请直接使用: http://127.0.0.1:{PORT}")
        raise SystemExit(0)
    print(f"Paper Workbench Web  http://127.0.0.1:{PORT}")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.serve_forever()
