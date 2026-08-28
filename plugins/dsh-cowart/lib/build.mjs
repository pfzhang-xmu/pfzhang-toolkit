import { existsSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

/** Where the built canvas app (vite build output) is served from. */
export function cowartDistDir() {
  return join(pluginRoot, 'dist', 'cowart')
}

/** Where the vendored Cowart canvas source lives (its own package). */
export function cowartCanvasDir() {
  return join(pluginRoot, 'canvas')
}

let buildPromise = null

/**
 * Ensure the plugin-root runtime deps (tldraw for snapshot sanitization,
 * fractional-indexing for canvas record ordering) exist. The profile's pnpm
 * workspace does not install linked-package dependencies, so the plugin owns
 * a pinned package-lock.json and installs from it when node_modules is absent.
 */
async function ensurePluginDeps(logger) {
  const marker = join(pluginRoot, 'node_modules', 'tldraw', 'package.json')
  if (existsSync(marker)) return
  logger.info('[cowart] installing plugin runtime deps (tldraw, fractional-indexing) …')
  await runCommand('npm', ['install', '--no-audit', '--no-fund'], {
    cwd: pluginRoot,
    label: 'cowart plugin dependency install',
    logger,
  })
  if (!existsSync(marker)) {
    throw new Error('cowart plugin runtime deps are missing after npm install')
  }
}

function runCommand(command, args, { cwd, label, logger }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env: { ...process.env, BROWSER: 'none', FORCE_COLOR: '0' },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const logs = []
    const capture = (chunk) => {
      const text = String(chunk)
      logs.push(text)
      if (logs.length > 80) logs.shift()
      logger.debug?.(`[cowart] ${text.trimEnd()}`)
    }
    child.stdout?.on('data', capture)
    child.stderr?.on('data', capture)
    child.once('error', reject)
    child.once('exit', (code, signal) => {
      if (code === 0) {
        resolve()
        return
      }
      reject(new Error(`${label} failed (${signal || `code ${code}`}).\n${logs.join('')}`))
    })
  })
}

function runCanvasBuild(logger) {
  const canvasDir = cowartCanvasDir()
  logger.info(`[cowart] building canvas app in ${canvasDir} …`)
  return new Promise((resolveBuild, rejectBuild) => {
    const steps = []
    if (!existsSync(join(canvasDir, 'node_modules', '.bin', 'vite'))) {
      steps.push(['npm', ['install', '--no-audit', '--no-fund']])
    }
    steps.push([
      process.execPath,
      [join(canvasDir, 'scripts', 'vite-build-once.mjs'), canvasDir, '--outDir', cowartDistDir(), '--emptyOutDir'],
    ])

    const runNext = (index) => {
      if (index >= steps.length) {
        resolveBuild()
        return
      }
      const [command, args] = steps[index]
      runCommand(command, args, { cwd: canvasDir, label: `cowart canvas build step ${index + 1}`, logger })
        .then(() => runNext(index + 1))
        .catch(rejectBuild)
    }
    runNext(0)
  })
}

/**
 * Ensure the built canvas app exists. `mode` comes from plugin config:
 * `auto` (build when missing), `force` (always rebuild), `never` (serve only).
 */
export async function ensureCanvasBuild(logger, mode = 'auto') {
  await ensurePluginDeps(logger)
  const distIndex = join(cowartDistDir(), 'index.html')
  if (mode === 'never') return
  if (mode !== 'force' && existsSync(distIndex)) return
  buildPromise ??= runCanvasBuild(logger).finally(() => {
    buildPromise = null
  })
  return buildPromise
}
