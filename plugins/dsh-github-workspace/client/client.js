window.__ModuleLoader__.load({
  id: 'dsh-github-workspace',
  factory: (require) => {
    const React = require('react')
    const { useCallback, useEffect, useMemo, useState } = React
    const h = React.createElement
    const STORAGE_KEY = 'dsh.github.workspace.open'

    const colors = {
      bg: 'var(--dsw-alias-bg-layer-2, #202124)', panel: 'var(--dsw-alias-bg-layer-1, #17181a)',
      border: 'var(--dsw-alias-border-l1, rgba(255,255,255,.14))', text: 'var(--dsw-alias-label-primary, #eee)',
      muted: 'var(--dsw-alias-label-secondary, #a8abb2)', accent: 'var(--dsw-alias-state-business-primary, #4c9aff)',
      soft: 'var(--dsw-alias-state-business-primary-soft, rgba(76,154,255,.14))', danger: '#ef6b73', success: '#4fb286',
    }

    const button = (primary = false, danger = false) => ({
      border: `1px solid ${danger ? colors.danger : primary ? colors.accent : colors.border}`,
      background: primary ? colors.accent : 'transparent', color: primary ? '#fff' : danger ? colors.danger : colors.text,
      borderRadius: '6px', padding: '6px 10px', cursor: 'pointer', font: 'inherit', fontSize: '12px', minHeight: '30px',
    })
    const input = { width: '100%', boxSizing: 'border-box', border: `1px solid ${colors.border}`, borderRadius: '6px', background: colors.panel, color: colors.text, padding: '7px 9px', font: 'inherit', fontSize: '12px' }

    async function api(path, options) {
      const response = await fetch(`/github-workspace/api${path}`, { ...options, headers: { 'content-type': 'application/json', ...(options?.headers ?? {}) } })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`)
      return data
    }

    function Workspace() {
      const [status, setStatus] = useState(null)
      const [repos, setRepos] = useState([])
      const [repo, setRepo] = useState('')
      const [branches, setBranches] = useState([])
      const [branch, setBranch] = useState('')
      const [path, setPath] = useState('')
      const [entries, setEntries] = useState([])
      const [file, setFile] = useState(null)
      const [content, setContent] = useState('')
      const [message, setMessage] = useState('Update file from DSH')
      const [newPath, setNewPath] = useState('')
      const [busy, setBusy] = useState(false)
      const [notice, setNotice] = useState(null)
      const [auth, setAuth] = useState(null)

      const run = useCallback(async (task) => {
        setBusy(true); setNotice(null)
        try { return await task() } catch (error) { setNotice({ error: true, text: error.message }); return null } finally { setBusy(false) }
      }, [])

      const refreshStatus = useCallback(() => run(async () => {
        const next = await api('/status'); setStatus(next)
        if (next.authenticated) { const data = await api('/repos'); setRepos(data.repos) }
        return next
      }), [run])

      useEffect(() => { refreshStatus() }, [refreshStatus])
      useEffect(() => {
        if (!auth?.active) return
        const timer = window.setInterval(async () => {
          try {
            const next = await api('/auth')
            setAuth(next)
            if (next.authenticated) refreshStatus()
          } catch {}
        }, 2000)
        return () => window.clearInterval(timer)
      }, [auth?.active, refreshStatus])

      const startAuth = useCallback(() => run(async () => {
        const next = await api('/auth', { method: 'POST', body: '{}' })
        setAuth(next)
        if (next.url) window.open(next.url, '_blank', 'noopener,noreferrer')
      }), [run])

      const cancelAuth = useCallback(() => run(async () => {
        await api('/auth', { method: 'DELETE' })
        setAuth(null)
      }), [run])

      const chooseRepo = useCallback((value) => run(async () => {
        setRepo(value); setPath(''); setFile(null); setEntries([])
        if (!value) return
        const data = await api(`/branches?repo=${encodeURIComponent(value)}`)
        setBranches(data.branches)
        const selected = repos.find((item) => item.nameWithOwner === value)?.defaultBranchRef?.name || data.branches[0]?.name || ''
        setBranch(selected)
        if (selected) {
          const root = await api(`/contents?repo=${encodeURIComponent(value)}&branch=${encodeURIComponent(selected)}&path=`)
          setEntries(root.entries || [])
        }
      }), [repos, run])

      const openPath = useCallback((nextPath, selectedBranch = branch) => run(async () => {
        const data = await api(`/contents?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(selectedBranch)}&path=${encodeURIComponent(nextPath)}`)
        if (data.type === 'directory') { setPath(nextPath); setEntries(data.entries); setFile(null) }
        else { setFile(data); setContent(data.content || ''); setNewPath(data.path) }
      }), [repo, branch, run])

      const changeBranch = useCallback((value) => {
        setBranch(value); setPath(''); setFile(null); setEntries([])
        if (value) openPath('', value)
      }, [openPath])

      const save = useCallback(() => run(async () => {
        const target = newPath.trim()
        if (!target) throw new Error('请输入仓库内文件路径。')
        const result = await api('/file', { method: 'PUT', body: JSON.stringify({ repo, branch, path: target, content, sha: file?.path === target ? file.sha : undefined, message }) })
        setNotice({ text: `已提交 ${target} (${result.commit?.slice(0, 7) || 'commit'})` })
        await openPath(target)
      }), [repo, branch, newPath, content, file, message, openPath, run])

      const remove = useCallback(() => run(async () => {
        if (!file || !window.confirm(`确认从 ${repo}@${branch} 删除 ${file.path}？`)) return
        const deletedPath = file.path
        await api('/file', { method: 'DELETE', body: JSON.stringify({ repo, branch, path: deletedPath, sha: file.sha, message: message || `Delete ${deletedPath}` }) })
        setNotice({ text: `已删除并提交 ${deletedPath}` }); setFile(null); setContent(''); setNewPath('')
        await openPath(path)
      }), [file, repo, branch, message, path, openPath, run])

      if (status === null) return h('div', { style: { padding: '24px', color: colors.muted } }, '正在检查 GitHub CLI...')
      if (!status.installed) return h('div', { style: { padding: '24px', color: colors.text } }, [h('h3', { key: 'h', style: { margin: '0 0 8px', fontSize: '15px' } }, '需要安装 GitHub CLI'), h('code', { key: 'c' }, 'brew install gh'), h('button', { key: 'r', style: { ...button(), marginLeft: '12px' }, onClick: refreshStatus }, '重新检查')])
      if (!status.authenticated) return h('div', { style: { padding: '24px', color: colors.text, display: 'grid', gap: '12px', maxWidth: '520px' } }, [
        h('h3', { key: 'h', style: { margin: 0, fontSize: '15px' } }, '连接 GitHub 账号'),
        h('div', { key: 'd', style: { color: colors.muted, fontSize: '12px' } }, '授权在 GitHub 官方页面完成。DSH 不读取、显示或保存访问令牌。'),
        auth?.code ? h('div', { key: 'code', style: { display: 'grid', gap: '7px', padding: '12px', background: colors.panel, border: `1px solid ${colors.border}` } }, [h('span', { key: 'l', style: { color: colors.muted, fontSize: '11px' } }, '一次性设备码'), h('strong', { key: 'v', style: { fontFamily: 'var(--ds-font-family-code, ui-monospace, monospace)', fontSize: '22px', letterSpacing: '2px' } }, auth.code), h('a', { key: 'a', href: auth.url || 'https://github.com/login/device', target: '_blank', rel: 'noreferrer', style: { color: colors.accent, fontSize: '12px' } }, '打开 GitHub 授权页面')]) : null,
        h('div', { key: 'actions', style: { display: 'flex', gap: '8px' } }, [
          auth?.active ? h('button', { key: 'cancel', style: button(false, true), onClick: cancelAuth, disabled: busy }, '取消授权') : h('button', { key: 'start', style: button(true), onClick: startAuth, disabled: busy }, '开始 GitHub 授权'),
          h('button', { key: 'r', style: button(), onClick: refreshStatus, disabled: busy }, '重新检查'),
        ]),
        notice ? h('div', { key: 'n', style: { color: colors.danger } }, notice.text) : null,
      ])

      const crumbs = path ? path.split('/').reduce((all, part, index, source) => [...all, { label: part, path: source.slice(0, index + 1).join('/') }], [{ label: '根目录', path: '' }]) : [{ label: '根目录', path: '' }]
      return h('div', { style: { height: '100%', display: 'grid', gridTemplateRows: 'auto minmax(0,1fr)', color: colors.text } }, [
        h('div', { key: 'top', style: { display: 'flex', alignItems: 'center', gap: '8px', padding: '9px 12px', borderBottom: `1px solid ${colors.border}` } }, [
          h('span', { key: 'u', style: { fontWeight: 600, fontSize: '12px' } }, `@${status.login}`),
          h('select', { key: 'repo', value: repo, onChange: (e) => chooseRepo(e.target.value), style: { ...input, width: '220px' } }, [h('option', { key: '', value: '' }, '选择仓库'), ...repos.map((item) => h('option', { key: item.nameWithOwner, value: item.nameWithOwner }, `${item.nameWithOwner}${item.isPrivate ? ' (私有)' : ''}`))]),
          h('select', { key: 'branch', value: branch, onChange: (e) => changeBranch(e.target.value), disabled: !repo, style: { ...input, width: '150px' } }, branches.map((item) => h('option', { key: item.name, value: item.name }, item.name))),
          h('span', { key: 'scope', style: { marginLeft: 'auto', color: colors.muted, fontSize: '11px' } }, status.scopes?.join(', ') || 'GitHub CLI'),
          h('button', { key: 'refresh', style: button(), title: '刷新账号与仓库', onClick: refreshStatus, disabled: busy }, '刷新'),
        ]),
        repo ? h('div', { key: 'body', style: { minHeight: 0, display: 'grid', gridTemplateColumns: '280px minmax(0,1fr)' } }, [
          h('div', { key: 'tree', style: { borderRight: `1px solid ${colors.border}`, overflow: 'auto' } }, [
            h('div', { key: 'crumb', style: { padding: '8px', borderBottom: `1px solid ${colors.border}`, display: 'flex', gap: '4px', flexWrap: 'wrap' } }, crumbs.map((crumb, index) => h('button', { key: crumb.path, style: { ...button(), padding: '2px 5px', minHeight: '24px' }, onClick: () => openPath(crumb.path) }, `${index ? '/' : ''}${crumb.label}`))),
            ...entries.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1)).map((entry) => h('button', { key: entry.path, onClick: () => openPath(entry.path), style: { width: '100%', display: 'flex', gap: '7px', alignItems: 'center', border: 0, borderBottom: `1px solid ${colors.border}`, background: file?.path === entry.path ? colors.soft : 'transparent', color: colors.text, padding: '8px 10px', cursor: 'pointer', textAlign: 'left', font: 'inherit', fontSize: '12px' } }, [h('span', { key: 'i' }, entry.type === 'dir' ? '▸' : '·'), h('span', { key: 'n', style: { overflow: 'hidden', textOverflow: 'ellipsis' } }, entry.name)])),
          ]),
          h('div', { key: 'editor', style: { minWidth: 0, minHeight: 0, display: 'grid', gridTemplateRows: 'auto minmax(140px,1fr) auto', padding: '12px', gap: '9px' } }, [
            h('div', { key: 'fields', style: { display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(180px,.6fr)', gap: '8px' } }, [h('input', { key: 'path', value: newPath, placeholder: '例如 docs/README.md', onChange: (e) => { setNewPath(e.target.value); if (file && e.target.value !== file.path) setFile(null) }, style: input }), h('input', { key: 'msg', value: message, placeholder: 'Commit message', onChange: (e) => setMessage(e.target.value), style: input })]),
            file?.binary ? h('div', { key: 'binary', style: { display: 'grid', placeItems: 'center', color: colors.muted, border: `1px solid ${colors.border}` } }, `二进制文件或超过 1 MB，当前编辑器不加载内容 (${file.size} bytes)`) : h('textarea', { key: 'content', value: content, onChange: (e) => setContent(e.target.value), placeholder: '选择文本文件，或输入新文件路径后创建内容', spellCheck: false, style: { ...input, resize: 'none', minHeight: 0, fontFamily: 'var(--ds-font-family-code, ui-monospace, monospace)', lineHeight: 1.55 } }),
            h('div', { key: 'actions', style: { display: 'flex', alignItems: 'center', gap: '8px' } }, [h('button', { key: 'new', style: button(), onClick: () => { setFile(null); setNewPath(path ? `${path}/` : ''); setContent(''); setMessage('Create file from DSH') } }, '新建文件'), h('button', { key: 'save', style: button(true), onClick: save, disabled: busy || !newPath || file?.binary }, file ? '提交修改' : '创建并提交'), file ? h('button', { key: 'delete', style: button(false, true), onClick: remove, disabled: busy }, '删除并提交') : null, notice ? h('span', { key: 'notice', style: { marginLeft: 'auto', color: notice.error ? colors.danger : colors.success, fontSize: '12px' } }, notice.text) : null]),
          ]),
        ]) : h('div', { key: 'empty', style: { display: 'grid', placeItems: 'center', color: colors.muted, fontSize: '13px' } }, '选择一个仓库开始管理文件。'),
      ])
    }

    function GitHubPanel() {
      const [open, setOpen] = useState(() => window.localStorage.getItem(STORAGE_KEY) === 'true')
      useEffect(() => { window.localStorage.setItem(STORAGE_KEY, String(open)) }, [open])
      const close = () => {
        window.localStorage.setItem(STORAGE_KEY, 'false')
        setOpen(false)
        window.dispatchEvent(new CustomEvent('dsh-github-toggle', { detail: false }))
      }
      if (!open) return null
      return h('div', { style: { position: 'fixed', zIndex: 2147482980, right: '18px', top: '58px', width: 'min(900px, calc(100vw - 36px))', height: 'min(680px, calc(100vh - 82px))', background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: '8px', boxShadow: '0 18px 48px rgba(0,0,0,.42)', overflow: 'hidden', display: 'grid', gridTemplateRows: '38px minmax(0,1fr)', fontFamily: 'inherit' } }, [
        h('div', { key: 'bar', style: { display: 'flex', alignItems: 'center', padding: '0 10px', borderBottom: `1px solid ${colors.border}`, color: colors.text } }, [h('strong', { key: 'title', style: { fontSize: '13px' } }, 'GitHub 工作区'), h('span', { key: 'desc', style: { marginLeft: '8px', color: colors.muted, fontSize: '11px' } }, '本机 gh + GitHub API'), h('button', { key: 'close', onClick: close, title: '关闭', style: { marginLeft: 'auto', border: 0, background: 'transparent', color: colors.muted, cursor: 'pointer', fontSize: '18px' } }, '×')]),
        h(Workspace, { key: 'workspace' }),
      ])
    }

    function Toggle() {
      const [open, setOpen] = useState(() => window.localStorage.getItem(STORAGE_KEY) === 'true')
      useEffect(() => {
        const listener = (event) => setOpen(event.detail === true)
        window.addEventListener('dsh-github-toggle', listener)
        return () => window.removeEventListener('dsh-github-toggle', listener)
      }, [])
      const toggle = () => { const next = window.localStorage.getItem(STORAGE_KEY) !== 'true'; window.localStorage.setItem(STORAGE_KEY, String(next)); setOpen(next); window.dispatchEvent(new CustomEvent('dsh-github-toggle', { detail: next })) }
      return h('button', { onClick: toggle, title: '打开 GitHub 工作区', style: { ...button(), borderColor: open ? colors.accent : colors.border, color: open ? colors.accent : colors.muted } }, 'GitHub')
    }

    function PanelRoot() {
      const [version, setVersion] = useState(0)
      useEffect(() => { const listener = () => setVersion((value) => value + 1); window.addEventListener('dsh-github-toggle', listener); return () => window.removeEventListener('dsh-github-toggle', listener) }, [])
      return h(GitHubPanel, { key: version })
    }

    function apply(ctx) {
      return [
        ctx.slots.inject('shell.overlay', () => ctx.slots.register({ name: 'shell.overlay', id: 'dsh-github-workspace-panel' }, () => h(PanelRoot))),
        ctx.slots.inject('conversation.input.right', () => ctx.slots.register({ name: 'conversation.input.right', id: 'dsh-github-workspace-toggle' }, () => h(Toggle))),
      ]
    }

    return { name: 'dsh-github-workspace-client', inject: ['slots'], apply }
  },
})
