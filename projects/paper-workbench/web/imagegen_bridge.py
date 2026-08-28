#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研绘图桥接模块 — 通过 OpenAI 兼容的图片生成 API 生成科研插图。

配置(app_config.json 的 "image" 段):
  {
    "image": {
      "base_url": "https://api.openai.com/v1",   # 或可灵/通义/本地SD等兼容端点
      "api_key": "sk-xxx",
      "model": "gpt-image-1",                     # gpt-image-1 / dall-e-3 / 其他兼容
      "size": "1024x1024",                        # 1024x1024 / 1024x1792 / 1792x1024 ...
      "quality": "high",                          # standard / high (部分模型支持)
      "timeout": 120
    }
  }

支持两种返回: data[].b64_json(直接给图) 与 data[].url(需下载)。
环境变量 PAPER_WORKBENCH_IMAGE_KEY 可作 api_key 兜底。
"""
import base64
import hashlib
import json
import os
import re
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "app_config.json"
STANDALONE_FIGURES_ROOT = CONFIG_PATH.parent / "data" / "figures" / "_standalone"
ENV_IMAGE_KEY = "PAPER_WORKBENCH_IMAGE_KEY"

DEFAULT_IMAGE = {
    "enabled": True,
    "base_url": "",
    "api_key": "",
    "model": "",
    "size": "1024x1024",
    "quality": "high",
    "timeout": 120,
}

# Only models with a documented OpenAI-compatible image-edit endpoint are
# allowed through the edit path.  Unknown aliases fail explicitly instead of
# silently degrading to text-to-image (which would lose the reference image).
EDIT_CAPABLE_MODELS = ("gpt-image-2", "gpt-image-1", "dall-e-2")
MAX_REFERENCE_BYTES = 20 * 1024 * 1024

# 生图 API 重试上限（网络异常/5xx 才重试；4xx 配置类错误不重试）
MAX_RETRIES = 2

# AI 生图披露声明（回填图注时附在引用后，供 charts 混合路由使用）
DISCLOSURE = "Illustration generated with an AI image model."


class ImageGenUnavailable(Exception):
    """生图能力不可用：开关关闭或 API 未配置（调用方应走降级链）。"""


class ImageGenError(Exception):
    """生图调用失败：API 报错/响应无法解析/落盘失败等。"""


def build_image_prompt(key_visual):
    """关键视觉描述 + 学术插图风格约束，构造生图提示词。"""
    kv = (key_visual or "").strip().rstrip(".。")
    if not kv:
        raise ImageGenError("关键视觉为空，无法构造生图提示词")
    return (
        f"{kv}. "
        "White background, clean vector-style scientific illustration, "
        "no text/watermark, colorblind-safe palette."
    )


def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            img = dict(DEFAULT_IMAGE)
            img.update(cfg.get("image", {}) or {})
            return cfg, img
        except Exception:
            pass
    return {}, dict(DEFAULT_IMAGE)


def save_image_settings(base_url=None, api_key=None, model=None, size=None, quality=None):
    cfg, img = load_config()
    if base_url is not None:
        img["base_url"] = str(base_url).strip().rstrip("/")
    if api_key:  # 留空表示不修改
        img["api_key"] = str(api_key).strip()
    if model is not None:
        img["model"] = str(model).strip()
    if size is not None:
        img["size"] = str(size).strip()
    if quality is not None:
        img["quality"] = str(quality).strip()
    cfg["image"] = img
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return img


def get_image_settings():
    _, img = load_config()
    api_key_set = bool(img.get("api_key")) or bool(os.environ.get(ENV_IMAGE_KEY, "").strip())
    return {
        "base_url": img.get("base_url", ""),
        "model": img.get("model", ""),
        "size": img.get("size", "1024x1024"),
        "quality": img.get("quality", "high"),
        "api_key_set": api_key_set,
        "edit_endpoint": "/images/edits",
        "edit_supported": is_edit_model_supported(img.get("model", "")),
    }


def is_edit_model_supported(model):
    """Return whether *model* is explicitly known to support image edits."""
    name = str(model or "").strip().lower()
    return any(name == m or name.startswith(m + "-") for m in EDIT_CAPABLE_MODELS)


def generate_image(prompt, size=None, quality=None, reference_path=None, timeout=None):
    """文生图；提供 reference_path 时严格走 /images/edits。

    返回 {"ok": True, "image_b64": "...", "format": "png", "model": "...", "time": ...}
    或 {"ok": False, "error": "..."}
    """
    if reference_path:
        return edit_image(prompt, reference_path, size=size, quality=quality, timeout=timeout)
    _, img = load_config()
    base_url = (img.get("base_url") or "").strip().rstrip("/")
    api_key = (img.get("api_key") or "").strip()
    if not api_key:
        api_key = os.environ.get(ENV_IMAGE_KEY, "").strip()
    model = (img.get("model") or "").strip()
    if not base_url or not api_key or not model:
        return {"ok": False, "error": "未配置图片生成 API(base_url/api_key/model)，请在「科研绘图」设置中填写"}
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "提示词不能为空"}
    timeout = timeout or int(img.get("timeout", 120))
    size = size or img.get("size", "1024x1024")
    quality = quality or img.get("quality", "high")

    # 构造请求体: 兼容 OpenAI /images/generations 格式
    payload = {
        "model": model,
        "prompt": prompt.strip(),
        "size": size,
        "n": 1,
        "response_format": "b64_json",
    }
    if quality:
        payload["quality"] = quality

    url = base_url + "/images/generations"
    result = None
    for attempt in range(MAX_RETRIES + 1):
        result = _request_image_once(url, api_key, payload, timeout)
        if result.get("ok"):
            return result
        code = result.get("http_code")
        transient = bool(result.get("transient")) or (code is not None and code >= 500)
        if not transient or attempt >= MAX_RETRIES:
            break
        time.sleep(1.0 * (attempt + 1))  # 递增退避后重试
    return result


def _multipart_body(fields, file_field, filename, raw, mime):
    """Build a small deterministic multipart/form-data payload without deps."""
    boundary = "----PaperWorkbenchBoundary" + uuid.uuid4().hex
    chunks = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"), b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(), raw, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return boundary, b"".join(chunks)


def _reference_info(path):
    """Validate and read a reference image, returning bytes/filename/mime."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise ImageGenError("参考图不存在")
    try:
        raw = p.read_bytes()
    except Exception as e:
        raise ImageGenError(f"读取参考图失败: {e}") from e
    if not raw:
        raise ImageGenError("参考图为空")
    if len(raw) > MAX_REFERENCE_BYTES:
        raise ImageGenError("参考图超过 20MB 上限")
    ext = p.suffix.lower().lstrip(".")
    magic_ok = (
        (ext == "png" and raw.startswith(_IMAGE_MAGIC["png"][0]))
        or (ext in ("jpg", "jpeg") and raw.startswith(_IMAGE_MAGIC["jpg"][0]))
        or (ext == "webp" and len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
    )
    if not magic_ok:
        raise ImageGenError("参考图格式不支持或文件内容与扩展名不匹配（支持 PNG/JPG/WEBP）")
    if ext == "jpeg":
        ext = "jpg"
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[ext]
    return raw, f"reference.{ext}", mime


def edit_image(prompt, reference_path, size=None, quality=None, timeout=None):
    """Edit an existing image through the OpenAI-compatible /images/edits API."""
    _, img = load_config()
    base_url = (img.get("base_url") or "").strip().rstrip("/")
    api_key = (img.get("api_key") or "").strip() or os.environ.get(ENV_IMAGE_KEY, "").strip()
    model = (img.get("model") or "").strip()
    if not base_url or not api_key or not model:
        return {"ok": False, "error": "未配置图片生成 API(base_url/api_key/model)，请在「科研绘图」设置中填写"}
    if not is_edit_model_supported(model):
        return {"ok": False, "error": f"当前模型不支持已确认的图像编辑接口 /images/edits: {model or '(未指定)'}"}
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "编辑提示词不能为空"}
    try:
        raw, filename, mime = _reference_info(reference_path)
    except ImageGenError as e:
        return {"ok": False, "error": str(e)}
    timeout = timeout or int(img.get("timeout", 120))
    fields = {
        "model": model,
        "prompt": prompt.strip(),
        "n": 1,
        "response_format": "b64_json",
    }
    size = size or img.get("size", "1024x1024")
    quality = quality or img.get("quality", "high")
    if size:
        fields["size"] = size
    if quality:
        fields["quality"] = quality
    url = base_url + "/images/edits"
    result = None
    for attempt in range(MAX_RETRIES + 1):
        result = _request_image_edit_once(url, api_key, fields, filename, raw, mime, timeout)
        if result.get("ok"):
            return result
        code = result.get("http_code")
        transient = bool(result.get("transient")) or (code is not None and code >= 500)
        if not transient or attempt >= MAX_RETRIES:
            break
        time.sleep(1.0 * (attempt + 1))
    return result


