#!/usr/bin/env python3
"""Hybrid literature retrieval for the Obsidian research vault.

The script indexes curated Markdown notes only. Embeddings are requested from
an OpenAI-compatible API; credentials are read from process environment
variables or an out-of-vault local env file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment diagnostic
    raise SystemExit("缺少 PyYAML，请安装后重试: python3 -m pip install pyyaml") from exc

try:
    import chromadb
except ImportError as exc:  # pragma: no cover - environment diagnostic
    raise SystemExit("缺少 chromadb，请安装后重试: python3 -m pip install chromadb") from exc


VAULT_ROOT = Path(__file__).resolve().parents[1]
INDEX_ROOT = VAULT_ROOT / ".research_index"
MANIFEST_PATH = INDEX_ROOT / "manifest.json"
INDEX_VERSION = 1
SOURCE_DIRS = (
    VAULT_ROOT / "10_论文",
    VAULT_ROOT / "20_知识" / "方法",
    VAULT_ROOT / "20_知识" / "流程",
    VAULT_ROOT / "20_知识" / "批判",
    VAULT_ROOT / "20_知识" / "启发",
)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_ENV_FILE_LOADED = False


class EmbeddingAPIError(RuntimeError):
    pass


@dataclass
class NoteChunk:
    chunk_id: str
    path: str
    title: str
    section: str
    text: str
    metadata: Dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_env_file() -> None:
    """Load a local, out-of-vault env file without overriding real env vars."""
    global _ENV_FILE_LOADED
    if _ENV_FILE_LOADED:
        return
    _ENV_FILE_LOADED = True
    configured = os.environ.get("EMBEDDING_ENV_FILE", "").strip()
    path = Path(configured).expanduser() if configured else Path.home() / ".config" / "obsidian-codex" / "embedding.env"
    if not path.is_file():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        return


def read_env(name: str, default: str = "") -> str:
    load_env_file()
    return os.environ.get(name, default).strip()


def required_embedding_config() -> Dict[str, Any]:
    base_url = read_env("EMBEDDING_BASE_URL")
    api_key = read_env("EMBEDDING_API_KEY")
    model = read_env("EMBEDDING_MODEL")
    missing = [name for name, value in (("EMBEDDING_BASE_URL", base_url), ("EMBEDDING_API_KEY", api_key), ("EMBEDDING_MODEL", model)) if not value]
    if missing:
        raise EmbeddingAPIError("缺少 embedding 配置: " + ", ".join(missing) + "。API key 只应通过环境变量提供。")
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/embeddings"):
        endpoint += "/embeddings"
    try:
        timeout = max(1.0, float(read_env("EMBEDDING_TIMEOUT_S", "60")))
        batch_size = max(1, int(read_env("EMBEDDING_BATCH_SIZE", "32")))
    except ValueError as exc:
        raise EmbeddingAPIError("EMBEDDING_TIMEOUT_S 和 EMBEDDING_BATCH_SIZE 必须是数字。") from exc
    host = urllib.parse.urlparse(endpoint).hostname or "configured-provider"
    return {"endpoint": endpoint, "api_key": api_key, "model": model, "timeout": timeout, "batch_size": batch_size, "provider": host}


class EmbeddingClient:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        output: List[List[float]] = []
        batch_size = self.config["batch_size"]
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            output.extend(self._embed_batch(batch))
        return output

    def _embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        payload = json.dumps({"model": self.config["model"], "input": list(texts)}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.config["api_key"]}
        last_error: Optional[Exception] = None
        for attempt in range(3):
            request = urllib.request.Request(self.config["endpoint"], data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.config["timeout"]) as response:
                    body = json.loads(response.read().decode("utf-8"))
                vectors = self._parse_response(body, len(texts))
                return vectors
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in RETRYABLE_STATUS or attempt == 2:
                    raise EmbeddingAPIError(f"embedding API HTTP {exc.code}，未写入向量索引。") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, EmbeddingAPIError) as exc:
                last_error = exc
                if attempt == 2:
                    if isinstance(exc, EmbeddingAPIError):
                        raise
                    raise EmbeddingAPIError("embedding API 请求失败，未写入向量索引。") from exc
            time.sleep(2**attempt)
        raise EmbeddingAPIError("embedding API 请求失败，未写入向量索引。") from last_error

    @staticmethod
    def _parse_response(body: Any, expected: int) -> List[List[float]]:
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list) or len(rows) != expected:
            raise EmbeddingAPIError("embedding API 响应缺少与输入数量匹配的 data。")
        ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
        vectors = [row.get("embedding") for row in ordered]
        if any(not isinstance(vector, list) or not vector for vector in vectors):
            raise EmbeddingAPIError("embedding API 返回了无效的向量。")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise EmbeddingAPIError("embedding API 返回的向量维度不一致。")
        return [[float(value) for value in vector] for vector in vectors]


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    match = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", text, flags=re.DOTALL)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    return data if isinstance(data, dict) else {}, match.group(2)


def scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def split_chunks(body: str, max_chars: int = 650) -> List[Tuple[str, str]]:
    lines = body.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    current = "正文"
    buffer: List[str] = []
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            if buffer and "\n".join(buffer).strip():
                sections.append((current, buffer))
            current = heading.group(2)
            buffer = [line]
        else:
            buffer.append(line)
    if buffer and "\n".join(buffer).strip():
        sections.append((current, buffer))

    chunks: List[Tuple[str, str]] = []
    for section, section_lines in sections:
        paragraphs = "\n".join(section_lines).strip()
        if len(paragraphs) <= max_chars:
            chunks.append((section, paragraphs))
            continue
        words = re.split(r"(\s+)", paragraphs)
        piece = ""
        for word in words:
            if piece and len(piece) + len(word) > max_chars:
                chunks.append((section, piece.strip()))
                piece = ""
            piece += word
        if piece.strip():
            chunks.append((section, piece.strip()))
    return [(section, text) for section, text in chunks if text.strip()]


def iter_note_chunks() -> Iterable[NoteChunk]:
    for directory in SOURCE_DIRS:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            raw = path.read_text(encoding="utf-8")
            props, body = parse_frontmatter(raw)
            if not props.get("type") or props.get("type") not in {"paper", "workflow", "knowledge"}:
                continue
            relative = path.relative_to(VAULT_ROOT).as_posix()
            title = scalar(props.get("title")) or path.stem
            metadata = {
                "path": relative,
                "title": title,
                "type": scalar(props.get("type")),
                "topics": scalar(props.get("topics")),
                "ml_tasks": scalar(props.get("ml_tasks")),
                "models": scalar(props.get("models")),
                "status": scalar(props.get("status")),
                "review_status": scalar(props.get("review_status")),
                "evidence_status": scalar(props.get("evidence_status")),
                "reproducibility": scalar(props.get("reproducibility")),
                "code_url": scalar(props.get("code_url")),
                "data_url": scalar(props.get("data_url")),
            }
            for index, (section, text) in enumerate(split_chunks(body)):
                normalized = " ".join(text.split())
                chunk_id = hashlib.sha256((read_env("EMBEDDING_MODEL") + "\n" + normalized).encode("utf-8")).hexdigest()
                chunk_metadata = dict(metadata)
                chunk_metadata["section"] = section
                chunk_metadata["chunk_index"] = index
                yield NoteChunk(chunk_id, relative, title, section, text, chunk_metadata)


def load_manifest() -> Dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_manifest(manifest: Dict[str, Any]) -> None:
    INDEX_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collection_name(model: str, dimension: int) -> str:
    model_hash = hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]
    return f"research_{model_hash}_{dimension}"


def get_collection(client: Any, manifest: Dict[str, Any], model: str, dimension: int, rebuild: bool = False) -> Any:
    name = collection_name(model, dimension)
    if rebuild and name in {item.name for item in client.list_collections()}:
        client.delete_collection(name)
    return client.get_or_create_collection(name=name, metadata={"index_version": INDEX_VERSION, "model": model, "dimension": dimension})


def index_notes() -> int:
    config = required_embedding_config()
    chunks = list(iter_note_chunks())
    if not chunks:
        print("没有找到可索引的 Paper/Workflow/Knowledge Markdown。")
        return 0
    client = chromadb.PersistentClient(path=str(INDEX_ROOT / "chroma"))
    manifest = load_manifest()
    old_model = manifest.get("model")
    old_dimension = manifest.get("dimension")
    if old_model and old_model != config["model"]:
        print("检测到 embedding model 变化，将创建新的 Chroma collection。")
    desired_ids = {chunk.chunk_id for chunk in chunks}
    same_index = bool(old_model == config["model"] and old_dimension and manifest.get("collection"))
    collection = None
    existing_ids: set[str] = set()
    if same_index:
        try:
            collection = client.get_collection(manifest["collection"])
            existing = collection.get(include=[])
            existing_ids = set(existing.get("ids", []))
        except Exception:
            collection = None
            existing_ids = set()
    new_chunks = chunks if collection is None else [chunk for chunk in chunks if chunk.chunk_id not in existing_ids]
    if collection is None:
        vectors = EmbeddingClient(config).embed([chunk.text for chunk in chunks])
        dimension = len(vectors[0])
        if old_model == config["model"] and old_dimension and int(old_dimension) != dimension:
            raise EmbeddingAPIError("当前 embedding model 返回了不同维度；请检查模型配置后重建索引。")
        collection = get_collection(client, manifest, config["model"], dimension, rebuild=bool(old_model and old_model != config["model"]))
    else:
        dimension = int(old_dimension)
        vectors = EmbeddingClient(config).embed([chunk.text for chunk in new_chunks]) if new_chunks else []
        if vectors and len(vectors[0]) != dimension:
            raise EmbeddingAPIError("当前 embedding model 返回了不同维度；请检查模型配置后重建索引。")
    stale = list(existing_ids - desired_ids)
    if stale:
        collection.delete(ids=stale)
    if new_chunks:
        collection.upsert(ids=[chunk.chunk_id for chunk in new_chunks], embeddings=vectors, documents=[chunk.text for chunk in new_chunks], metadatas=[chunk.metadata for chunk in new_chunks])
    manifest = {"index_version": INDEX_VERSION, "provider": config["provider"], "model": config["model"], "dimension": dimension, "collection": collection.name, "chunk_count": len(chunks), "indexed_at": utc_now()}
    save_manifest(manifest)
    print(f"索引完成: {len(chunks)} 个文本块，新增/更新 {len(new_chunks)} 个，collection={collection.name}，dimension={dimension}")
    return len(chunks)


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]", text.lower())


def lexical_candidates(query: str, top: int) -> List[Dict[str, Any]]:
    query_tokens = set(tokenize(query))
    grouped: Dict[str, Dict[str, Any]] = {}
    for chunk in iter_note_chunks():
        searchable = f"{chunk.title} {chunk.section} {chunk.text} {json.dumps(chunk.metadata, ensure_ascii=False)}".lower()
        tokens = set(tokenize(searchable))
        overlap = len(query_tokens & tokens)
        if overlap == 0:
            continue
        score = overlap / max(1, len(query_tokens))
        item = grouped.setdefault(chunk.path, {"path": chunk.path, "title": chunk.title, "metadata": chunk.metadata, "text": chunk.text, "semantic_score": 0.0, "lexical_score": 0.0})
        item["lexical_score"] = max(item["lexical_score"], score)
        if chunk.text not in item["text"]:
            item["text"] = (item["text"] + "\n" + chunk.text)[:8000]
    return sorted(grouped.values(), key=lambda item: item["lexical_score"], reverse=True)[:top]


def semantic_candidates(query: str, top: int) -> List[Dict[str, Any]]:
    manifest = load_manifest()
    if not manifest.get("collection"):
        raise EmbeddingAPIError("尚未建立向量索引，请先运行 index。")
    config = required_embedding_config()
    if config["model"] != manifest.get("model"):
        raise EmbeddingAPIError("当前 EMBEDDING_MODEL 与索引模型不同，请先重建索引。")
    client = chromadb.PersistentClient(path=str(INDEX_ROOT / "chroma"))
    collection = client.get_collection(manifest["collection"])
    vector = EmbeddingClient(config).embed([query])[0]
    result = collection.query(query_embeddings=[vector], n_results=max(top * 3, top), include=["documents", "metadatas", "distances"])
    grouped: Dict[str, Dict[str, Any]] = {}
    for document, metadata, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
        path = metadata["path"]
        score = 1.0 / (1.0 + float(distance))
        item = grouped.setdefault(path, {"path": path, "title": metadata.get("title", path), "metadata": metadata, "text": document, "semantic_score": 0.0, "lexical_score": 0.0})
        item["semantic_score"] = max(item["semantic_score"], score)
        if document not in item["text"]:
            item["text"] = (item["text"] + "\n" + document)[:8000]
    return sorted(grouped.values(), key=lambda item: item["semantic_score"], reverse=True)[:top]


def profile_text() -> str:
    path = VAULT_ROOT / "00_系统" / "研究画像.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def fit_score(item: Dict[str, Any], query: str) -> Tuple[float, Dict[str, float], List[str], List[str]]:
    text = f"{item['title']} {item['text']} {json.dumps(item['metadata'], ensure_ascii=False)}".lower()
    context = f"{query} {profile_text()}".lower()
    document_tokens = set(tokenize(text))
    context_tokens = set(tokenize(context))
    overlap = len(context_tokens & document_tokens) / max(1, len(context_tokens))
    small_data = any(term in text for term in ("small-data", "small sample", "few-shot", "low-data", "63 个催化剂", "少量", "小数据", "小样本", "meta-learning", "元学习"))
    split_quality = any(term in text for term in ("out-of-sample", "external validation", "cluster", "cross-validation", "外部测试", "外推"))
    validation = any(term in text for term in ("auprc", "auroc", "validation", "evaluation", "评价", "验证"))
    domain = any(term in text for term in ("electrocatalysis", "electrocatalytic", "电催化"))
    reproducible = item["metadata"].get("reproducibility") in {"full", "good"} or (("github" in text or "zenodo" in text) and "code" in text)
    reviewed = item["metadata"].get("review_status") == "reviewed"
    dimensions = {
        "task_match": round(25 * overlap, 2),
        "small_data_match": 20.0 if small_data else 0.0,
        "generalization_design": 15.0 if split_quality else 0.0,
        "validation_metrics": 15.0 if validation else 0.0,
        "electrocatalysis_transfer": 10.0 if domain else 0.0,
        "reproducibility": 10.0 if reproducible else 0.0,
        "evidence_completeness": 5.0 if reviewed or item["metadata"].get("evidence_status") == "full" else 2.0,
    }
    score = sum(dimensions.values())
    risks: List[str] = []
    missing: List[str] = []
    if not domain:
        risks.append("domain-mismatch")
    if "data leakage" in text or "数据泄漏" in text:
        risks.append("data-leakage-risk")
    if not any(term in text for term in ("external", "out-of-sample", "外部", "外推")):
        risks.append("weak-external-validation")
    if not reproducible:
        risks.append("missing-reproducibility")
        missing.append("代码、环境或数据复现信息不足")
    if item["metadata"].get("evidence_status") in {"partial", ""}:
        risks.append("insufficient-evidence")
        missing.append("证据状态尚未完整核对")
    if not item["metadata"].get("ml_tasks"):
        risks.append("task-definition-mismatch")
        missing.append("缺少结构化 ML task")
    return round(score, 2), dimensions, risks, missing


def search(query: str, top: int) -> int:
    lexical = lexical_candidates(query, max(top * 3, top))
    semantic: List[Dict[str, Any]] = []
    semantic_status = "available"
    try:
        semantic = semantic_candidates(query, max(top * 3, top))
    except EmbeddingAPIError as exc:
        semantic_status = "unavailable"
        print(f"语义检索不可用，已降级为关键词检索：{exc}", file=sys.stderr)
    merged: Dict[str, Dict[str, Any]] = {}
    for item in lexical + semantic:
        current = merged.setdefault(item["path"], item.copy())
        current["lexical_score"] = max(current.get("lexical_score", 0), item.get("lexical_score", 0))
        current["semantic_score"] = max(current.get("semantic_score", 0), item.get("semantic_score", 0))
        if len(item.get("text", "")) > len(current.get("text", "")):
            current["text"] = item["text"]
    ranked = []
    for item in merged.values():
        fit, dimensions, risks, missing = fit_score(item, query)
        retrieval = 0.55 * item.get("semantic_score", 0) + 0.45 * item.get("lexical_score", 0)
        item["fit_score"] = fit
        item["fit_dimensions"] = dimensions
        item["retrieval_score"] = round(retrieval, 4)
        item["risks"] = risks
        item["missing"] = missing
        item["evidence_anchors"] = re.findall(r"(?:p{1,2}\.\s*\d+(?:[–-]\d+)?|pp\.\s*\d+(?:[–-]\d+)?)\b[^\n]*", item["text"], flags=re.IGNORECASE)[:3]
        ranked.append(item)
    ranked.sort(key=lambda item: (item["fit_score"], item["retrieval_score"]), reverse=True)
    output = {"query": query, "top": top, "semantic_status": semantic_status, "profile": "00_系统/研究画像.md", "results": ranked[:top]}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Obsidian hybrid literature retrieval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("index", help="index curated Markdown notes")
    search_parser = subparsers.add_parser("search", help="search curated Markdown notes")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.command == "index":
            return 0 if index_notes() >= 0 else 1
        return search(args.query, max(1, args.top))
    except EmbeddingAPIError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
