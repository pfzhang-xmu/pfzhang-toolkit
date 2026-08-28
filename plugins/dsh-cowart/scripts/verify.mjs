#!/usr/bin/env node
/**
 * dsh-cowart pre-flight check.
 *
 * Verifies every piece the plugin needs, with or without a running DSH web:
 *   node scripts/verify.mjs                # filesystem + profile checks
 *   node scripts/verify.mjs --port 3080    # also probe the live web instance
 *
 * Exit code 0 = all good, 1 = problems found.
 */
import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..')
const failures = []
const ok = (label) => console.log(`  ✅ ${label}`)
const fail = (label, detail) => {
  failures.push(label)
  console.log(`  ❌ ${label}${detail ? ` — ${detail}` : ''}`)
}

console.log('dsh-cowart 预检\n')

console.log('· 插件文件')
const distIndex = join(root, 'dist', 'cowart', 'index.html')
existsSync(distIndex) ? ok('画布构建产物 dist/cowart/index.html') : fail('画布构建产物缺失', '运行 npm run build:canvas')
existsSync(join(root, 'client', 'client.js')) ? ok('客户端插件 client/client.js') : fail('客户端插件缺失')
existsSync(join(root, 'node_modules', 'tldraw', 'package.json')) ? ok('运行时依赖 tldraw') : fail('运行时依赖 tldraw 缺失', 'cd cowart-plugin && npm install')
existsSync(join(root, 'node_modules', 'fractional-indexing', 'package.json')) ? ok('运行时依赖 fractional-indexing') : fail('运行时依赖 fractional-indexing 缺失')

console.log('\n· web profile 组合')
const profileDir = join(homedir(), '.dsh', 'profiles', 'web')
const pkgPath = join(profileDir, 'package.json')
let profileOk = false
try {
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'))
  const bundles = pkg.dsh?.profile?.bundles ?? []
  if (bundles.includes('dsh-cowart')) {
    ok('bundles 列表包含 dsh-cowart')
    profileOk = true
  } else {
    fail('bundles 列表缺少 dsh-cowart', '运行 dsh plugin --profile web add link:…')
  }
  if (pkg.dependencies?.['dsh-cowart']) ok('dependencies 包含 dsh-cowart')
  else fail('dependencies 缺少 dsh-cowart')
} catch (error) {
  fail('无法读取 profile package.json', error.message)
}

if (profileOk && existsSync(join(profileDir, 'node_modules', 'dsh-cowart'))) {
  ok('profile node_modules 中存在 dsh-cowart')
} else {
  fail('profile node_modules 中缺少 dsh-cowart', '重新运行 dsh plugin --profile web add link:…')
}

const args = process.argv.slice(2)
const portArg = args.find((a) => a.startsWith('--port'))
if (portArg) {
  const port = portArg.slice('--port='.length) || '3080'
  console.log(`\n· 运行中的实例 http://127.0.0.1:${port}`)
  const probe = async (path, label) => {
    try {
      const res = await fetch(`http://127.0.0.1:${port}${path}`)
      if (res.ok) {
        ok(`${label} (${res.status})`)
        return res.json()
      }
      fail(`${label} (HTTP ${res.status})`)
      return null
    } catch (error) {
      fail(`${label}`, error.message)
      return null
    }
  }
  const health = await probe('/cowart/api/health', '健康检查 /cowart/api/health')
  if (health) {
    if (Array.isArray(health.tools) && health.tools.includes('cowart_insert_image')) {
      ok(`工具注册清单（${health.tools.length} 个）`)
    } else {
      fail('工具清单异常', JSON.stringify(health.tools))
    }
    if (health.canvasDistReady) ok('画布 dist 就绪')
    else fail('画布 dist 未就绪')
  }
  await probe('/cowart/api/canvas?projectDir=' + encodeURIComponent(root), '存储 API /cowart/api/canvas')
  const clientRes = await fetch(`http://127.0.0.1:${port}/plugins/dsh-cowart/client.js`).catch(() => null)
  if (clientRes?.ok) ok('客户端 bundle /plugins/dsh-cowart/client.js (200)')
  else fail('客户端 bundle /plugins/dsh-cowart/client.js', clientRes ? `HTTP ${clientRes.status}` : '无法连接')
  const pageRes = await fetch(`http://127.0.0.1:${port}/cowart/`).catch(() => null)
  if (pageRes?.ok) ok('画布页面 /cowart/ (200)')
  else fail('画布页面 /cowart/', pageRes ? `HTTP ${pageRes.status}` : '无法连接')
} else {
  console.log('\n（提示：加 --port 3080 可同时探测运行中的实例）')
}

console.log('')
if (failures.length) {
  console.log(`共 ${failures.length} 项问题：`)
  for (const f of failures) console.log(`  - ${f}`)
  process.exit(1)
}
console.log('全部通过 ✅ — 可以重启 dsh web 使用了。')