def _request_image_edit_once(url, api_key, fields, filename, raw, mime, timeout):
    boundary, body = _multipart_body(fields, "image", filename, raw, mime)
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": "Bearer " + api_key},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        return {"ok": False, "error": f"图像编辑 API HTTP {e.code}: {body_text}", "http_code": e.code}
    except Exception as e:
        return {"ok": False, "error": f"图像编辑 API 调用失败: {e}", "transient": True}
    return _parse_image_response(data, fields.get("model", ""), t0)


def _parse_image_response(data, model, t0):
    """Parse the common b64_json/url image response used by both endpoints."""
    items = data.get("data") or [] if isinstance(data, dict) else []
    if not items:
        err = data.get("error") or data.get("message") or "空响应" if isinstance(data, dict) else "空响应"
        return {"ok": False, "error": f"图片 API 返回异常: {err}"}
    item = items[0] or {}
    b64 = item.get("b64_json")
    if b64:
        return {"ok": True, "image_b64": b64, "format": "png", "model": model,
                "time": round(time.time() - t0, 1)}
    img_url = item.get("url")
    if img_url:
        try:
            with urllib.request.urlopen(img_url, timeout=120) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type") or ""
            ext = "png" if "png" in ctype else ("webp" if "webp" in ctype else "jpg")
            return {"ok": True, "image_b64": base64.b64encode(raw).decode("ascii"), "format": ext,
                    "model": model, "time": round(time.time() - t0, 1)}
        except Exception as e:
            return {"ok": False, "error": f"下载生成图片失败: {e}", "transient": True}
    return {"ok": False, "error": "图片 API 返回中没有 b64_json 或 url 字段"}


