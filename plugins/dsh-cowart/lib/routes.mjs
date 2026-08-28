import { existsSync, createReadStream } from 'node:fs'
import { mkdir, stat, writeFile } from 'node:fs/promises'
import { extname, join, relative, resolve, sep } from 'node:path'

import {
  localAssetFilePathFromUrl,
  pageAssetUrl,
  pageDirName,
  readCowartCanvasState,
  readCowartPageAsset,
  readCowartSelectionState,
  readCowartViewState,
  resolveCowartPaths,
  saveCowartCanvasSnapshot,
  writeCowartSelectionState,
  writeCowartViewState,
} from './canvas-storage.mjs'
import { downloadCowartFile, saveCowartReferenceImage } from './insert.mjs'
import { cowartDistDir, ensureCanvasBuild } from './build.mjs'

const REGISTERED_TOOL_NAMES = [
  'cowart_open_canvas',
  'cowart_get_canvas_state',
  'cowart_save_canvas_state',
  'cowart_get_selection',
  'cowart_save_selection_state',
  'cowart_save_view_state',
  'cowart_save_reference_image',
  'cowart_read_page_asset',
  'cowart_insert_image',
  'cowart_insert_html_draft',
  'cowart_download_file',
]

const MAX_BODY_BYTES = 50 * 1024 * 1024
const PAGE_ID_PREFIX = 'page:'

