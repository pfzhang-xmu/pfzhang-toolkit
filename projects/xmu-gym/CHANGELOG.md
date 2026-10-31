# Changelog

## 0.1.0 — 2026-10-31

- 首个公开版本：`projects/xmu-gym`。
- 本机页面：预订、我的预约、设置（账号 / 每日任务 / 关于）。
- macOS + Windows 共用 Python 后端；Windows 一键安装脚本、任务计划程序自启。
- 内置 06:58 / 07:00 调度器，不依赖 Hermes；页面可开关。
- 演示模式 `--demo`：假数据、假二维码，用于预览和截图。
- 可选 Hermes 通知 / 聊天控制模板（`integrations/hermes/SKILL.md`）。
- 预约核心保留跨进程锁、幂等键、结果不明不重试、取消窗口和入场码只读校验。
- 第三方声明与 vendored wheels，安装不依赖 Git。
