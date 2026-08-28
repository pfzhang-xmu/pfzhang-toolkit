import assert from 'node:assert/strict'
import { chmod, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { registerGitHubRoutes } from '../lib/routes.mjs'

async function fixture() {
  const directory = await mkdtemp(join(tmpdir(), 'dsh-github-test-'))
  const log = join(directory, 'calls.jsonl')
  const executable = join(directory, 'gh')
  await writeFile(executable, `#!/usr/bin/env node
const fs = require('node:fs')
const args = process.argv.slice(2)
let input = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', c => input += c)
process.stdin.on('end', () => {
  fs.appendFileSync(process.env.GH_TEST_LOG, JSON.stringify({ args, input }) + '\\n')
  const joined = args.join(' ')
  if (joined.includes('auth status')) return console.log(JSON.stringify({ hosts: { 'github.com': [{ active: true, login: 'tester', scopes: 'repo, workflow', gitProtocol: 'ssh' }] } }))
  if (joined.includes('auth login')) { console.error('! First copy your one-time code: AB12-CD34\\nOpen this URL to continue in your web browser: https://github.com/login/device'); return setInterval(() => {}, 1000) }
  if (joined.includes('user/repos?')) return console.log(JSON.stringify([[{ full_name: 'tester/alpha', name: 'alpha', description: 'A', private: true, updated_at: '2026-01-01', default_branch: 'main', html_url: 'https://github.com/tester/alpha', permissions: { admin: true } }]]))
  if (joined.includes('/branches?')) return console.log(JSON.stringify([{ name: 'main', protected: true }]))
  if (joined.includes('contents/README.md?')) return console.log(JSON.stringify({ name: 'README.md', path: 'README.md', sha: 'abc', size: 5, encoding: 'base64', content: Buffer.from('hello').toString('base64') }))
  if (joined.includes('/contents?')) return console.log(JSON.stringify([{ name: 'README.md', path: 'README.md', type: 'file', size: 5, sha: 'abc' }]))
  if (joined.includes('--method PUT')) return console.log(JSON.stringify({ content: { path: 'README.md' }, commit: { sha: 'commit-put' } }))
  if (joined.includes('--method DELETE')) return console.log(JSON.stringify({ commit: { sha: 'commit-delete' } }))
  console.error('unexpected args: ' + joined); process.exit(2)
})
`)
  await chmod(executable, 0o755)

  const routes = new Map()
  const effects = []
  const ctx = {
    webServer: { register(route) { routes.set(route.path, route.handler); return () => routes.delete(route.path) } },
    effect(factory) { effects.push(factory()) },
  }
  registerGitHubRoutes(ctx)
  const server = createServer((req, res) => {
    const path = new URL(req.url, 'http://127.0.0.1').pathname
    const handler = routes.get(path)
    if (!handler) { res.statusCode = 404; return res.end() }
    handler(req, res)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const base = `http://127.0.0.1:${address.port}`
  const oldPath = process.env.PATH
  const oldLog = process.env.GH_TEST_LOG
  process.env.PATH = `${directory}:${oldPath}`
  process.env.GH_TEST_LOG = log
  return {
    base, log,
    async close() {
      process.env.PATH = oldPath
      if (oldLog === undefined) delete process.env.GH_TEST_LOG
      else process.env.GH_TEST_LOG = oldLog
      for (const dispose of effects) dispose?.()
      await new Promise((resolve) => server.close(resolve))
    },
  }
}

async function json(base, path, options) {
  const response = await fetch(`${base}${path}`, options)
  return { status: response.status, body: await response.json() }
}

test('GitHub workspace routes manage repository contents without exposing credentials', async () => {
  const app = await fixture()
  try {
    const status = await json(app.base, '/github-workspace/api/status')
    assert.equal(status.status, 200)
    assert.deepEqual(status.body, { installed: true, authenticated: true, login: 'tester', scopes: ['repo', 'workflow'], gitProtocol: 'ssh' })

    const auth = await json(app.base, '/github-workspace/api/auth', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })
    assert.equal(auth.body.active, true)
    assert.equal(auth.body.code, 'AB12-CD34')
    assert.equal(auth.body.url, 'https://github.com/login/device')
    const stopped = await json(app.base, '/github-workspace/api/auth', { method: 'DELETE' })
    assert.deepEqual(stopped.body, { active: false })

    const repos = await json(app.base, '/github-workspace/api/repos')
    assert.equal(repos.body.repos[0].nameWithOwner, 'tester/alpha')
    assert.equal(repos.body.repos[0].viewerPermission, 'ADMIN')

    const root = await json(app.base, '/github-workspace/api/contents?repo=tester%2Falpha&branch=main&path=')
    assert.equal(root.body.entries[0].path, 'README.md')
    const file = await json(app.base, '/github-workspace/api/contents?repo=tester%2Falpha&branch=main&path=README.md')
    assert.equal(file.body.content, 'hello')

    const update = await json(app.base, '/github-workspace/api/file', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ repo: 'tester/alpha', branch: 'main', path: 'README.md', content: 'changed', sha: 'abc', message: 'Update README' }) })
    assert.equal(update.body.commit, 'commit-put')
    const remove = await json(app.base, '/github-workspace/api/file', { method: 'DELETE', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ repo: 'tester/alpha', branch: 'main', path: 'README.md', sha: 'abc', message: 'Delete README' }) })
    assert.equal(remove.body.commit, 'commit-delete')

    const rejected = await json(app.base, '/github-workspace/api/file', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ repo: 'tester/alpha', branch: 'main', path: '../secret', content: 'x', message: 'bad' }) })
    assert.equal(rejected.status, 500)
    assert.match(rejected.body.error, /path is invalid/i)

    const calls = (await readFile(app.log, 'utf8')).trim().split('\n').map(JSON.parse)
    assert.ok(calls.some((call) => call.args.includes('--paginate')))
    assert.ok(calls.some((call) => call.args.includes('PUT') && JSON.parse(call.input).sha === 'abc'))
    assert.ok(calls.some((call) => call.args.includes('DELETE') && JSON.parse(call.input).sha === 'abc'))
    assert.equal(calls.some((call) => /token|authorization/i.test(call.input)), false)
  } finally {
    await app.close()
  }
})
