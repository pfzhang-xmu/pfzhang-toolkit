#!/usr/bin/env node
/**
 * CDP Proxy v2 — 连接池复用
 * 每个 tab 维护一个持久 WebSocket 连接，避免反复握手。
 */

import http from "node:http";
import { WebSocket } from "ws";

const PORT = parseInt(process.env.CDP_PROXY_PORT || "3456", 10);
const BROWSER = `http://127.0.0.1:${process.env.CDP_BROWSER_PORT || "9222"}`;

// ---- 连接池 ----
const pool = new Map();  // targetId → { ws, pending, ref }
let seq = 0;

async function fetchJson(url, opts = {}) {
  const resp = await fetch(url, {
    ...opts,
    signal: AbortSignal.timeout(opts.timeoutMs || 15000),
  });
  const text = await resp.text();
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${text.slice(0, 300)}`);
  return JSON.parse(text);
}

async function acquireWS(targetId) {
  // 检查缓存
  const cached = pool.get(targetId);
  if (cached && cached.ws.readyState === WebSocket.OPEN) {
    cached.ref = Date.now();
    return cached;
  }

  // 获取最新 WebSocket URL
  const targets = await fetchJson(`${BROWSER}/json`);
  const info = targets.find((t) => t.id === targetId);
  if (!info?.webSocketDebuggerUrl) {
    throw new Error(`Target ${targetId} unreachable`);
  }

  // 建立新连接
  let conn;
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(info.webSocketDebuggerUrl);
    const pending = new Map();
    const timer = setTimeout(() => { ws.close(); reject(new Error("WS connect timeout")); }, 10000);

    ws.on("open", () => {
      clearTimeout(timer);
      conn = { ws, pending, ref: Date.now() };
      pool.set(targetId, conn);
      resolve(conn);
    });

    ws.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.id && conn?.pending.has(msg.id)) {
          const entry = conn.pending.get(msg.id);
          clearTimeout(entry.timer);
          conn.pending.delete(msg.id);
          if (msg.error) entry.reject(new Error(msg.error.message));
          else entry.resolve(msg.result || {});
        }
      } catch (_) {}
    });

    ws.on("error", (e) => { clearTimeout(timer); pool.delete(targetId); reject(e); });
    ws.on("close", () => { pool.delete(targetId); });

    let conn;
  });
}

function send(targetId, method, params = {}, timeoutMs = 30000) {
  return acquireWS(targetId).then((conn) => {
    return new Promise((resolve, reject) => {
      const id = ++seq;
      const timer = setTimeout(() => {
        conn.pending.delete(id);
        reject(new Error(`CDP ${method} timeout`));
      }, timeoutMs);
      conn.pending.set(id, { resolve, reject, timer });
      conn.ws.send(JSON.stringify({ id, method, params }));
    });
  });
}

// 定期清理空闲超过 2 分钟的连接
setInterval(() => {
  const now = Date.now();
  for (const [id, c] of pool) {
    if (now - c.ref > 120000) { try { c.ws.close(); } catch (_) {} pool.delete(id); }
  }
}, 30000);

// ---- HTTP 端点 ----

async function handleNew(url) {
  const target = await fetchJson(
    `${BROWSER}/json/new?${new URLSearchParams({ url: url || "about:blank" })}`,
    { method: "PUT" }
  );
  return { targetId: target.id, ...target };
}

async function handleNavigate(targetId, url) {
  return send(targetId, "Page.navigate", { url }, 30000);
}

async function handleEval(targetId, expression) {
  const result = await send(targetId, "Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  }, 120000);
  return result.result || {};
}

async function handleInfo(targetId) {
  try {
    const result = await send(targetId, "Runtime.evaluate", {
      expression: `(function(){return{title:document.title,url:location.href,ready:document.readyState}})()`,
      returnByValue: true,
      awaitPromise: false,
    }, 10000);
    const val = result.result?.value;
    if (val) return { targetId, ...val };
  } catch (_) {}
  const targets = await fetchJson(`${BROWSER}/json`);
  const info = targets.find((t) => t.id === targetId);
  return { targetId, title: info?.title || "", url: info?.url || "", ready: "unknown" };
}

async function handleClose(targetId) {
  try { await send(targetId, "Page.close", {}, 10000); } catch (_) {}
  try { await fetchJson(`${BROWSER}/json/close/${targetId}`); } catch (_) {}
  pool.delete(targetId);
  return { closed: true };
}

// ---- Server ----
const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://localhost");
  const p = u.pathname;
  const q = Object.fromEntries(u.searchParams);

  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

  const json = (code, data) => {
    res.writeHead(code, { "Content-Type": "application/json" });
    res.end(JSON.stringify(data));
  };

  try {
    if (p === "/health")               json(200, { ok: true, conns: pool.size });
    else if (p === "/targets")         json(200, await fetchJson(`${BROWSER}/json`));
    else if (p === "/new")             json(200, await handleNew(q.url));
    else if (p === "/navigate")        json(200, await handleNavigate(q.target, q.url));
    else if (p === "/info")            json(200, await handleInfo(q.target));
    else if (p === "/close")           json(200, await handleClose(q.target));
    else if (p === "/eval" && req.method === "POST") {
      const body = await new Promise((r) => { let b = ""; req.on("data", (c) => b += c); req.on("end", () => r(b)); });
      json(200, await handleEval(q.target, body));
    }
    else json(404, { error: "not found" });
  } catch (err) {
    json(500, { error: err.message });
  }
});

server.listen(PORT, () => console.log(`CDP proxy v2 on http://127.0.0.1:${PORT}  |  browser: ${BROWSER}`));
