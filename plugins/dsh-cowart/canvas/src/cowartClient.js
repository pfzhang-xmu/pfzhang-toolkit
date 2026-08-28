const CANVAS_ENDPOINT = '/cowart/api/canvas'
const SELECTION_ENDPOINT = '/cowart/api/selection'
const VIEW_STATE_ENDPOINT = '/cowart/api/view-state'
const HTML_DRAFT_ENDPOINT = '/cowart/api/html-draft'
const REFERENCE_ENDPOINT = '/cowart/api/reference'
const ASSET_ENDPOINT = '/cowart/api/asset'
const DOWNLOAD_ENDPOINT = '/cowart/api/download'
const CANVAS_EVENTS_ENDPOINT = '/cowart/api/canvas-events'

const TOOL_GET_CANVAS_STATE = 'get_cowart_canvas_state'
const TOOL_SAVE_CANVAS_STATE = 'save_cowart_canvas_state'
const TOOL_SAVE_SELECTION_STATE = 'save_cowart_selection_state'
const TOOL_SAVE_VIEW_STATE = 'save_cowart_view_state'
const TOOL_SAVE_REFERENCE_IMAGE = 'save_cowart_reference_image'
const TOOL_READ_PAGE_ASSET = 'read_cowart_page_asset'
const TOOL_DOWNLOAD_FILE = 'download_cowart_file'
const TOOL_COPY_IMAGE_TO_CLIPBOARD = 'copy_cowart_image_to_clipboard'
const TOOL_INSERT_HTML_DRAFT = 'insert_cowart_html_draft'
const WIDGET_PAYLOAD_TIMEOUT_MS = 5000

globalThis.__COWART_WIDGET_FETCH_GUARD__ = true

export const IS_COWART_WIDGET_BUILD =
  typeof __COWART_WIDGET_BUILD__ !== 'undefined' && __COWART_WIDGET_BUILD__

/**
 * DSH mode: the canvas is embedded in the DSH Web GUI (same origin) and talks
 * to the dsh-cowart host plugin through HTTP routes instead of the Codex MCP
 * widget bridge. Detected by the projectDir query param set by the client
 * plugin when it renders the iframe.
 */
export const IS_DSH_MODE =
  typeof window !== 'undefined' &&
  typeof window.location !== 'undefined' &&
  new URLSearchParams(window.location.search).has('projectDir')

function dshProjectDirFromUrl() {
  if (!IS_DSH_MODE) return null
  const projectDir = new URLSearchParams(window.location.search).get('projectDir')
  if (typeof projectDir === 'string' && projectDir.trim() !== '') {
    try {
      document.cookie = `cowart_project_dir=${encodeURIComponent(projectDir)}; path=/`
    } catch {
      // cookie write is best-effort; API calls carry projectDir explicitly
    }
    return projectDir
  }
  return null
}

export function hasCowartWidgetBridge() {
  return IS_DSH_MODE || Boolean(window.cowartMcp && typeof window.cowartMcp.callServerTool === 'function')
}

function currentWidgetPayload() {
  if (IS_DSH_MODE) {
    const projectDir = dshProjectDirFromUrl()
    return projectDir ? { projectDir } : {}
  }
  return window.openai?.toolOutput && typeof window.openai.toolOutput === 'object'
    ? window.openai.toolOutput
    : {}
}

function hasWidgetStorageTarget() {
  const payload = currentWidgetPayload()
  return Boolean(payload.projectDir || payload.canvasDir)
}

function serverToolArgs(extra = {}) {
  const payload = currentWidgetPayload()
  return removeUndefined({
    projectDir: payload.projectDir,
    canvasDir: payload.canvasDir,
    ...extra
  })
}

function removeUndefined(value) {
  return Object.fromEntries(Object.entries(value).filter(([_key, item]) => item !== undefined))
}

function abortError() {
  return new DOMException('The operation was aborted.', 'AbortError')
}

async function waitForWidgetPayload(signal) {
  if (!hasCowartWidgetBridge()) return
  if (hasWidgetStorageTarget()) return

  await new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }

    const timer = window.setTimeout(() => {
      cleanup()
      reject(new Error('Cowart widget storage target was not ready. Refusing to read or write without projectDir/canvasDir.'))
    }, WIDGET_PAYLOAD_TIMEOUT_MS)
    const cleanup = () => {
      window.clearTimeout(timer)
      window.removeEventListener('openai:set_globals', handleGlobals)
      signal?.removeEventListener('abort', handleAbort)
    }
    const finish = () => {
      cleanup()
      resolve()
    }
    const handleGlobals = () => {
      if (hasWidgetStorageTarget()) finish()
    }
    const handleAbort = () => {
      cleanup()
      reject(abortError())
    }

    window.addEventListener('openai:set_globals', handleGlobals, { once: true })
    signal?.addEventListener('abort', handleAbort, { once: true })
  })
}

