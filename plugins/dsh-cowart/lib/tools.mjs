import {
  readCowartCanvasState,
  readCowartPageAsset,
  readCowartSelectionState,
  readCowartViewState,
  resolveCowartPaths,
  saveCowartCanvasSnapshot,
  writeCowartSelectionState,
  writeCowartViewState,
} from './canvas-storage.mjs'
import {
  downloadCowartFile,
  insertCowartHtmlDraft,
  insertCowartImage,
  saveCowartReferenceImage,
} from './insert.mjs'
import { cowartCanvasUrl, broadcastCanvasChanged } from './routes.mjs'

const DEFAULT_TIMEOUT_MS = 120_000

function nonBlank(value, label) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${label} must be a non-empty string`)
  return value.trim()
}

/** Resolve the canvas project directory: explicit arg first, then the session workspace. */
function projectArgs(args = {}, exec) {
  const cwd = exec?.agent?.session?.header?.cwd
  const projectDir = typeof args.projectDir === 'string' && args.projectDir.trim() !== ''
    ? args.projectDir.trim()
    : typeof cwd === 'string' && cwd.trim() !== ''
      ? cwd
      : undefined
  const base = projectDir === undefined ? {} : { projectDir }
  const canvasDir = typeof args.canvasDir === 'string' && args.canvasDir.trim() !== ''
    ? args.canvasDir.trim()
    : undefined
  return canvasDir === undefined ? base : { ...base, canvasDir }
}

function presentCall(title, kind, args, locations = []) {
  return {
    card: 'generic',
    title,
    kind,
    rawInput: args,
    ...(locations.length > 0 ? { locations } : {}),
  }
}

function resultSchema(extraProperties = {}) {
  return {
    type: 'object',
    additionalProperties: false,
    properties: {
      ok: { type: 'boolean' },
      ...extraProperties,
    },
  }
}

function textRender(title) {
  return (_args, value) => [
    {
      type: 'text',
      text: [`${title}:`, JSON.stringify(value, null, 2).slice(0, 4000)].join('\n'),
    },
  ]
}

function readOnly(parameters) {
  return {
    type: 'object',
    additionalProperties: false,
    properties: parameters,
    ...(Object.keys(parameters).length === 0 ? {} : { required: [] }),
  }
}

const PROJECT_ARGS = {
  projectDir: { type: 'string', description: 'The project/workspace directory whose canvas/ folder to use. Defaults to the current session workspace.' },
  canvasDir: { type: 'string', description: 'Optional explicit canvas directory override.' },
}

function register(ctx, tool) {
  ctx.tools.register({
    timeoutMs: DEFAULT_TIMEOUT_MS,
    isConcurrencySafe: () => true,
    ...tool,
  })
}

export function registerCowartTools(ctx) {
  register(ctx, {
    name: 'cowart_open_canvas',
    description:
      'Open the Cowart tldraw infinite canvas for a project inside the DSH Web GUI. Renders an embedded canvas iframe; canvas data is stored under <projectDir>/canvas. Call this when the user asks to open, launch, or work in the Cowart canvas.',
    parameters: readOnly({
      ...PROJECT_ARGS,
      title: { type: 'string', description: 'Optional canvas window title.' },
    }),
    output: {
      schema: resultSchema({
        projectDir: { type: 'string' },
        canvasDir: { type: 'string' },
        url: { type: 'string' },
        widget: { type: 'string' },
      }),
      render: (_args, value) => [
        { type: 'text', text: `Cowart canvas opened.\n- projectDir: ${value.projectDir}\n- canvasDir: ${value.canvasDir}\n- url: ${value.url}` },
      ],
    },
    presentCall: (args) => presentCall('Open Cowart canvas', 'read', args),
    async execute(args, exec) {
      const project = projectArgs(args, exec)
      const { projectDir, canvasDir } = resolveCowartPaths(project)
      if (!projectDir) throw new Error('cowart_open_canvas requires a projectDir; no session workspace is available')
      return {
        ok: true,
        projectDir,
        canvasDir,
        url: cowartCanvasUrl(projectDir),
        widget: 'cowart-canvas',
      }
    },
  })

  register(ctx, {
    name: 'cowart_get_canvas_state',
    description:
      'Read the project-backed Cowart canvas snapshot, view state, and storage paths. Prefer cowart_get_selection for just the selected shapes.',
    parameters: readOnly({
      ...PROJECT_ARGS,
      hydrateAssets: { type: 'boolean', description: 'Inline page-local image assets as data: URLs in the snapshot (large). Defaults to false.' },
    }),
    output: { schema: { type: 'object' }, render: textRender('Cowart canvas state') },
    presentCall: (args) => presentCall('Get Cowart canvas state', 'read', args),
    async execute(args, exec) {
      return readCowartCanvasState(projectArgs(args, exec), { hydrateAssets: args.hydrateAssets === true })
    },
  })

  register(ctx, {
    name: 'cowart_save_canvas_state',
    description:
      'Persist a Cowart/tldraw store snapshot to the project canvas directory, preserving per-page files and page-local assets.',
    parameters: readOnly({
      ...PROJECT_ARGS,
      snapshot: { type: 'object', description: 'The tldraw snapshot ({ schema, store }) to save.' },
      protectImageRecords: { type: 'boolean', description: 'Refuse saves that silently delete existing image shapes.' },
      acknowledgedImageShapeDeletes: { type: 'array', items: { type: 'string' }, description: 'Image shape ids the user already confirmed deleting.' },
    }),
    output: {
      schema: resultSchema({ storage: { type: 'string' }, paths: { type: 'array', items: { type: 'string' } }, skippedRecords: { type: 'array', items: { type: 'string' } } }),
      render: (_args, value) => textRender(`Cowart canvas saved (${value.storage ?? 'unknown'})`)(_args, value),
    },
    presentCall: (args) => presentCall('Save Cowart canvas state', 'write', args),
    async execute(args, exec) {
      if (!args.snapshot || typeof args.snapshot !== 'object') throw new Error('snapshot is required')
      const result = await saveCowartCanvasSnapshot(projectArgs(args, exec), args.snapshot)
      if (!result.ok) throw new Error(result.message || 'Invalid Cowart canvas snapshot')
      return result
    },
  })

  register(ctx, {
    name: 'cowart_get_selection',
    description:
      'Return the currently selected Cowart/tldraw shapes and image asset metadata from the project canvas selection state. Check this first when the user asks you to act on what is selected in the canvas.',
    parameters: readOnly({ ...PROJECT_ARGS }),
    output: { schema: { type: 'object' }, render: (_args, value) => {
      const shapes = value.selection?.selectedShapes ?? []
      const summary = shapes.length === 0
        ? 'No Cowart shapes are currently selected.'
        : shapes.map((shape) => `${shape.id} [${shape.type ?? 'unknown'}]${shape.asset?.name ? ` (${shape.asset.name})` : ''}`).join('\n')
      return [{ type: 'text', text: summary }]
    } },
    presentCall: (args) => presentCall('Get Cowart selection', 'read', args),
    async execute(args, exec) {
      return readCowartSelectionState(projectArgs(args, exec))
    },
  })

  register(ctx, {
    name: 'cowart_save_selection_state',
    description:
      'Persist the current Cowart widget selection to canvas/cowart-selection.json so agent tools can target selected shapes. Normally called by the canvas itself.',
    parameters: readOnly({
      ...PROJECT_ARGS,
      selection: { type: 'object', description: 'The selection state ({ selectedShapes: [...] }).' },
    }),
    output: { schema: { type: 'object' }, render: textRender('Cowart selection saved') },
    presentCall: (args) => presentCall('Save Cowart selection', 'write', args),
    async execute(args, exec) {
      if (!args.selection || typeof args.selection !== 'object') throw new Error('selection is required')
      return writeCowartSelectionState(projectArgs(args, exec), args.selection)
    },
  })

  register(ctx, {
    name: 'cowart_save_view_state',
    description:
      'Persist the current Cowart page and camera state to canvas/cowart-view-state.json. Normally called by the canvas itself.',
    parameters: readOnly({
      ...PROJECT_ARGS,
      viewState: { type: 'object', description: 'The view state ({ version: 1, currentPageId, camera: {x,y,z} }).' },
    }),
    output: { schema: { type: 'object' }, render: textRender('Cowart view state saved') },
    presentCall: (args) => presentCall('Save Cowart view state', 'write', args),
    async execute(args, exec) {
      if (!args.viewState || typeof args.viewState !== 'object') throw new Error('viewState is required')
      return writeCowartViewState(projectArgs(args, exec), args.viewState)
    },
  })

  register(ctx, {
    name: 'cowart_save_reference_image',
    description:
      'Save a widget-selected or exported reference image (dataUrl/base64) into the current Cowart page assets folder so the agent can read it from the local project. Normally called by the canvas itself; the agent may also call it for generated screenshots.',
    parameters: readOnly({
      ...PROJECT_ARGS,
      holderShapeId: { type: 'string', description: 'The AI image holder / anchor shape the reference belongs to.' },
      anchorShapeId: { type: 'string', description: 'Alias of holderShapeId.' },
      pageId: { type: 'string', description: 'Target page id; resolved from the selection/snapshot when omitted.' },
      fileName: { type: 'string', description: 'Desired file name (extension normalized).' },
      dataUrl: { type: 'string', description: 'The image as a data: URL.' },
      dataBase64: { type: 'string', description: 'Raw base64 payload (with mimeType).' },
      mimeType: { type: 'string', description: 'Image mime type, e.g. image/png.' },
    }),
    output: { schema: { type: 'object' }, render: textRender('Cowart reference image saved') },
    presentCall: (args) => presentCall('Save Cowart reference image', 'write', args),
    async execute(args, exec) {
      const result = await saveCowartReferenceImage(projectArgs(args, exec))
      return result
    },
  })

  register(ctx, {
    name: 'cowart_read_page_asset',
    description:
      'Read one project-local Cowart page asset (image or HTML) as base64 for inspection. Prefer modlens_read_image on the returned assetPath when you need to see an image.',
    parameters: readOnly({
      ...PROJECT_ARGS,
      assetUrl: { type: 'string', description: 'The /page-assets/... URL of the asset.' },
    }),
    output: { schema: { type: 'object' }, render: (_args, value) => [
      { type: 'text', text: `Cowart page asset ${value.assetUrl}\n- path: ${value.assetPath}\n- mime: ${value.mimeType}\n- bytes: ${value.fileSize}` },
    ] },
    presentCall: (args) => presentCall('Read Cowart page asset', 'read', args, typeof args?.assetUrl === 'string' ? [{ path: args.assetUrl }] : []),
    async execute(args, exec) {
      const url = nonBlank(args.assetUrl, 'assetUrl')
      return readCowartPageAsset(projectArgs(args, exec), { assetUrl: url })
    },
  })

  register(ctx, {
    name: 'cowart_insert_image',
    description:
      'Copy a local bitmap into a Cowart page-local assets folder, create a tldraw image asset and shape, replace a targeted AI image holder by default, otherwise place it beside an anchor or clear page area, and save the project-backed Cowart canvas.',
    parameters: readOnly({
      imagePath: { type: 'string', description: 'Local path of the bitmap to insert (PNG/JPEG/WebP).' },
      ...PROJECT_ARGS,
      pageId: { type: 'string', description: 'Target page id; auto-detected from the anchor/selection when omitted.' },
      anchorShapeId: { type: 'string', description: 'Place beside or replace this shape; defaults to the current selection.' },
      sourceShapeId: { type: 'string', description: 'Alias of anchorShapeId.' },
      fileName: { type: 'string', description: 'File name to store the bitmap under.' },
      placement: { type: 'string', enum: ['right', 'left', 'below'], description: 'Where to place the new image relative to the anchor. Defaults to right.' },
      margin: { type: 'number', description: 'Gap from the anchor. Defaults to 40.' },
      matchAnchor: { type: 'boolean', description: 'Match the anchor shape size when placing beside it. Defaults to true.' },
      replaceAiImageHolder: { type: 'boolean', description: 'Replace the targeted AI image holder frame with the image (default true when the anchor is a holder).' },
      displayWidth: { type: 'number', description: 'Explicit display width in canvas units.' },
      displayHeight: { type: 'number', description: 'Explicit display height in canvas units.' },
      altText: { type: 'string', description: 'Alt text for the image shape.' },
      annotationScreenshot: { type: 'string', description: 'Path of the annotation screenshot this result was generated from (recorded in shape meta).' },
      shapeMeta: { type: 'object', description: 'Extra metadata to merge into the shape meta.' },
      assetMeta: { type: 'object', description: 'Extra metadata to merge into the asset meta.' },
      dryRun: { type: 'boolean', description: 'Compute placement without writing anything.' },
    }),
    output: { schema: { type: 'object' }, render: (_args, value) => [
      { type: 'text', text: `${value.dryRun ? 'Planned' : 'Inserted'} ${value.shapeId} on ${value.pageId} at (${value.bounds?.x}, ${value.bounds?.y}) using ${value.index}.\nasset: ${value.assetFile}` },
    ] },
    presentCall: (args) => presentCall('Insert image into Cowart canvas', 'write', args, typeof args?.imagePath === 'string' ? [{ path: args.imagePath }] : []),
    async execute(args, exec) {
      const merged = { ...projectArgs(args, exec), ...args }
      const result = await insertCowartImage(merged)
      if (!result.dryRun) {
        broadcastCanvasChanged(merged, { storage: 'agent-insert', paths: [result.assetFile] })
      }
      return result
    },
  })

  register(ctx, {
    name: 'cowart_insert_html_draft',
    description:
      'Save a single-file HTML draft into the current Cowart page assets folder, update a targeted existing HTML draft in place, replace a targeted AI HTML holder, or append a 16:9 HTML page inside an AI Slides frame.',
    parameters: readOnly({
      htmlContent: { type: 'string', description: 'The complete single-file HTML document.' },
      htmlPath: { type: 'string', description: 'Alternative: local path of an HTML file to read.' },
      draftShapeId: { type: 'string', description: 'Target AI HTML holder / existing draft / AI Slides frame.' },
      anchorShapeId: { type: 'string', description: 'Alias of draftShapeId.' },
      pageId: { type: 'string', description: 'Target page id; auto-detected when omitted.' },
      fileName: { type: 'string', description: 'File name for the stored HTML.' },
      placement: { type: 'string', enum: ['right', 'left', 'below'] },
      margin: { type: 'number' },
      matchAnchor: { type: 'boolean', description: 'Match the anchor size when placing beside it.' },
      replaceDraftHolder: { type: 'boolean', description: 'Replace the targeted AI HTML holder with the embed (default true for holders).' },
      updateExistingDraft: { type: 'boolean', description: 'Update an existing HTML draft in place.' },
      displayWidth: { type: 'number' },
      displayHeight: { type: 'number' },
      shapeMeta: { type: 'object' },
      dryRun: { type: 'boolean' },
      ...PROJECT_ARGS,
    }),
    output: { schema: { type: 'object' }, render: (_args, value) => [
      { type: 'text', text: `${value.dryRun ? 'Planned' : 'Inserted'} HTML draft ${value.shapeId} on ${value.pageId} at (${value.bounds?.x}, ${value.bounds?.y}) using ${value.index}.\nasset: ${value.assetFile}` },
    ] },
    presentCall: (args) => presentCall('Insert HTML draft into Cowart canvas', 'write', args, typeof args?.htmlPath === 'string' ? [{ path: args.htmlPath }] : []),
    async execute(args, exec) {
      const merged = { ...projectArgs(args, exec), ...args }
      const result = await insertCowartHtmlDraft(merged)
      if (!result.dryRun) {
        broadcastCanvasChanged(merged, { storage: 'agent-insert', paths: [result.assetFile] })
      }
      return result
    },
  })

  register(ctx, {
    name: 'cowart_download_file',
    description:
      'Save an image, HTML draft, or exported file requested by the Cowart canvas into <projectDir>/Downloads (instead of the system Downloads folder).',
    parameters: readOnly({
      ...PROJECT_ARGS,
      assetUrl: { type: 'string', description: 'A /page-assets/... URL to copy out.' },
      fileName: { type: 'string', description: 'Desired file name.' },
      dataUrl: { type: 'string', description: 'Or a data: URL payload.' },
      dataBase64: { type: 'string', description: 'Or a raw base64 payload.' },
      mimeType: { type: 'string' },
      directoryName: { type: 'string', description: 'Sub-directory under Downloads.' },
      subdirectory: { type: 'string' },
      overwrite: { type: 'boolean' },
    }),
    output: { schema: { type: 'object' }, render: (_args, value) => [
      { type: 'text', text: `Cowart file saved to ${value.filePath} (${value.fileSize} bytes).` },
    ] },
    presentCall: (args) => presentCall('Download Cowart file', 'write', args),
    async execute(args, exec) {
      const merged = { ...projectArgs(args, exec), ...args }
      return downloadCowartFile(merged)
    },
  })
}
