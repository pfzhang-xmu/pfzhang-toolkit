---
name: xmu-gym
description: Use when 用户要查厦大体育场次、预约/取消场馆、看入场码，或配置每日自动预约。
---

# XMU Gym（可选聊天入口）

这个技能只负责把用户的话翻译成 `app/cli.py` 命令；预约核心和页面在 XMU Gym 项目里。
先定位项目根目录（含 `app/cli.py` 和 `web/index.html`），以下命令都在项目根目录执行。

## 命令

```bash
.venv/bin/python -m app.cli auth
.venv/bin/python -m app.cli profiles
.venv/bin/python -m app.cli slots --date tomorrow --sport 健身房 --venue 翔安校区健身房
.venv/bin/python -m app.cli book --date tomorrow --start 13:30 --end 15:00 --yes
.venv/bin/python -m app.cli orders
.venv/bin/python -m app.cli cancel --order next --yes
.venv/bin/python -m app.cli qr --order next
.venv/bin/python -m app.cli daily show
.venv/bin/python -m app.cli daily set --start 13:30 --end 15:00 --fallback
.venv/bin/python -m app.cli scheduler enable
```

Windows 把 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。

## 对话规则

- 用户说“约明天下午健身房”，缺场馆/时段就问一个最关键的问题，不要一次问五个。
- 用户明确说了日期、开始、结束、场馆后，直接 `book --yes`；不要再让用户确认一遍。
- `book` 返回 `verified: true` 才算成功；否则只报“未核实”，让用户去微信「我的预约」核对。
- 结果不明（`SUBMIT_UNKNOWN` / 未核实 / 命令中断）**绝不重试**，也不换参数再提交。
- 取消只允许开始前 1 小时以上；先 `orders` 确认是哪一笔，再 `cancel --yes`。
- 入场码只在用户明确要看时生成；不要把 PNG 内容或路径发到群里。
- 不自动付款；显示价格不等于需要线上支付。

## 可选通知（Hermes）

内置调度器会在页面开关打开、程序运行时执行 06:58 预热 / 07:00 提交。
如果用户已经在用 Hermes，可另建一个 `no_agent` cron：

- 时间：每天 07:02
- 命令：`cd <项目根> && .venv/bin/python -m app.cli notify --latest`
- 投递：`weixin`

`notify --latest` 只打印最近一次调度结果；没有结果时退出 1，cron 不会发空消息。
也可以设置 `state/config.json` 的 `notify.webhook_url`，用 `notify --latest` 发通用 webhook。