async function fetchJson(url, options = {}) {
  const response = await window.fetch(url, options)
  if (!response.ok) {
    let detail = ''
    try {
      const parsed = await response.json()
      detail = parsed?.error?.message ?? parsed?.message ?? parsed?.error ?? ''
    } catch {
      // not JSON
    }
    const clean = typeof detail === 'string' && detail.trim() !== '' ? detail.trim() : `${response.status} ${response.statusText}`
    throw new Error(`Cowart request failed: ${clean}`)
  }
  return response.json()
}

function dshQuery() {
  const projectDir = dshProjectDirFromUrl()
  return projectDir ? `?projectDir=${encodeURIComponent(projectDir)}` : ''
}

function readPngDimensions(base64) {
  const buffer = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))
  if (buffer.length < 24 || String.fromCharCode(...buffer.subarray(12, 16)) !== 'IHDR') {
    throw new Error('Cowart clipboard PNG is missing its IHDR header.')
  }
  const readU32 = (offset) => ((buffer[offset] << 24) | (buffer[offset + 1] << 16) | (buffer[offset + 2] << 8) | buffer[offset + 3]) >>> 0
  return { width: readU32(16), height: readU32(20) }
}

async function copyPngToBrowserClipboard(dataUrl) {
  const match = String(dataUrl || '').match(/^data:image\/png;base64,(.+)$/)
  if (!match) throw new Error('Cowart clipboard only supports image/png data URLs.')
  const base64 = match[1]
  const dimensions = readPngDimensions(base64)
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  const blob = new Blob([bytes], { type: 'image/png' })
  if (!navigator.clipboard?.write) throw new Error('浏览器剪贴板不可用。')
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
  return { ok: true, width: dimensions.width, height: dimensions.height, mimeType: 'image/png', platform: 'web' }
}

/** DSH-mode dispatcher: route each Cowart "server tool" to an HTTP endpoint. */
async function callDshServerTool(name, args, options = {}) {
  const query = dshQuery()
  switch (name) {
    case TOOL_GET_CANVAS_STATE: {
      const [canvasData, viewData] = await Promise.all([
        fetchJson(`${CANVAS_ENDPOINT}${query}`, { signal: options.signal }),
        fetchJson(`${VIEW_STATE_ENDPOINT}${query}`, { signal: options.signal }),
      ])
      return {
        ...canvasData,
        viewState: viewData.viewState ?? null,
        viewStateFile: viewData.path ?? null,
        hydratedAssets: [],
      }
    }
    case TOOL_SAVE_CANVAS_STATE: {
      const result = await fetchJson(`${CANVAS_ENDPOINT}${query}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(args.snapshot),
        signal: options.signal,
      })
      if (result?.ok === false) {
        throw new Error(result.message || 'Invalid Cowart canvas snapshot.')
      }
      return result
    }
    case TOOL_SAVE_SELECTION_STATE: {
      const result = await fetchJson(`${SELECTION_ENDPOINT}${query}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(args.selection),
        signal: options.signal,
      })
      return { ok: true, path: result.path, selection: args.selection }
    }
    case TOOL_SAVE_VIEW_STATE: {
      const result = await fetchJson(`${VIEW_STATE_ENDPOINT}${query}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(args.viewState),
        signal: options.signal,
      })
      return { ok: true, path: result.path, viewState: args.viewState }
    }
    case TOOL_SAVE_REFERENCE_IMAGE: {
      return fetchJson(`${REFERENCE_ENDPOINT}${query}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(args),
        signal: options.signal,
      })
    }
    case TOOL_READ_PAGE_ASSET: {
      return fetchJson(`${ASSET_ENDPOINT}${query}&url=${encodeURIComponent(args.assetUrl)}`, {
        signal: options.signal,
      })
    }
    case TOOL_DOWNLOAD_FILE: {
      return fetchJson(`${DOWNLOAD_ENDPOINT}${query}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(args),
        signal: options.signal,
      })
    }
    case TOOL_COPY_IMAGE_TO_CLIPBOARD: {
      return copyPngToBrowserClipboard(args.dataUrl)
    }
    case TOOL_INSERT_HTML_DRAFT: {
      return fetchJson(`${HTML_DRAFT_ENDPOINT}${query}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(args),
        signal: options.signal,
      })
    }
    default:
      throw new Error(`Unsupported Cowart tool in DSH mode: ${name}`)
  }
}

