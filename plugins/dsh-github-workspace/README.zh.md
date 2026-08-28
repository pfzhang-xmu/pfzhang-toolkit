# DSH GitHub 工作区

[English](./README.md)

这是一个仅在本机运行的 DeepSeek Harness Web GUI GitHub 仓库工作区插件。插件通过已安装的 GitHub CLI（`gh`）完成认证和 GitHub API 调用。访问令牌保留在 GitHub CLI 的凭据存储中，绝不会返回给浏览器。

## 功能

- 显示当前 GitHub CLI 账号和已授权 scopes。
- 列出作为所有者、协作者或组织成员可访问的仓库。
- 浏览分支和仓库目录。
- 读取不超过 1 MB 的文本文件。
- 创建、修改和删除仓库文件，并附带提交说明。
- 修改或删除已有文件时携带当前 Blob SHA，避免静默覆盖并发修改。

## 安装

在 DSH Web profile 中，将插件作为本地链接依赖并加入 bundle：

```json
{
  "dependencies": {
    "dsh-github-workspace": "link:/absolute/path/to/dsh-github-workspace"
  },
  "dsh": {
    "profile": {
      "bundles": ["dsh-github-workspace"]
    }
  }
}
```

也可以通过 profile patch 加载服务端插件。该方法与 `dsh.profile.bundles` 二选一，不能同时使用，否则会产生重复插件 ID：

```yaml
- insert:
    - id: github-workspace
      name: dsh-github-workspace
```

安装并认证 GitHub CLI：

```bash
brew install gh
gh auth login --hostname github.com --web --git-protocol ssh
```

如需额外 scopes，可以执行：

```bash
gh auth refresh -h github.com -s <scope>
```

仅应在可信任的本机上授予宽泛权限。GitHub 账户和组织策略仍决定最终有效权限。

## 安全边界

- UI 和插件路由不接收 Token、SSH 私钥、密码或恢复码。
- 命令使用 `spawn(command, args, { shell: false })` 执行；仓库、分支和路径输入都经过校验。
- 浏览器只能调用固定的状态、仓库、分支、内容和文件变更路由。
- 写操作必须包含提交说明；更新和删除已有文件必须包含 GitHub Blob SHA。
- 当前 UI 不暴露仓库删除、强制推送、Secret 管理、协作者变更、工作流派发或组织管理操作。

## 验证

```bash
npm run check
npm test
curl http://127.0.0.1:3080/github-workspace/api/status
```
