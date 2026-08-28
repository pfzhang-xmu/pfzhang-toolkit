# DSH GitHub Workspace

[中文文档](./README.zh.md)

A local-only GitHub repository workspace for the DeepSeek Harness Web GUI. The plugin uses the installed GitHub CLI (`gh`) for authentication and GitHub API calls. Tokens remain in GitHub CLI's credential store and are never returned to the browser.

## Features

- Display the active GitHub CLI account and granted scopes.
- List every repository accessible as owner, collaborator, or organization member.
- Browse branches and repository directories.
- Read text files up to 1 MB.
- Create, update, and delete repository files with a commit message.
- Include the current blob SHA on updates and deletes to prevent silent concurrent overwrite.

## Install

Add the package as a linked dependency and bundle in the DSH Web profile:

```json
{
  "dependencies": {
    "dsh-github-workspace": "link:/absolute/path/to/github-plugin"
  },
  "dsh": {
    "profile": {
      "bundles": ["dsh-github-workspace"]
    }
  }
}
```

Alternatively, add the server plugin from the profile patch. Use this method **instead of** adding it to `dsh.profile.bundles`; do not load the plugin both ways:

```yaml
- insert:
    - id: github-workspace
      name: dsh-github-workspace
```

Install and authenticate GitHub CLI:

```bash
brew install gh
gh auth login --hostname github.com --web --git-protocol ssh
```

Additional scopes can be requested with `gh auth refresh -h github.com -s <scope>`. Broad access should only be granted on a trusted local machine. The GitHub account and organization policies still determine the effective permissions.

## Security boundaries

- No token, SSH private key, password, or recovery code is accepted by the UI or plugin routes.
- Commands are executed with `spawn(command, args, { shell: false })`; repository, branch, and path inputs are validated.
- The browser can only invoke the fixed status, repository, branch, contents, and file mutation routes.
- Mutating calls require a commit message. Existing file updates and deletes require the GitHub blob SHA.
- Repository deletion, force push, secret management, collaborator changes, workflow dispatch, and organization administration are intentionally not exposed by this first UI.

## Verify

```bash
npm run check
npm test
curl http://127.0.0.1:3080/github-workspace/api/status
```
