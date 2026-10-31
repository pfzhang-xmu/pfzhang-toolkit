# Agent guide

给接手 XMU Gym 的 agent：先读这一页，再动代码或真实账号。

## 项目是什么

本机运行的厦大体育场馆预约控制台：静态网页 + Python 后端 + 预约核心。
网页是主入口；微信登录态是预约的硬前提；Hermes/微信只是可选的通知/聊天入口。

## 目录

| 路径 | 作用 |
|---|---|
| `app/server.py` | 127.0.0.1 本地 HTTP 服务、静态页面、`/api/invoke` |
| `app/backend.py` | 认证、场次、订单、取消、预约；跨进程锁和幂等标记 |
| `app/daily.py` | 每日任务策略、执行、通知文本、二维码 |
| `app/daily_prestart.py` | 06:58 入口：06:59:30 预热，07:00 执行 |
| `app/scheduler.py` | 内置调度器（页面开关；不依赖 Hermes） |
| `app/cli.py` | 命令行：auth/profiles/slots/book/orders/cancel/daily/qr/scheduler/notify |
| `app/demo.py` | 演示数据；不读微信、不提交 |
| `core/adapter.py` | 微信 profile 发现、账号指纹、每日预约适配 |
| `core/booking/` | 预约核心（会话读取、查询、提交、订单、验证码） |
| `core/platform.py` | macOS/Windows 路径、活跃 profile、防睡眠 |
| `core/locks.py` | fcntl / msvcrt 文件锁 |
| `web/` | 纯静态页面；无构建链 |
| `scripts/` | macOS/Windows 安装、启动、自启 |
| `state/` | 运行时数据；**永远不要提交** |

## 命令

```bash
# macOS
./scripts/setup-macos.sh
./scripts/start-macos.command              # 真实模式
./scripts/start-macos.command --demo --port 1431

# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
scripts\start-windows.bat
scripts\start-windows.bat --demo --port 1431

# 测试（不需要微信）
.venv/bin/python -m pytest tests -q

# 只读 CLI
.venv/bin/python -m app.cli auth
.venv/bin/python -m app.cli profiles
.venv/bin/python -m app.cli slots --date tomorrow
.venv/bin/python -m app.cli orders
```

## 硬规则

1. **不自动付款。** 看到价格/支付包就停，提示去官方小程序核对。
2. **结果不明绝不重试。** `SUBMIT_UNKNOWN`、`RESULT_UNCONFIRMED`、未核实、提交中断：先查「我的预约」，不要重复提交。
3. **账号指纹是敏感关联标识。** 不写日志、不进 README、不贴聊天；只显示脱敏尾号。
4. **入场码是凭证。** 不写日志、不发群、不提交到仓库；用完删。
5. **预约/取消是真实副作用。** CLI 必须带 `--yes`；页面提交前有确认框，取消要按住 1 秒。
6. **不要并发运行多个预约工具。** 本地锁只覆盖本项目的 `state/`。
7. **`state/`、真实截图不能进 Git。** 演示截图只用 `--demo`。
8. **跨平台改动先看 `core/platform.py` 和 `core/locks.py`。** 不要在业务代码里直接写 `fcntl`、`lsof`、Mac 路径。

## 测试要求

- 改业务逻辑：先加/改测试，再跑全量 `pytest`。
- 浏览器测试 `tests/test_daily_ui.py` 需要 Playwright；没装时跳过，不要伪造通过。
- 不联网的测试不得访问学校接口；真实预约只能由用户明确授权后手动执行。

## 代码风格

- 页面保持无构建链：`index.html` + `styles.css` + `app.js` + `catalog.js`。
- 中文文案；错误信息先讲“有没有提交”，再讲下一步。
- 只读失败和提交失败分开措辞；不要把查询失败说成“结果不明”。