def _request_image_once(url, api_key, payload, timeout):
    """单次请求 /images/generations 并解析 b64_json/url，返回与 generate_image 同构 dict。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "error": f"图片 API HTTP {e.code}: {body}", "http_code": e.code}
    except Exception as e:
        return {"ok": False, "error": f"图片 API 调用失败: {e}", "transient": True}

    # 解析返回: data[].b64_json 或 data[].url
    items = data.get("data") or []
    if not items:
        err = data.get("error") or data.get("message") or "空响应"
        return {"ok": False, "error": f"图片 API 返回异常: {err}"}
    item = items[0]
    b64 = item.get("b64_json")
    if b64:
        return {"ok": True, "image_b64": b64, "format": "png", "model": payload.get("model", ""), "time": round(time.time() - t0, 1)}
    img_url = item.get("url")
    if img_url:
        try:
            with urllib.request.urlopen(img_url, timeout=timeout) as r:
                raw = r.read()
            ext = "png" if "png" in (r.headers.get("Content-Type") or "") else "jpg"
            return {"ok": True, "image_b64": base64.b64encode(raw).decode("ascii"),
                    "format": ext, "model": payload.get("model", ""), "time": round(time.time() - t0, 1)}
        except Exception as e:
            return {"ok": False, "error": f"下载生成图片失败: {e}", "transient": True}
    return {"ok": False, "error": "图片 API 返回中没有 b64_json 或 url 字段"}


def save_figure(project_dir, image_b64, fmt, name=None):
    """把生成图保存到项目 data/figures/, 返回相对路径。"""
    out_dir = Path(project_dir) / "data" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "png" if fmt == "png" else "jpg"
    fname = name or f"ai_figure_{int(time.time())}.{ext}"
    if not fname.endswith("." + ext):
        fname += "." + ext
    target = out_dir / fname
    target.write_bytes(base64.b64decode(image_b64))
    return str(target.relative_to(Path(project_dir))) if target.is_relative_to(Path(project_dir)) else str(target)


def is_image_gen_configured():
    """判断生图是否可用：开关开启且 base_url/api_key/model 齐备（key 可用环境变量兜底）。"""
    _, img = load_config()
    if not img.get("enabled", True):
        return False
    api_key = (img.get("api_key") or "").strip() or os.environ.get(ENV_IMAGE_KEY, "").strip()
    return bool((img.get("base_url") or "").strip()) and bool(api_key) and bool((img.get("model") or "").strip())


def cache_key_for(key_visual):
    """关键视觉内容哈希（sha1 前 8 位），作为生图产物文件名去重键。"""
    return hashlib.sha1((key_visual or "").strip().encode("utf-8")).hexdigest()[:8]


# 生图产物支持格式（扩展名 → 魔数校验：头字节 + 最小完整长度）
SUPPORTED_IMAGE_FORMATS = ("png", "jpg", "webp")
_IMAGE_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n", 8),
    "jpg": (b"\xff\xd8\xff", 4),
    "webp": (b"RIFF", 12),  # RIFF????WEBP，头 12 字节
}


def _image_file_intact(path):
    """按扩展名校验图片文件完整性：魔数齐备且长度达标；残缺文件删除返回 False。"""
    path = Path(path)
    try:
        if not path.exists():
            return False
        if path.stat().st_size <= 0:
            raise ValueError("空文件")
        magic, need = _IMAGE_MAGIC.get(path.suffix.lstrip(".").lower(), (None, 0))
        if magic is None:
            return False
        head = path.read_bytes()[:max(need, 12)]
        if len(head) < need or not head.startswith(magic):
            raise ValueError("魔数不符")
        if path.suffix.lower() == ".webp" and head[8:12] != b"WEBP":
            raise ValueError("非 WEBP 容器")
        return True
    except Exception:
        try:
            path.unlink()  # 残缺/损坏缓存：删除以便重取
        except OSError:
            pass
        return False


def normalize_asset_id(value):
    """Normalize a user-facing asset id and reject path traversal."""
    raw = str(value or "").strip()
    if not raw:
        raw = "scientific-figure"
    stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", raw, flags=re.UNICODE).strip("._-")
    if not stem:
        raise ImageGenError("素材名称规范化后为空")
    reserved = {"con", "aux", "nul", "prn"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
    if stem.lower() in reserved:
        raise ImageGenError(f"素材名称命中保留设备名: {stem}")
    return stem[:96]


def _asset_root(project_dir, asset_id):
    root = Path(project_dir).resolve()
    if not (root / "state.json").exists():
        raise ImageGenError("项目不存在或缺少 state.json")
    aid = normalize_asset_id(asset_id)
    target = (root / "data" / "figures" / aid).resolve()
    if root not in target.parents:
        raise ImageGenError("素材路径越界")
    return target, aid


def _standalone_asset_root(asset_id):
    """Resolve an asset directory for the project-independent drawing mode."""
    aid = normalize_asset_id(asset_id)
    target = (STANDALONE_FIGURES_ROOT / aid).resolve()
    base = STANDALONE_FIGURES_ROOT.resolve()
    if base not in target.parents:
        raise ImageGenError("独立素材路径越界")
    return target, aid


def _load_versions(root):
    p = root / "versions.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_versions(root, records):
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "versions.json.tmp"
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, root / "versions.json")


def _next_version(records):
    nums = []
    for row in records:
        m = re.fullmatch(r"v(\d{3,})", str(row.get("version", "")))
        if m:
            nums.append(int(m.group(1)))
    return f"v{(max(nums) + 1 if nums else 1):03d}"


def save_version(project_dir, asset_id, image_b64, fmt="png"):
    """Save a generated image as a new immutable version."""
    root, aid = _asset_root(project_dir, asset_id)
    ext = str(fmt or "png").lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ("png", "jpg", "webp"):
        ext = "png"
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except Exception as e:
        raise ImageGenError(f"生成图片 Base64 无效: {e}") from e
    if not raw:
        raise ImageGenError("生成图片为空")
    versions_dir = root / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    records = _load_versions(root)
    version = _next_version(records)
    filename = f"{version}.{ext}"
    target = versions_dir / filename
    while target.exists():
        version = f"v{int(version[1:]) + 1:03d}"
        filename = f"{version}.{ext}"
        target = versions_dir / filename
    tmp = versions_dir / (filename + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, target)
    if not _image_file_intact(target):
        raise ImageGenError("生成图片内容不是有效的 PNG/JPG/WEBP")
    record = {"version": version, "filename": filename}
    records.append(record)
    _save_versions(root, records)
    return {**record, "asset_id": aid, "rel": str(target.relative_to(Path(project_dir).resolve()))}


def save_standalone_version(asset_id, image_b64, fmt="png"):
    """Save a generated image in the project-independent workspace store."""
    root, aid = _standalone_asset_root(asset_id)
    result = _save_version_under_root(root, aid, image_b64, fmt)
    result["rel"] = str((Path("data") / "figures" / "_standalone" / aid / "versions" / result["filename"]).as_posix())
    result["standalone"] = True
    return result


def _save_version_under_root(root, aid, image_b64, fmt="png"):
    ext = str(fmt or "png").lower().lstrip(".")
    if ext == "jpeg": ext = "jpg"
    if ext not in ("png", "jpg", "webp"): ext = "png"
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except Exception as e:
        raise ImageGenError(f"生成图片 Base64 无效: {e}") from e
    if not raw: raise ImageGenError("生成图片为空")
    versions_dir = root / "versions"; versions_dir.mkdir(parents=True, exist_ok=True)
    records = _load_versions(root); version = _next_version(records)
    filename = f"{version}.{ext}"; target = versions_dir / filename
    while target.exists():
        version = f"v{int(version[1:]) + 1:03d}"; filename = f"{version}.{ext}"; target = versions_dir / filename
    tmp = versions_dir / (filename + ".tmp"); tmp.write_bytes(raw); os.replace(tmp, target)
    if not _image_file_intact(target): raise ImageGenError("生成图片内容不是有效的 PNG/JPG/WEBP")
    record = {"version": version, "filename": filename}; records.append(record); _save_versions(root, records)
    return {**record, "asset_id": aid}


def save_figureforge_version(project_dir, asset_id, image_b64, project_json, fmt="png"):
    """Save a FigureForge-rendered image and its editable project JSON together."""
    root, aid = _asset_root(project_dir, asset_id) if project_dir else _standalone_asset_root(asset_id)
    if isinstance(project_json, str):
        try:
            project_obj = json.loads(project_json)
        except Exception as e:
            raise ImageGenError(f"FigureForge 工程 JSON 无效: {e}") from e
    elif isinstance(project_json, dict):
        project_obj = project_json
    else:
        raise ImageGenError("FigureForge 工程 JSON 必填")
    if not isinstance(project_obj, dict) or not project_obj.get("baseImageSrc"):
        raise ImageGenError("FigureForge 工程 JSON 缺少 baseImageSrc")
    saved = _save_version_under_root(root, aid, image_b64, fmt)
    project_name = f"{saved['version']}.figureforge.json"
    project_path = root / "versions" / project_name
    tmp = root / "versions" / (project_name + ".tmp")
    tmp.write_text(json.dumps(project_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, project_path)
    records = _load_versions(root)
    for row in records:
        if row.get("version") == saved["version"]:
            row["project_file"] = project_name
    _save_versions(root, records)
    prefix = Path("data") / "figures"
    if not project_dir:
        rel_root = prefix / "_standalone" / aid / "versions"
    else:
        rel_root = prefix / aid / "versions"
    saved.update({"project_file": project_name, "project_rel": str((rel_root / project_name).as_posix()),
                  "rel": str((rel_root / saved["filename"]).as_posix())})
    return saved


def load_figureforge_version(project_dir, asset_id, version):
    """Load a stored PNG version and optional FigureForge project JSON."""
    root, aid = _asset_root(project_dir, asset_id) if project_dir else _standalone_asset_root(asset_id)
    if not re.fullmatch(r"v\d{3,}", str(version or "")):
        raise ImageGenError("版本号格式无效")
    records = _load_versions(root)
    row = next((r for r in records if str(r.get("version")) == str(version)), None)
    if not row:
        raise ImageGenError(f"版本不存在: {version}")
    filename = str(row.get("filename") or f"{version}.png")
    path = root / "versions" / filename
    if not path.is_file() or not _image_file_intact(path):
        raise ImageGenError(f"版本不存在或已损坏: {version}")
    project_name = str(row.get("project_file") or f"{version}.figureforge.json")
    project_path = root / "versions" / project_name
    project_json = None
    if project_path.is_file():
        project_json = project_path.read_text(encoding="utf-8", errors="replace")
    raw = path.read_bytes()
    if project_dir:
        rel = str(path.relative_to(Path(project_dir).resolve()))
        project_rel = str(project_path.relative_to(Path(project_dir).resolve())) if project_path.exists() else ""
    else:
        rel = f"data/figures/_standalone/{aid}/versions/{filename}"
        project_rel = f"data/figures/_standalone/{aid}/versions/{project_name}" if project_path.exists() else ""
    return {"ok": True, "asset_id": aid, "version": str(version), "image_b64": base64.b64encode(raw).decode("ascii"),
            "project_json": project_json, "format": path.suffix.lower().lstrip("."), "rel": rel, "project_rel": project_rel}


def _safe_reference_id(value):
    ref = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip()).strip("_-")
    if not ref:
        raise ImageGenError("参考图编号无效")
    return ref[:96]


def save_reference(project_dir, asset_id, raw, filename="reference.png"):
    """Persist a validated uploaded reference image without overwriting."""
    root, aid = _asset_root(project_dir, asset_id)
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise ImageGenError("参考图为空")
    if len(raw) > MAX_REFERENCE_BYTES:
        raise ImageGenError("参考图超过 20MB 上限")
    original = Path(str(filename or "reference.png")).name
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else "png"
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ("png", "jpg", "webp"):
        raise ImageGenError("参考图仅支持 PNG/JPG/WEBP")
    # Validate bytes by temporarily using the same magic checks as API edits.
    magic_ok = ((ext == "png" and bytes(raw).startswith(_IMAGE_MAGIC["png"][0]))
                or (ext == "jpg" and bytes(raw).startswith(_IMAGE_MAGIC["jpg"][0]))
                or (ext == "webp" and len(raw) >= 12 and bytes(raw).startswith(b"RIFF") and bytes(raw)[8:12] == b"WEBP"))
    if not magic_ok:
        raise ImageGenError("参考图格式不支持或文件内容与扩展名不匹配")
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    existing = sorted(refs.glob("ref*.*"))
    nums = []
    for p in existing:
        m = re.match(r"ref(\d+)", p.stem)
        if m:
            nums.append(int(m.group(1)))
    ref_id = f"ref{(max(nums) + 1 if nums else 1):03d}"
    out_name = f"{ref_id}.{ext}"
    target = refs / out_name
    target.write_bytes(bytes(raw))
    return {"reference_id": ref_id, "filename": out_name, "asset_id": aid,
            "rel": str(target.relative_to(Path(project_dir).resolve()))}


def save_standalone_reference(asset_id, raw, filename="reference.png"):
    """Persist a reference image for a project-independent asset."""
    root, aid = _standalone_asset_root(asset_id)
    result = _save_reference_under_root(root, aid, raw, filename)
    result["rel"] = str((Path("data") / "figures" / "_standalone" / aid / "references" / result["filename"]).as_posix())
    result["standalone"] = True
    return result


def _save_reference_under_root(root, aid, raw, filename="reference.png"):
    if not isinstance(raw, (bytes, bytearray)) or not raw: raise ImageGenError("参考图为空")
    if len(raw) > MAX_REFERENCE_BYTES: raise ImageGenError("参考图超过 20MB 上限")
    original = Path(str(filename or "reference.png")).name
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else "png"
    if ext == "jpeg": ext = "jpg"
    if ext not in ("png", "jpg", "webp"): raise ImageGenError("参考图仅支持 PNG/JPG/WEBP")
    magic_ok = ((ext == "png" and bytes(raw).startswith(_IMAGE_MAGIC["png"][0])) or
                (ext == "jpg" and bytes(raw).startswith(_IMAGE_MAGIC["jpg"][0])) or
                (ext == "webp" and len(raw) >= 12 and bytes(raw).startswith(b"RIFF") and bytes(raw)[8:12] == b"WEBP"))
    if not magic_ok: raise ImageGenError("参考图格式不支持或文件内容与扩展名不匹配")
    refs = root / "references"; refs.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in refs.glob("ref*.*") if (m := re.match(r"ref(\d+)", p.stem))]
    ref_id = f"ref{(max(nums) + 1 if nums else 1):03d}"; out_name = f"{ref_id}.{ext}"
    (refs / out_name).write_bytes(bytes(raw))
    return {"reference_id": ref_id, "filename": out_name, "asset_id": aid}


def list_asset_versions(project_dir, asset_id):
    """Return persisted versions/references and the explicit current pointer."""
    root, aid = _asset_root(project_dir, asset_id)
    versions = []
    for row in _load_versions(root):
        version = str(row.get("version", ""))
        filename = str(row.get("filename", ""))
        path = root / "versions" / filename
        if re.fullmatch(r"v\d{3,}", version) and path.is_file() and _image_file_intact(path):
            versions.append({"version": version, "filename": filename,
                             "asset_id": aid,
                             "rel": str(path.relative_to(Path(project_dir).resolve()))})
    versions.sort(key=lambda x: int(x["version"][1:]))
    references = []
    refs = root / "references"
    if refs.exists():
        for path in sorted(refs.iterdir()):
            if path.is_file() and path.suffix.lower().lstrip(".") in ("png", "jpg", "jpeg", "webp"):
                references.append({"reference_id": path.stem, "filename": path.name,
                                   "asset_id": aid,
                                   "rel": str(path.relative_to(Path(project_dir).resolve()))})
    current = ""
    current_file = root / "current.txt"
    if current_file.exists():
        current = current_file.read_text(encoding="utf-8", errors="replace").strip()
    return {"asset_id": aid, "current": current, "versions": versions, "references": references}


def list_standalone_versions(asset_id):
    root, aid = _standalone_asset_root(asset_id)
    versions = []; refs = []
    for row in _load_versions(root):
        version, filename = str(row.get("version", "")), str(row.get("filename", ""))
        path = root / "versions" / filename
        if re.fullmatch(r"v\d{3,}", version) and path.is_file() and _image_file_intact(path):
            versions.append({"version": version, "filename": filename, "asset_id": aid,
                             "rel": f"data/figures/_standalone/{aid}/versions/{filename}"})
    versions.sort(key=lambda x: int(x["version"][1:]))
    refdir = root / "references"
    if refdir.exists():
        for path in sorted(refdir.iterdir()):
            if path.is_file() and path.suffix.lower().lstrip(".") in ("png", "jpg", "jpeg", "webp"):
                refs.append({"reference_id": path.stem, "filename": path.name, "asset_id": aid,
                             "rel": f"data/figures/_standalone/{aid}/references/{path.name}"})
    current = (root / "current.txt").read_text(encoding="utf-8", errors="replace").strip() if (root / "current.txt").exists() else ""
    return {"asset_id": aid, "current": current, "versions": versions, "references": refs, "standalone": True}


def resolve_standalone_image(asset_id, version="", reference_id=""):
    root, aid = _standalone_asset_root(asset_id)
    if version:
        if not re.fullmatch(r"v\d{3,}", str(version)): raise ImageGenError("版本号格式无效")
        matches = list((root / "versions").glob(str(version) + ".*")); path = matches[0] if matches else root / "versions" / (str(version) + ".png")
        if not path.is_file() or not _image_file_intact(path): raise ImageGenError(f"版本不存在或已损坏: {version}")
        return path
    if reference_id:
        ref = _safe_reference_id(reference_id); matches = list((root / "references").glob(ref + ".*")); path = matches[0] if matches else root / "references" / ref
        if not path.is_file(): raise ImageGenError(f"参考图不存在: {reference_id}")
        _reference_info(path); return path
    raise ImageGenError("编辑必须提供 source_version 或 reference_id")


def set_standalone_current(asset_id, version):
    root, aid = _standalone_asset_root(asset_id); path = resolve_standalone_image(aid, version=version)
    if path.parent != root / "versions": raise ImageGenError("当前版本必须来自 versions 目录")
    tmp = root / "current.txt.tmp"; tmp.write_text(str(version) + "\n", encoding="utf-8"); os.replace(tmp, root / "current.txt")
    return {"asset_id": aid, "current": str(version), "standalone": True}


def resolve_asset_image(project_dir, asset_id, version="", reference_id=""):
    """Resolve a stored version or reference for an edit request."""
    root, aid = _asset_root(project_dir, asset_id)
    if version:
        if not re.fullmatch(r"v\d{3,}", str(version)):
            raise ImageGenError("版本号格式无效")
        path = root / "versions" / (str(version) + ".png")
        if not path.exists():
            matches = list((root / "versions").glob(str(version) + ".*"))
            path = matches[0] if matches else path
        if not path.is_file() or not _image_file_intact(path):
            raise ImageGenError(f"版本不存在或已损坏: {version}")
        return path
    if reference_id:
        ref = _safe_reference_id(reference_id)
        matches = list((root / "references").glob(ref + ".*"))
        path = matches[0] if matches else root / "references" / ref
        if not path.is_file():
            raise ImageGenError(f"参考图不存在: {reference_id}")
        _reference_info(path)
        return path
    raise ImageGenError("编辑必须提供 source_version 或 reference_id")


def set_current_version(project_dir, asset_id, version):
    """Set the explicit current version after validating it exists/intact."""
    root, aid = _asset_root(project_dir, asset_id)
    path = resolve_asset_image(project_dir, aid, version=version)
    if path.parent != root / "versions":
        raise ImageGenError("当前版本必须来自 versions 目录")
    current = str(version)
    tmp = root / "current.txt.tmp"
    tmp.write_text(current + "\n", encoding="utf-8")
    os.replace(tmp, root / "current.txt")
    return {"asset_id": aid, "current": current}


def find_cached_figure_image(figures_dir, cache_key):
    """按 cache_key 在 figures_dir 查找完整缓存产物（任一支持扩展名），返回 Path 或 None。"""
    d = Path(figures_dir)
    if not d.exists():
        return None
    for ext in SUPPORTED_IMAGE_FORMATS:
        p = d / ("fig_imagegen_%s.%s" % (cache_key, ext))
        if p.exists() and _image_file_intact(p):
            return p
    return None


def clear_cached_figure_image(figures_dir, cache_key):
    """删除该 cache_key 的全部缓存产物（所有支持扩展名）。"""
    d = Path(figures_dir)
    if not d.exists():
        return
    for ext in SUPPORTED_IMAGE_FORMATS:
        p = d / ("fig_imagegen_%s.%s" % (cache_key, ext))
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def generate_figure_image(prompt, out_dir, cache_key):
    """图表混合路由生图入口：sha1 内容哈希缓存去重 + 落盘 fig_imagegen_<cache_key>.<ext>。

    - 完整缓存（png/jpg/webp 任一，魔数校验通过）存在时直接复用（不调 API）；
      残缺/损坏缓存自动删除并重取；
    - 扩展名按 API 返回 format 决定（png/jpg，其余归 jpg；webp 仅兼容复用旧产物）；
    - 落盘原子化：临时文件 + os.replace；
    - 开关关闭或 API 未配置 → 抛 ImageGenUnavailable；其余失败 → 抛 ImageGenError。

    返回 {"file": 绝对路径, "cached": bool, "model": ...}。
    """
    _, img = load_config()
    if not img.get("enabled", True):
        raise ImageGenUnavailable("生图开关已关闭（image.enabled=false）")
    base_url = (img.get("base_url") or "").strip().rstrip("/")
    api_key = (img.get("api_key") or "").strip() or os.environ.get(ENV_IMAGE_KEY, "").strip()
    model = (img.get("model") or "").strip()
    if not base_url or not api_key or not model:
        raise ImageGenUnavailable("生图 API 未配置（base_url/api_key/model 缺失）")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cached = find_cached_figure_image(out_dir, cache_key)  # 完整性校验内置，残缺自动清理
    if cached is not None:
        return {"file": str(cached), "cached": True, "model": model}
    result = generate_image(build_image_prompt(prompt))  # generate_image 内部含重试上限
    if not result.get("ok"):
        raise ImageGenError(result.get("error", "生图失败"))
    fmt = str(result.get("format") or "png").lower()
    ext = fmt if fmt in ("png", "jpg", "webp") else "jpg"
    target = out_dir / f"fig_imagegen_{cache_key}.{ext}"
    tmp = out_dir / (target.name + ".tmp")
    try:
        tmp.write_bytes(base64.b64decode(result["image_b64"]))
        os.replace(tmp, target)  # 原子替换，避免半写状态被当缓存复用
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ImageGenError(f"生图落盘失败: {e}") from e
    return {"file": str(target), "cached": False, "model": result.get("model", "")}