const MIME_TYPES = new Map([
  ['.apng', 'image/apng'],
  ['.avif', 'image/avif'],
  ['.gif', 'image/gif'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.webp', 'image/webp'],
  ['.htm', 'text/html; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.map', 'application/json; charset=utf-8'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
  ['.ttf', 'font/ttf'],
  ['.ico', 'image/x-icon'],
  ['.txt', 'text/plain; charset=utf-8'],
  ['.webmanifest', 'application/manifest+json'],
])

/** Canvas iframe URL for the web client plugin. */
export function cowartCanvasUrl(projectDir) {
  return `/cowart/?projectDir=${encodeURIComponent(projectDir)}`
}

function sendJson(res, statusCode, payload) {
  res.statusCode = statusCode
  res.setHeader('content-type', 'application/json')
  res.end(JSON.stringify(payload))
}

function isSafeChildPath(parent, child) {
  const pathToChild = relative(parent, child)
  return pathToChild === '' || (pathToChild !== '..' && !pathToChild.startsWith(`..${sep}`) && !pathToChild.includes(`..${sep}`))
}

function readRequestBody(req) {
  return new Promise((resolveBody, rejectBody) => {
    let body = ''
    req.setEncoding('utf8')
    req.on('data', (chunk) => {
      body += chunk
      if (body.length > MAX_BODY_BYTES) {
        rejectBody(new Error('Canvas payload is too large.'))
        req.destroy()
      }
    })
    req.on('end', () => resolveBody(body))
    req.on('error', rejectBody)
  })
}

function parseCookies(header) {
  const cookies = {}
  if (typeof header !== 'string') return cookies
  for (const part of header.split(';')) {
    const index = part.indexOf('=')
    if (index === -1) continue
    const key = part.slice(0, index).trim()
    const value = part.slice(index + 1).trim()
    if (key) cookies[key] = value
  }
  return cookies
}

/** Resolve the canvas storage project from query param or the cowart_project_dir cookie. */
function projectDirFromRequest(req) {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1')
  const query = url.searchParams.get('projectDir')
  if (typeof query === 'string' && query.trim() !== '') {
    const dir = resolve(query.trim())
    return dir
  }
  const cookieDir = parseCookies(req.headers.cookie)['cowart_project_dir']
  if (typeof cookieDir === 'string' && cookieDir.trim() !== '') {
    try {
      return resolve(decodeURIComponent(cookieDir))
    } catch {
      return null
    }
  }
  return null
}

function projectArgsFromRequest(req) {
  const projectDir = projectDirFromRequest(req)
  if (projectDir === null) {
    throw new Error('Cowart request is missing projectDir; open the canvas through cowart_open_canvas first')
  }
  return { projectDir }
}

// ---- SSE broadcast ---------------------------------------------------------

const eventClients = new Map() // canvasDir -> Set<res>
let eventVersion = 0

function sendCanvasEvent(res, payload) {
  res.write(`event: canvas-changed\n`)
  res.write(`id: ${payload.version}\n`)
  res.write(`data: ${JSON.stringify(payload)}\n\n`)
}

/** Broadcast a canvas-saved event to open SSE clients of that canvas dir. */
export function broadcastCanvasChanged(args, result) {
  const { canvasDir } = resolveCowartPaths(args)
  const payload = {
    version: ++eventVersion,
    updatedAt: new Date().toISOString(),
    storage: result.storage,
    paths: result.paths,
  }
  const clients = eventClients.get(canvasDir)
  if (!clients) return
  for (const res of clients) {
    if (res.destroyed) {
      clients.delete(res)
      continue
    }
    try {
      sendCanvasEvent(res, payload)
    } catch {
      clients.delete(res)
    }
  }
}

// ---- HTML draft update (ported from the Cowart vite middleware) ------------

function isHtmlDraftShapeRecord(record) {
  return (
    record?.typeName === 'shape' &&
    record.type === 'embed' &&
    record.meta?.cowartHtmlDraft === true
  )
}

function pageIdForShape(store, shape) {
  let record = shape
  const visited = new Set()
  while (record && !visited.has(record.id)) {
    visited.add(record.id)
    if (record.typeName === 'page') return record.id
    record = store[record.parentId]
  }
  return null
}

function sanitizeAssetFileName(name, fallbackName, mimeType) {
  const extension = extname(String(name || fallbackName || 'asset')) || '.html'
  const baseName = String(name || fallbackName || 'asset')
    .slice(0, Math.max(0, String(name || fallbackName || 'asset').length - extension.length))
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return `${baseName || 'asset'}${extension}`
}

async function uniquePageAssetTarget(args, pageId, requestedName) {
  const { canvasDir } = resolveCowartPaths(args)
  const directory = join(canvasDir, 'pages', pageDirName(pageId), 'assets')
  const safeName = sanitizeAssetFileName(requestedName, 'html-draft.html', 'text/html')
  const extension = extname(safeName)
  const baseName = safeName.slice(0, safeName.length - extension.length)
  let fileName = safeName
  let counter = 2
  for (;;) {
    const filePath = join(directory, fileName)
    try {
      await stat(filePath)
      fileName = `${baseName}-v${counter}${extension}`
      counter += 1
    } catch (error) {
      if (error.code === 'ENOENT') return { fileName, filePath }
      throw error
    }
  }
}

function htmlDraftDataUrl(htmlContent) {
  return `data:text/html;base64,${Buffer.from(String(htmlContent || ''), 'utf8').toString('base64')}`
}

async function updateHtmlDraftSnapshot(args, snapshot, { draftShapeId, htmlContent }) {
  const shape = snapshot.store[draftShapeId]
  if (!isHtmlDraftShapeRecord(shape)) throw new Error(`HTML draft shape not found: ${draftShapeId}`)
  if (typeof htmlContent !== 'string' || !htmlContent.trim()) throw new Error('HTML draft content is empty.')

  const pageId = pageIdForShape(snapshot.store, shape)
  if (!pageId) throw new Error(`Could not determine page for HTML draft: ${draftShapeId}`)

  const existingAssetUrl = shape.meta?.cowartHtmlDraftAssetUrl
  const expectedPrefix = `/page-assets/${pageDirName(pageId)}/`
  const sharedAsset =
    typeof existingAssetUrl === 'string' &&
    Object.values(snapshot.store).some(
      (record) =>
        record?.id !== draftShapeId &&
        isHtmlDraftShapeRecord(record) &&
        record.meta?.cowartHtmlDraftAssetUrl === existingAssetUrl
    )

  let assetUrl = existingAssetUrl
  let assetPath = typeof existingAssetUrl === 'string' ? localAssetFilePathFromUrl(existingAssetUrl, args) : null
  if (!assetPath || !existingAssetUrl.startsWith(expectedPrefix) || sharedAsset) {
    let requestedName = `${draftShapeId.replace(/[^a-zA-Z0-9_-]+/g, '-')}.html`
    if (typeof existingAssetUrl === 'string' && existingAssetUrl.startsWith(expectedPrefix)) {
      try {
        requestedName = decodeURIComponent(existingAssetUrl.slice(expectedPrefix.length))
      } catch {
        // fall back to the shape-based file name
      }
    }
    const target = await uniquePageAssetTarget(args, pageId, requestedName)
    assetPath = target.filePath
    assetUrl = pageAssetUrl(pageId, target.fileName)
  }

  await mkdir(join(assetPath, '..'), { recursive: true })
  await writeFile(assetPath, htmlContent)
  snapshot.store[draftShapeId] = {
    ...shape,
    meta: {
      ...shape.meta,
      cowartHtmlDraft: true,
      cowartHtmlDraftAssetUrl: assetUrl,
    },
    props: {
      ...shape.props,
      url: htmlDraftDataUrl(htmlContent),
    },
  }

  return { assetPath, assetUrl, forkedSharedHtmlDraftAsset: sharedAsset, pageId, shapeId: draftShapeId }
}

// ---- Route handlers --------------------------------------------------------

async function serveCowartAsset(req, res) {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1')
  const assetUrl = url.pathname
  const args = { projectDir: projectDirFromRequest(req) }
  if (args.projectDir === null) {
    res.statusCode = 403
    res.end('Forbidden: no canvas project')
    return
  }
  const filePath = localAssetFilePathFromUrl(assetUrl, args)
  if (!filePath) {
    res.statusCode = 403
    res.end('Forbidden')
    return
  }
  try {
    const fileStat = await stat(filePath)
    if (!fileStat.isFile()) {
      res.statusCode = 404
      res.end('Not found')
      return
    }
    res.statusCode = 200
    res.setHeader('content-type', MIME_TYPES.get(extname(filePath).toLowerCase()) ?? 'application/octet-stream')
    res.setHeader('content-length', String(fileStat.size))
    res.setHeader('cache-control', 'no-cache')
    createReadStream(filePath).pipe(res)
  } catch (error) {
    if (error?.code === 'ENOENT') {
      res.statusCode = 404
      res.end('Not found')
      return
    }
    res.statusCode = 500
    res.end('Internal error')
  }
}

async function serveCowartStatic(req, res, logger) {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1')
  const distDir = cowartDistDir()
  try {
    await ensureCanvasBuild(logger, 'auto')
  } catch (error) {
    res.statusCode = 503
    res.setHeader('content-type', 'text/plain; charset=utf-8')
    res.end(`Cowart canvas is not built yet.\n${error instanceof Error ? error.message : String(error)}`)
    return
  }
  let rel = url.pathname.slice('/cowart'.length)
  if (rel === '' || rel === '/') rel = '/index.html'
  const filePath = join(distDir, rel)
  if (!isSafeChildPath(distDir, filePath)) {
    res.statusCode = 403
    res.end('Forbidden')
    return
  }
  try {
    const fileStat = await stat(filePath)
    if (!fileStat.isFile()) {
      res.statusCode = 404
      res.end('Not found')
      return
    }
    res.statusCode = 200
    res.setHeader('content-type', MIME_TYPES.get(extname(filePath).toLowerCase()) ?? 'application/octet-stream')
    res.setHeader('content-length', String(fileStat.size))
    res.setHeader('cache-control', 'no-cache')
    createReadStream(filePath).pipe(res)
  } catch (error) {
    if (error?.code === 'ENOENT') {
      res.statusCode = 404
      res.end('Not found')
      return
    }
    res.statusCode = 500
    res.end('Internal error')
  }
}

function isCanvasSnapshot(value) {
  return value && typeof value === 'object' && value.store && value.schema
}

function isSelectionState(value) {
  return value && typeof value === 'object' && Array.isArray(value.selectedShapes)
}

function isViewState(value) {
  return (
    value &&
    typeof value === 'object' &&
    value.version === 1 &&
    (value.currentPageId === null || typeof value.currentPageId === 'string') &&
    value.camera &&
    typeof value.camera === 'object' &&
    Number.isFinite(value.camera.x) &&
    Number.isFinite(value.camera.y) &&
    Number.isFinite(value.camera.z)
  )
}

function register(ctx) {
  const route = (path, handler) => {
    ctx.effect(
      () => ctx.webServer.register({ kind: 'exact', path, handler }),
      `cowart: route ${path}`
    )
  }

  // Canvas iframe app + its API namespace (longest prefix wins over /cowart).
  ctx.effect(
    () => ctx.webServer.register({ kind: 'prefix', path: '/cowart', handler: (req, res) => serveCowartStatic(req, res, ctx.logger) }),
    'cowart: /cowart static'
  )
  ctx.effect(
    () => ctx.webServer.register({ kind: 'prefix', path: '/page-assets', handler: serveCowartAsset }),
    'cowart: /page-assets'
  )
  // Do not claim `/assets`: it is the Harness web app's own static asset
  // namespace. Cowart assets use the page-scoped `/page-assets` route above.

  route('/cowart/api/canvas', async (req, res) => {
    try {
      const args = projectArgsFromRequest(req)
      if (req.method === 'GET') {
        const state = await readCowartCanvasState(args, { hydrateAssets: false })
        sendJson(res, 200, state)
        return
      }
      if (req.method === 'PUT') {
        const body = JSON.parse(await readRequestBody(req))
        if (!isCanvasSnapshot(body)) {
          sendJson(res, 400, { error: 'Expected a tldraw store snapshot.' })
          return
        }
        const result = await saveCowartCanvasSnapshot(args, body)
        if (!result.ok) {
          sendJson(res, 422, result)
          return
        }
        broadcastCanvasChanged(args, result)
        sendJson(res, 200, { ok: true, ...result })
        return
      }
      res.statusCode = 405
      res.setHeader('allow', 'GET, PUT')
      res.end()
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/cowart/api/selection', async (req, res) => {
    try {
      const args = projectArgsFromRequest(req)
      if (req.method === 'GET') {
        const result = await readCowartSelectionState(args)
        sendJson(res, 200, { selection: result.selection, path: result.selectionFile })
        return
      }
      if (req.method === 'PUT') {
        const body = JSON.parse(await readRequestBody(req))
        if (!isSelectionState(body)) {
          sendJson(res, 400, { error: 'Expected a Cowart selection state.' })
          return
        }
        const result = await writeCowartSelectionState(args, body)
        sendJson(res, 200, { ok: true, path: result.path })
        return
      }
      res.statusCode = 405
      res.setHeader('allow', 'GET, PUT')
      res.end()
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/cowart/api/view-state', async (req, res) => {
    try {
      const args = projectArgsFromRequest(req)
      if (req.method === 'GET') {
        const result = await readCowartViewState(args)
        sendJson(res, 200, { viewState: result.viewState, path: result.viewStateFile })
        return
      }
      if (req.method === 'PUT') {
        const body = JSON.parse(await readRequestBody(req))
        if (!isViewState(body)) {
          sendJson(res, 400, { error: 'Expected a Cowart view state.' })
          return
        }
        const result = await writeCowartViewState(args, body)
        sendJson(res, 200, { ok: true, path: result.path })
        return
      }
      res.statusCode = 405
      res.setHeader('allow', 'GET, PUT')
      res.end()
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/cowart/api/html-draft', async (req, res) => {
    try {
      if (req.method !== 'PUT') {
        res.statusCode = 405
        res.setHeader('allow', 'PUT')
        res.end()
        return
      }
      const args = projectArgsFromRequest(req)
      const body = JSON.parse(await readRequestBody(req))
      const loaded = await readCowartCanvasState(args, { hydrateAssets: false })
      if (!loaded.snapshot) {
        sendJson(res, 404, { error: 'No Cowart canvas snapshot exists.' })
        return
      }
      const updatedDraft = await updateHtmlDraftSnapshot(args, loaded.snapshot, body)
      const result = await saveCowartCanvasSnapshot(args, loaded.snapshot)
      if (!result.ok) {
        sendJson(res, 422, result)
        return
      }
      broadcastCanvasChanged(args, result)
      sendJson(res, 200, { ok: true, ...updatedDraft, ...result })
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/cowart/api/reference', async (req, res) => {
    try {
      if (req.method !== 'POST') {
        res.statusCode = 405
        res.setHeader('allow', 'POST')
        res.end()
        return
      }
      const args = projectArgsFromRequest(req)
      const body = JSON.parse(await readRequestBody(req))
      const result = await saveCowartReferenceImage({ ...args, ...body })
      sendJson(res, 200, result)
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/cowart/api/asset', async (req, res) => {
    try {
      if (req.method !== 'GET') {
        res.statusCode = 405
        res.setHeader('allow', 'GET')
        res.end()
        return
      }
      const args = projectArgsFromRequest(req)
      const url = new URL(req.url ?? '/', 'http://127.0.0.1')
      const assetUrl = url.searchParams.get('url')
      if (!assetUrl) {
        sendJson(res, 400, { error: 'assetUrl is required.' })
        return
      }
      const result = await readCowartPageAsset(args, { assetUrl })
      sendJson(res, 200, result)
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/cowart/api/download', async (req, res) => {
    try {
      if (req.method !== 'POST') {
        res.statusCode = 405
        res.setHeader('allow', 'POST')
        res.end()
        return
      }
      const args = projectArgsFromRequest(req)
      const body = JSON.parse(await readRequestBody(req))
      const result = await downloadCowartFile({ ...args, ...body })
      sendJson(res, 200, result)
    } catch (error) {
      sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
    }
  })

  route('/cowart/api/health', (req, res) => {
    const distIndex = join(cowartDistDir(), 'index.html')
    sendJson(res, 200, {
      ok: true,
      plugin: 'dsh-cowart',
      tools: REGISTERED_TOOL_NAMES,
      canvasDistReady: existsSync(distIndex),
      canvasDistDir: cowartDistDir(),
      projectDirSource: projectDirFromRequest(req) ?? null,
    })
  })

  route('/cowart/api/canvas-events', (req, res) => {
    if (req.method !== 'GET') {
      res.statusCode = 405
      res.setHeader('allow', 'GET')
      res.end()
      return
    }
    const args = projectArgsFromRequest(req)
    const { canvasDir } = resolveCowartPaths(args)

    res.statusCode = 200
    res.setHeader('content-type', 'text/event-stream')
    res.setHeader('cache-control', 'no-cache, no-transform')
    res.setHeader('connection', 'keep-alive')
    res.write(`: connected\n\n`)

    let clients = eventClients.get(canvasDir)
    if (!clients) {
      clients = new Set()
      eventClients.set(canvasDir, clients)
    }
    clients.add(res)

    const heartbeat = setInterval(() => {
      res.write(`: heartbeat ${Date.now()}\n\n`)
    }, 25000)

    req.on('close', () => {
      clearInterval(heartbeat)
      clients.delete(res)
      if (clients.size === 0) eventClients.delete(canvasDir)
    })
  })
}

export function registerCowartRoutes(ctx) {
  register(ctx)
}
