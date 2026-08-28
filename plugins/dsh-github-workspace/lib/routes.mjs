import { spawn } from 'node:child_process'

const MAX_BODY_BYTES = 2 * 1024 * 1024
const MAX_TEXT_BYTES = 1024 * 1024
const REPO_NAME = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
const BRANCH_NAME = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/
const FILE_PATH = /^(?!\/)(?!.*(?:^|\/)\.\.(?:\/|$))[A-Za-z0-9._/@+=,-]+(?:\/[A-Za-z0-9._@+=,-]+)*$/

function sendJson(res, statusCode, payload) {
  res.statusCode = statusCode
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(JSON.stringify(payload))
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.setEncoding('utf8')
    req.on('data', (chunk) => {
      body += chunk
      if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) {
        reject(new Error('Request body is too large.'))
        req.destroy()
      }
    })
    req.on('end', () => resolve(body))
    req.on('error', reject)
  })
}

function requireString(value, label, maxLength = 500) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${label} is required.`)
  const result = value.trim()
  if (result.length > maxLength) throw new Error(`${label} is too long.`)
  return result
}

function validRepo(value) {
  const repo = requireString(value, 'Repository', 240)
  if (!REPO_NAME.test(repo)) throw new Error('Repository must be in owner/repository format.')
  return repo
}

function validBranch(value) {
  const branch = requireString(value, 'Branch', 255)
  if (!BRANCH_NAME.test(branch) || branch.includes('..') || branch.endsWith('/') || branch.endsWith('.lock')) {
    throw new Error('Branch name is invalid.')
  }
  return branch
}

function validPath(value) {
  const path = requireString(value, 'File path', 1024)
  if (!FILE_PATH.test(path)) throw new Error('File path is invalid.')
  return path
}

function text(value, label, maxLength = MAX_TEXT_BYTES) {
  if (typeof value !== 'string') throw new Error(`${label} must be text.`)
  if (Buffer.byteLength(value, 'utf8') > maxLength) throw new Error(`${label} exceeds the ${maxLength}-byte limit.`)
  return value
}

function run(command, args, { input, signal, timeoutMs = 30_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      shell: false,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, GH_PAGER: 'cat', GIT_PAGER: 'cat', PAGER: 'cat' },
    })
    const stdout = []
    const stderr = []
    let settled = false
    const finish = (fn, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      fn(value)
    }
    const onAbort = () => {
      child.kill('SIGTERM')
      finish(reject, new Error('Request cancelled.'))
    }
    const timer = setTimeout(() => {
      child.kill('SIGTERM')
      finish(reject, new Error(`${command} command timed out.`))
    }, timeoutMs)
    signal?.addEventListener('abort', onAbort, { once: true })
    child.stdout.on('data', (chunk) => stdout.push(chunk))
    child.stderr.on('data', (chunk) => stderr.push(chunk))
    child.on('error', (error) => finish(reject, error))
    child.on('close', (code) => {
      const out = Buffer.concat(stdout).toString('utf8')
      const err = Buffer.concat(stderr).toString('utf8').trim()
      if (code === 0) finish(resolve, out)
      else finish(reject, new Error(err || `${command} exited with code ${code}.`))
    })
    if (input !== undefined) child.stdin.end(input)
    else child.stdin.end()
  })
}

async function gh(args, options) {
  try {
    return await run('gh', args, options)
  } catch (error) {
    if (error?.code === 'ENOENT') throw new Error('GitHub CLI is not installed. Install it with: brew install gh')
    throw error
  }
}

async function ghJson(args, options) {
  const output = await gh(args, options)
  try {
    return JSON.parse(output)
  } catch {
    throw new Error('GitHub CLI returned invalid JSON.')
  }
}

async function accountStatus(signal) {
  try {
    const accounts = await ghJson(['auth', 'status', '--hostname', 'github.com', '--json', 'hosts'], { signal })
    const host = accounts.hosts?.['github.com']
    if (!host?.length) return { installed: true, authenticated: false }
    const active = host.find((account) => account.active) ?? host[0]
    const scopes = Array.isArray(active.scopes)
      ? active.scopes
      : typeof active.scopes === 'string'
        ? active.scopes.split(',').map((scope) => scope.trim()).filter(Boolean)
        : []
    return {
      installed: true,
      authenticated: true,
      login: active.login,
      scopes,
      gitProtocol: active.gitProtocol ?? active.git_protocol,
    }
  } catch (error) {
    if (/not installed/i.test(error.message)) return { installed: false, authenticated: false, error: error.message }
    if (/not logged into|not logged in|not authenticated|authenticate/i.test(error.message)) return { installed: true, authenticated: false }
    throw error
  }
}

function decodeContent(encoded) {
  if (typeof encoded !== 'string') return ''
  return Buffer.from(encoded.replace(/\n/g, ''), 'base64').toString('utf8')
}

let authorization = null

function authorizationState() {
  if (!authorization) return { active: false }
  return {
    active: true,
    ...(authorization.code ? { code: authorization.code } : {}),
    ...(authorization.url ? { url: authorization.url } : {}),
    startedAt: authorization.startedAt,
  }
}

function beginAuthorization() {
  if (authorization) return authorizationState()
  const child = spawn('gh', ['auth', 'login', '--hostname', 'github.com', '--web', '--git-protocol', 'ssh'], {
    shell: false,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, GH_PAGER: 'cat', GIT_PAGER: 'cat', PAGER: 'cat' },
  })
  authorization = { child, code: null, url: null, startedAt: new Date().toISOString() }
  const consume = (chunk) => {
    const output = String(chunk)
    const code = output.match(/one-time code:\s*([A-Z0-9-]+)/i)?.[1]
    const url = output.match(/https:\/\/github\.com\/login\/device/i)?.[0]
    if (code) authorization.code = code
    if (url) authorization.url = url
  }
  child.stdout.on('data', consume)
  child.stderr.on('data', consume)
  child.on('error', () => { authorization = null })
  child.on('close', () => { authorization = null })
  return authorizationState()
}

function cancelAuthorization() {
  if (authorization?.child && !authorization.child.killed) authorization.child.kill('SIGTERM')
  authorization = null
}

function register(ctx) {
  const route = (path, handler) => {
    ctx.effect(() => ctx.webServer.register({ kind: 'exact', path, handler }), `github-workspace: ${path}`)
  }

  route('/github-workspace/api/status', async (req, res) => {
    if (req.method !== 'GET') return sendJson(res, 405, { error: 'Method not allowed.' })
    try {
      sendJson(res, 200, await accountStatus(req.signal))
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/github-workspace/api/auth', async (req, res) => {
    try {
      if (req.method === 'POST') {
        beginAuthorization()
        await new Promise((resolve) => setTimeout(resolve, 250))
        return sendJson(res, 200, authorizationState())
      }
      if (req.method === 'GET') {
        const status = await accountStatus(req.signal)
        return sendJson(res, 200, { ...authorizationState(), authenticated: status.authenticated, ...(status.login ? { login: status.login } : {}) })
      }
      if (req.method === 'DELETE') {
        cancelAuthorization()
        return sendJson(res, 200, { active: false })
      }
      sendJson(res, 405, { error: 'Method not allowed.' })
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/github-workspace/api/repos', async (req, res) => {
    if (req.method !== 'GET') return sendJson(res, 405, { error: 'Method not allowed.' })
    try {
      const status = await accountStatus(req.signal)
      if (!status.authenticated) return sendJson(res, 401, { error: 'Connect GitHub CLI first.' })
      const pages = await ghJson(['api', '--paginate', '--slurp', 'user/repos?affiliation=owner,collaborator,organization_member&sort=updated&per_page=100'], { signal: req.signal })
      const repos = pages.flat().map((item) => ({
        nameWithOwner: item.full_name,
        name: item.name,
        description: item.description,
        isPrivate: item.private,
        updatedAt: item.updated_at,
        defaultBranchRef: item.default_branch ? { name: item.default_branch } : null,
        url: item.html_url,
        viewerPermission: item.permissions?.admin ? 'ADMIN' : item.permissions?.maintain ? 'MAINTAIN' : item.permissions?.push ? 'WRITE' : item.permissions?.triage ? 'TRIAGE' : 'READ',
      }))
      sendJson(res, 200, { login: status.login, repos })
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/github-workspace/api/branches', async (req, res) => {
    if (req.method !== 'GET') return sendJson(res, 405, { error: 'Method not allowed.' })
    try {
      const url = new URL(req.url, 'http://127.0.0.1')
      const repo = validRepo(url.searchParams.get('repo'))
      const branches = await ghJson(['api', `repos/${repo}/branches?per_page=100`], { signal: req.signal })
      sendJson(res, 200, { branches: branches.map((branch) => ({ name: branch.name, protected: branch.protected })) })
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/github-workspace/api/contents', async (req, res) => {
    if (req.method !== 'GET') return sendJson(res, 405, { error: 'Method not allowed.' })
    try {
      const url = new URL(req.url, 'http://127.0.0.1')
      const repo = validRepo(url.searchParams.get('repo'))
      const branch = validBranch(url.searchParams.get('branch'))
      const path = url.searchParams.get('path') ?? ''
      if (path !== '' && !FILE_PATH.test(path)) throw new Error('File path is invalid.')
      const apiPath = `repos/${repo}/contents${path ? `/${encodeURIComponent(path).replace(/%2F/g, '/')}` : ''}?ref=${encodeURIComponent(branch)}`
      const data = await ghJson(['api', apiPath], { signal: req.signal })
      if (Array.isArray(data)) {
        sendJson(res, 200, { type: 'directory', entries: data.map(({ name, path: entryPath, type, size, sha }) => ({ name, path: entryPath, type, size, sha })) })
      } else {
        const isText = data.encoding === 'base64' && Number(data.size ?? 0) <= MAX_TEXT_BYTES
        sendJson(res, 200, { type: 'file', name: data.name, path: data.path, sha: data.sha, size: data.size, content: isText ? decodeContent(data.content) : null, binary: !isText })
      }
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/github-workspace/api/file', async (req, res) => {
    if (!['PUT', 'DELETE'].includes(req.method)) return sendJson(res, 405, { error: 'Method not allowed.' })
    try {
      const body = JSON.parse(await readRequestBody(req))
      const repo = validRepo(body.repo)
      const branch = validBranch(body.branch)
      const path = validPath(body.path)
      const message = requireString(body.message, 'Commit message', 500)
      const target = `repos/${repo}/contents/${encodeURIComponent(path).replace(/%2F/g, '/')}`
      if (req.method === 'PUT') {
        const content = text(body.content, 'Content')
        const payload = { message, content: Buffer.from(content, 'utf8').toString('base64'), branch }
        if (typeof body.sha === 'string' && body.sha.length > 0) payload.sha = body.sha
        const result = await ghJson(['api', '--method', 'PUT', target, '--input', '-'], { input: JSON.stringify(payload), signal: req.signal })
        return sendJson(res, 200, { ok: true, commit: result.commit?.sha, path: result.content?.path })
      }
      const sha = requireString(body.sha, 'File SHA', 100)
      const result = await ghJson(['api', '--method', 'DELETE', target, '--input', '-'], { input: JSON.stringify({ message, sha, branch }), signal: req.signal })
      sendJson(res, 200, { ok: true, commit: result.commit?.sha, path })
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })
}

export function registerGitHubRoutes(ctx) {
  register(ctx)
  ctx.effect(() => () => cancelAuthorization(), 'github-workspace: stop authorization on dispose')
}
