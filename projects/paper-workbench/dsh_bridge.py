#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSH Bridge — 将论文工作台 Web UI 桥接到 DSH Agent。

通过 DSH 的 JSON-RPC API 创建会话、发送提示、轮询响应,
使工作台的 AI 任务能够利用 Agent 的技能系统和工具编排能力。
"""
import json
import os
import time
import uuid
import urllib.request
import urllib.error

DSH_URL = "http://127.0.0.1:3080"
_FALLBACK_PRESET = "router-flash"  # settings.yaml 缺失/解析失败时的硬编码回退预设


def _resolve_default_preset(fallback=_FALLBACK_PRESET):
    """从 ~/.dsh/settings.yaml 读取默认 agent 预设（标准库逐行解析，不依赖 yaml 库）。

    定位顶层 agent-presets 配置块下的 default 子键，例如：
        agent-presets:
          default: router-flash
    文件不存在或解析失败时回退硬编码 fallback，避免写死已失效的预设名。
    """
    path = os.path.join(os.path.expanduser("~"), ".dsh", "settings.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            in_block = False
            for raw in f:
                line = raw.rstrip("\n")
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if not line[:1].isspace():
                    # 顶层键：进入/离开 agent-presets 块
                    in_block = (stripped == "agent-presets:")
                    continue
                if in_block and ":" in stripped:
                    key, _, val = stripped.partition(":")
                    if key.strip() == "default":
                        val = val.strip().strip("'\"")
                        if val:
                            return val
    except Exception:
        pass
    return fallback


# 模块级常量语义保持不变（导入时一次性解析），create_session 默认参数行为不受影响
DEFAULT_PRESET = _resolve_default_preset()
DEFAULT_TIMEOUT = 300
POLL_INTERVAL = 3
# 复用会话的缓存文件（dsh 无 session.close RPC，每次新建会话会累积孤儿）
_SESSION_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dsh_workbench_session")


def _rpc(method, payload, timeout=30):
    """发送 JSON-RPC 请求到 DSH,返回 result.value。"""
    body = json.dumps({
        "type": "client-request",
        "rpcId": str(uuid.uuid4()),
        "method": method,
        "payload": payload,
    }).encode("utf-8")
    req = urllib.request.Request(
        "%s/api/%s" % (DSH_URL, method),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError("DSH 不可达 (%s): %s" % (DSH_URL, e))
    except Exception as e:
        raise RuntimeError("DSH 请求异常: %s" % e)

    result = data.get("result", {})
    if not result.get("ok"):
        err = result.get("error", {})
        raise RuntimeError("DSH 错误 [%s]: %s" % (
            err.get("code", "?"),
            err.get("message", str(data)),
        ))
    return result.get("value")


def is_available():
    """检查 DSH 是否在运行。"""
    try:
        _rpc("host.describe", {}, timeout=5)
        return True
    except Exception:
        return False


def host_info():
    """获取 DSH 主机信息。"""
    return _rpc("host.describe", {}, timeout=5)


def list_presets():
    """列出可用的 Agent 预设。"""
    val = _rpc("agentPreset.list", {}, timeout=10)
    return [p.get("id") or p.get("name", "") for p in val.get("presets", [])]


def create_session(preset=DEFAULT_PRESET, cwd=None):
    """创建新的 DSH Agent 会话,返回 sessionId。"""
    payload = {"agentPreset": preset}
    if cwd:
        payload["cwd"] = cwd
    val = _rpc("session.create", payload, timeout=15)
    return val.get("sessionId", "")


def send_prompt(session_id, text):
    """向会话发送文本提示。"""
    return _rpc("session.prompt", {
        "sessionId": session_id,
        "mode": "queue",
        "content": [{"type": "text", "text": text}],
        "clientTimeZone": "Asia/Shanghai",
    }, timeout=15)


def get_history(session_id, max_messages=100):
    """获取会话事件历史。"""
    return _rpc("session.history", {
        "sessionId": session_id,
        "maxMessages": max_messages,
    }, timeout=15)


def _extract_text(events):
    """从事件列表中提取 assistant 的文本回复。"""
    texts = []
    tool_calls = []
    for e in events:
        ev = e.get("event", e)
        et = ev.get("type", "")

        if et == "assistant/message":
            content = ev.get("data", {}).get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    texts.append(block["text"])

        elif et == "tool/call":
            data = ev.get("data", {})
            tool_calls.append({
                "tool": data.get("name", "?"),
                "status": data.get("status", "?"),
            })

        elif et == "tool/result":
            data = ev.get("data", {})
            result_text = data.get("result", "")
            if isinstance(result_text, str) and len(result_text) > 200:
                result_text = result_text[:200] + "..."
            if tool_calls:
                tool_calls[-1]["result_preview"] = result_text

    return {
        "text": "\n".join(texts) if texts else "",
        "tool_calls": tool_calls,
    }


def _is_turn_complete(events):
    """检查最新的 turn 是否已完成。"""
    for e in reversed(events):
        ev = e.get("event", e)
        if ev.get("type") == "turn/end":
            reason = ev.get("data", {}).get("reason", {}).get("kind", "")
            return reason in ("completed", "cancelled", "error")
    return False


def _get_max_seq(events):
    """获取事件列表中的最大 seq。"""
    return max((e.get("event", e).get("seq", 0) for e in events), default=0)


def _session_running(session_id):
    """通过 session.list 轻量检查会话是否仍在运行。"""
    items = list_sessions()
    for s in items:
        if s.get("sessionId") == session_id:
            return s.get("running", False)
    return False


def wait_for_response(session_id, timeout=DEFAULT_TIMEOUT, poll_interval=POLL_INTERVAL, since_seq=0):
    """轮询会话状态直到 turn 完成,返回响应文本和工具调用记录。

    优化:先用 session.list 轻量轮询 running 字段,
    仅在 running 变为 false 时才拉取完整历史提取结果。
    since_seq>0 时只提取 seq 大于它的事件(复用会话时隔离本轮响应)。
    """
    def _recent(events):
        if since_seq:
            return [e for e in events if e.get("event", e).get("seq", 0) > since_seq]
        return events

    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            if _session_running(session_id):
                continue
            # running=false,拉取完整历史
            hist = get_history(session_id, max_messages=200)
            events = _recent(hist.get("events", []))
            if not events:
                return {"text": "", "tool_calls": []}
            result = _extract_text(events)
            return result
        except Exception:
            continue

    # 超时:仍尝试拉取已有结果
    try:
        hist = get_history(session_id, max_messages=200)
        events = _recent(hist.get("events", []))
        result = _extract_text(events)
        if result["text"] or result["tool_calls"]:
            result["timeout"] = True
            return result
    except Exception:
        pass
    return {
        "text": "(超时: Agent 在 %ds 内未完成)" % timeout,
        "tool_calls": [],
        "timeout": True,
    }


def _get_or_create_session(preset=DEFAULT_PRESET, cwd=None):
    """复用一个长期会话,避免每次委托都新建导致孤儿会话堆积(dsh 无 session.close RPC)。
    缓存 session_id 到本地文件;若缓存的会话已不存在(dsh 重启等)则新建并更新缓存。"""
    sid = ""
    try:
        if os.path.exists(_SESSION_CACHE_FILE):
            with open(_SESSION_CACHE_FILE, encoding="utf-8") as f:
                sid = f.read().strip()
    except Exception:
        sid = ""
    if sid:
        try:
            for s in list_sessions():
                if s.get("sessionId") == sid:
                    return sid
        except Exception:
            pass
    sid = create_session(preset=preset, cwd=cwd)
    try:
        with open(_SESSION_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(sid)
    except Exception:
        pass
    return sid


# 串行通道跨调用互斥（O_EXCL 文件锁 + 轮询）:
# subagent_writer 与 web 委托/gen_delegate 共用单会话缓存 .dsh_workbench_session,
# 不互斥时两路 send_prompt 会在同一会话上交叉, 造成响应串扰。
_SESSION_LOCK_FILE = _SESSION_CACHE_FILE + ".lock"


def _acquire_serial_lock(timeout=1800, poll=0.5, stale=3600):
    """获取串行通道锁（O_EXCL 文件锁, 跨进程/线程有效）; timeout 内拿不到则报错, 不无限等。
    锁文件存在超过 stale 秒视为持有进程崩溃残留, 强拆接管, 防死锁。"""
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(_SESSION_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(_SESSION_LOCK_FILE) > stale:
                    os.remove(_SESSION_LOCK_FILE)
                    continue
            except Exception:
                pass
            if time.time() > deadline:
                raise RuntimeError("串行通道互斥等待超时（.dsh_workbench_session 正被另一路委托占用）")
            time.sleep(poll)


def _release_serial_lock():
    """释放串行通道锁（删除失败静默; 陈旧锁由 _acquire_serial_lock 的 stale 逻辑兜底接管）。"""
    try:
        os.remove(_SESSION_LOCK_FILE)
    except Exception:
        pass


def delegate_task(instruction, cwd=None, preset=DEFAULT_PRESET, timeout=DEFAULT_TIMEOUT):
    """端到端委托:复用会话→记录 seq 边界→发送提示→只提取本轮响应。

    全程持串行通道互斥锁: 单会话缓存被 subagent 通道与 web 委托通道共用,
    不互斥时两路 send_prompt 交叉会造成串扰; finally 保证释放, 陈旧锁超时接管不死锁。
    """
    _acquire_serial_lock(timeout=max(int(timeout), 60))
    try:
        session_id = _get_or_create_session(preset=preset, cwd=cwd)
        # 发送前记录最大 seq,复用会话时只提取本轮新产生的事件
        since_seq = 0
        try:
            hist = get_history(session_id, max_messages=200)
            since_seq = _get_max_seq(hist.get("events", []))
        except Exception:
            since_seq = 0
        send_prompt(session_id, instruction)
        result = wait_for_response(session_id, timeout=timeout, since_seq=since_seq)
        result["sessionId"] = session_id
        return result
    finally:
        _release_serial_lock()


def list_sessions():
    """列出当前所有会话。"""
    val = _rpc("session.list", {}, timeout=10)
    items = val.get("items", [])
    out = []
    for s in items:
        out.append({
            "sessionId": s.get("sessionId", ""),
            "running": s.get("running", False),
            "preset": s.get("agentPreset", ""),
            "cwd": s.get("cwd", ""),
        })
    return out



# ─────────────────────────── 会话池（并行生成专用） ───────────────────────────
#
# 与单会话缓存（_get_or_create_session）完全独立，互不干扰：
#   - 单会话缓存供 delegate_task 串行复用，语义不变；
#   - 会话池供 parallel_gen 多线程并发取用：容量固定、长期复用，
#     不每任务新建会话（dsh 无 session.close RPC，新建会累积孤儿）。
#
# 池状态落盘: .dsh_workbench_pool.json（与 .dsh_workbench_session 同目录）
#   {"capacity": 4, "sessions": [sid, ...], "in_use": {sid: 时间戳}}

import threading as _threading  # noqa: E402  仅池函数使用, 不扩大上方单会话路径的导入面

_POOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dsh_workbench_pool.json")
POOL_CAPACITY = 4   # 默认容量: 实测 6 会话并发 100% 成功, 4 为稳妥的固定复用值
_POOL_LOCK = _threading.Lock()


def _pool_load():
    """读池状态文件; 不存在/损坏时返回空结构。"""
    try:
        with open(_POOL_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {"capacity": int(data.get("capacity", POOL_CAPACITY)),
                    "sessions": [s for s in data.get("sessions", []) if s],
                    "in_use": dict(data.get("in_use", {}))}
    except Exception:
        pass
    return {"capacity": POOL_CAPACITY, "sessions": [], "in_use": {}}


def _pool_save(state):
    """池状态原子落盘（先写临时文件再替换, 防并发写坏）。"""
    tmp = _POOL_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _POOL_FILE)
    except Exception:
        pass


def _pool_ensure_sessions(state, preset=DEFAULT_PRESET, cwd=None):
    """启动/取用前校验：池中会话必须仍存在于 dsh（dsh 重启会丢）,
    不存在则重建；槽位不足容量时也补齐到容量。"""
    live_ids = {s.get("sessionId") for s in list_sessions()}
    kept = []
    for sid in state["sessions"]:
        if sid in live_ids:
            kept.append(sid)
        else:
            state["in_use"].pop(sid, None)  # 会话已消失, 占用记录一并清除
    state["sessions"] = kept
    while len(state["sessions"]) < state["capacity"]:
        state["sessions"].append(create_session(preset=preset, cwd=cwd))


def acquire_session(preset=DEFAULT_PRESET, cwd=None, wait=True, wait_timeout=600, lease_timeout=1800):
    """从池中取一个空闲会话（线程安全）; 池不足容量时先补齐会话。
    全部被占用且 wait=True 时轮询等待释放; wait=False 时无空闲直接返回空串。
    lease_timeout: 占用租约上限秒数（默认 1800 = 2× 单段 timeout, 调用方可按 2×timeout 传入）;
    in_use 中超过租约的占用强制回收——防持有进程被杀后占用永久残留导致空等 600s。"""
    deadline = time.time() + wait_timeout
    while True:
        with _POOL_LOCK:
            state = _pool_load()
            _pool_ensure_sessions(state, preset=preset, cwd=cwd)
            # 租约回收: 超阈值的占用强制释放（持有进程可能已死）
            now = time.time()
            for sid0 in list(state["in_use"]):
                try:
                    expired = now - float(state["in_use"].get(sid0)) > lease_timeout
                except Exception:
                    expired = True  # 时间戳缺失/损坏 → 直接回收
                if expired:
                    state["in_use"].pop(sid0, None)
            for sid in state["sessions"]:
                if sid not in state["in_use"]:
                    state["in_use"][sid] = time.time()
                    _pool_save(state)
                    return sid
            _pool_save(state)
        if not wait or time.time() > deadline:
            return ""
        time.sleep(2)


def release_session(session_id):
    """归还会话到池中（仅清占用标记; 会话本身长期保留复用）。"""
    if not session_id:
        return
    with _POOL_LOCK:
        state = _pool_load()
        state["in_use"].pop(session_id, None)
        _pool_save(state)


def pool_status():
    """返回池状态快照 {capacity, sessions, in_use}（诊断用）。"""
    with _POOL_LOCK:
        return _pool_load()