async function callCowartServerTool(name, args = {}, options = {}) {
  await waitForWidgetPayload(options.signal)
  if (options.signal?.aborted) throw abortError()
  if (IS_DSH_MODE) {
    return callDshServerTool(name, serverToolArgs(args), options)
  }
  const result = await window.cowartMcp.callServerTool({
    name,
    arguments: serverToolArgs(args)
  })
  if (result?.isError) {
    const message = result.content?.find((item) => item.type === 'text')?.text
    throw new Error(message || `Cowart server tool failed: ${name}`)
  }
  return result.structuredContent ?? result
}

export async function loadCowartCanvasState(signal) {
  if (hasCowartWidgetBridge()) {
    const state = await callCowartServerTool(
      TOOL_GET_CANVAS_STATE,
      { hydrateAssets: false },
      { signal }
    )
    return {
      snapshot: state.snapshot,
      viewState: state.viewState ?? null,
      storage: state.storage,
      skippedRecords: []
    }
  }

  const [canvasData, viewStateData] = await Promise.all([
    fetchJson(`${CANVAS_ENDPOINT}${dshQuery()}`, { signal }),
    fetchJson(`${VIEW_STATE_ENDPOINT}${dshQuery()}`, { signal })
  ])
  return {
    snapshot: canvasData.snapshot,
    viewState: viewStateData.viewState ?? null,
    storage: canvasData.storage,
    skippedRecords: []
  }
}

export async function refreshCowartCanvasSnapshot(signal) {
  if (hasCowartWidgetBridge()) {
    const state = await callCowartServerTool(
      TOOL_GET_CANVAS_STATE,
      { hydrateAssets: false },
      { signal }
    )
    return state.snapshot
  }

  const canvasData = await fetchJson(`${CANVAS_ENDPOINT}${dshQuery()}`, { signal })
  return canvasData.snapshot
}

export async function saveCowartCanvasSnapshot(snapshot, options = {}) {
  if (hasCowartWidgetBridge()) {
    return callCowartServerTool(TOOL_SAVE_CANVAS_STATE, {
      snapshot,
      protectImageRecords: options.protectImageRecords,
      acknowledgedImageShapeDeletes: options.acknowledgedImageShapeDeletes
    })
  }

  return fetchJson(`${CANVAS_ENDPOINT}${dshQuery()}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(snapshot)
  })
}

export async function saveCowartSelectionState(selection) {
  if (hasCowartWidgetBridge()) {
    return callCowartServerTool(TOOL_SAVE_SELECTION_STATE, { selection })
  }

  return fetchJson(`${SELECTION_ENDPOINT}${dshQuery()}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(selection)
  })
}

export async function saveCowartViewState(viewState) {
  if (hasCowartWidgetBridge()) {
    return callCowartServerTool(TOOL_SAVE_VIEW_STATE, { viewState })
  }

  return fetchJson(`${VIEW_STATE_ENDPOINT}${dshQuery()}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(viewState)
  })
}

export async function saveCowartReferenceImage(reference) {
  if (!hasCowartWidgetBridge()) {
    throw new Error('当前 Cowart 画布没有可用的保存桥。')
  }

  return callCowartServerTool(TOOL_SAVE_REFERENCE_IMAGE, reference)
}

export async function downloadCowartFile(download) {
  if (!hasCowartWidgetBridge()) {
    throw new Error('当前 Cowart 画布没有可用的下载桥。')
  }

  return callCowartServerTool(TOOL_DOWNLOAD_FILE, download)
}

export async function copyCowartImageToClipboard(image) {
  if (!hasCowartWidgetBridge()) {
    throw new Error('当前 Cowart 画布没有可用的剪贴板桥。')
  }

  return callCowartServerTool(TOOL_COPY_IMAGE_TO_CLIPBOARD, image)
}

export async function updateCowartHtmlDraft({ draftShapeId, htmlContent }) {
  if (!hasCowartWidgetBridge()) {
    return fetchJson(`${HTML_DRAFT_ENDPOINT}${dshQuery()}`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ draftShapeId, htmlContent })
    })
  }

  return callCowartServerTool(TOOL_INSERT_HTML_DRAFT, {
    draftShapeId,
    htmlContent,
    updateExistingDraft: true
  })
}

export async function readCowartPageAsset(assetUrl, options = {}) {
  if (!hasCowartWidgetBridge()) {
    const response = await window.fetch(`${ASSET_ENDPOINT}${dshQuery()}&url=${encodeURIComponent(assetUrl)}`, { signal: options.signal })
    if (!response.ok) throw new Error(`Cowart asset request failed: ${response.status}`)
    return response.json()
  }

  return callCowartServerTool(TOOL_READ_PAGE_ASSET, { assetUrl }, options)
}

/** SSE endpoint for live canvas refresh in DSH mode. */
export function cowartCanvasEventsUrl() {
  return `${CANVAS_EVENTS_ENDPOINT}${dshQuery()}`
}
