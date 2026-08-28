import { spawn } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const pluginRoot = join(scriptDir, '..')
const canvasDir = join(pluginRoot, 'canvas')
const distDir = join(pluginRoot, 'dist', 'cowart')

const child = spawn(process.execPath, [join(canvasDir, 'scripts', 'vite-build-once.mjs'), canvasDir, '--outDir', distDir, '--emptyOutDir'], {
  cwd: canvasDir,
  env: { ...process.env, BROWSER: 'none', FORCE_COLOR: '0', COWART_BASE: '/cowart/' },
  stdio: 'inherit',
})

child.once('error', (error) => {
  console.error(error)
  process.exit(1)
})
child.once('exit', (code, signal) => {
  process.exit(code ?? (signal ? 1 : 0))
})
