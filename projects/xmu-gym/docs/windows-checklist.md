# Windows 真机验证清单

这份清单给第一次在 Windows 上跑的人。**先只读，不要一上来就 book。**

## 0. 版本

```powershell
py -3.12 --version
```

期望：`Python 3.12.x`。微信需要 4.x（`xwechat`），3.x 的本地存储路径不同，不在支持范围。

## 1. 安装

```powershell
cd <仓库>\projects\xmu-gym
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

期望：`.venv\Scripts\python.exe` 存在，桌面出现 `XMU Gym` 快捷方式。

## 2. 先看演示模式（不读微信）

```powershell
scripts\start-windows.bat --demo --port 1431
```

打开 `http://127.0.0.1:1431/`。期望：

- 右上角有「演示数据 · 不会提交」；
- 预订页能切今天/明天/后天，场次有余量；
- 我的预约有 1 条演示订单，入场码能弹出。

Ctrl-C 停止。

## 3. 真实登录态（只读）

先在电脑微信打开「厦大体育」→ 进入一次「场馆预约」，然后：

```powershell
.venv\Scripts\python.exe -m app.cli auth
.venv\Scripts\python.exe -m app.cli profiles
.venv\Scripts\python.exe -m app.cli slots --date tomorrow --sport 健身房 --venue 翔安校区健身房
.venv\Scripts\python.exe -m app.cli orders
```

期望：

- `auth` 输出 `account_label`（形如 `账号 ····1234`），`ok: true`；
- `profiles` 至少 1 行，显示 `webview_...`、脱敏账号、票据年龄；
- `slots` 输出 7 个时段和余量；
- `orders` 输出进行中的订单，或“没有进行中的预约”。

如果 `auth` 报 `NO_ENTRY`：微信没登录/没进过场馆预约。
如果 `SESSION_READ_FAILED`：确认 `%APPDATA%\Tencent\xwechat\radium\web\profiles` 存在，且微信是 4.x。

## 4. 页面

```powershell
scripts\start-windows.bat
```

打开 `http://127.0.0.1:1420/`，期望：

- 自动核验账号，显示「已核验：账号 ····1234」；
- 设置 → 账号 → 「微信配置」能选当前配置；
- 设置 → 每日自动预约 → 能选时间、勾选备选、打开开关、保存；
- 保存后显示「已启用：每天 06:58 待命，07:00 提交」。

## 5. 调度器（不提交）

在设置里打开开关并保存后，检查：

```powershell
type state\scheduler.json
```

06:58–07:20 之外应显示 `idle` 或没有文件；到点后应有 `running` / `done` / `failed`。
**不要为了测试把系统时间改到 06:58 后真的提交**；如果要验证，先用演示模式或临时把任务设成不重要的时段。

## 6. 真实预约（确认后才做）

```powershell
.venv\Scripts\python.exe -m app.cli book --date tomorrow --start 13:30 --end 15:00 --yes
```

期望：`verified: true`。如果返回未核实/`SUBMIT_UNKNOWN`：

- **不要重试**；
- 到微信「我的预约」核对；
- 确认没有订单后再手动补。

## 7. 回报什么

出问题时把以下信息（脱敏后）发出来：

- Windows 版本、微信版本；
- 出错命令 + 完整错误码（不要发账号指纹/入场码）；
- `state\scheduler.json` 内容；
- 页面截图（演示模式或遮掉账号）。
