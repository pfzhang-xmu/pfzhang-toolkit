import { createHash, randomBytes } from 'node:crypto'
import { mkdir, open, readFile, realpath, rename, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

const DEFAULT_API_KEY_ENV = 'IMAGE_API_KEY'
const DEFAULT_BASE_URL = 'https://api.example.com/v1'
const DEFAULT_MODEL = 'gpt-image-1'
const DEFAULT_TIMEOUT_MS = 180_000
const DEFAULT_MAX_INPUT_BYTES = 25 * 1024 * 1024
const DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024 * 1024
const OUTPUT_DIRECTORY = '.dsh-images'
const RETRYABLE_STATUS = new Set([408, 409, 429, 500, 502, 503, 504])

const IMAGE_TYPES = [
  { mime: 'image/png', ext: '.png', test: (b) => b.length >= 8 && b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) },
  { mime: 'image/jpeg', ext: '.jpg', test: (b) => b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  { mime: 'image/webp', ext: '.webp', test: (b) => b.length >= 12 && b.toString('ascii', 0, 4) === 'RIFF' && b.toString('ascii', 8, 12) === 'WEBP' },
  { mime: 'image/gif', ext: '.gif', test: (b) => b.length >= 6 && ['GIF87a', 'GIF89a'].includes(b.toString('ascii', 0, 6)) },
]

export const name = 'image-tools'
export const inject = ['tools', 'agents']

function nonBlank(value, label) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${label} must be a non-empty string`)
  return value.trim()
}

function workspaceOf(exec) {
  const cwd = exec?.agent?.session?.header?.cwd
  if (typeof cwd !== 'string' || cwd.trim() === '' || !isAbsolute(cwd)) {
    throw new Error('Image tools require an agent session with an absolute workspace path')
  }
  return resolve(cwd)
}

function within(parent, child) {
  const candidate = relative(parent, child)
  return candidate === '' || (candidate !== '..' && !candidate.startsWith(`..${sep}`) && !isAbsolute(candidate))
}

async function resolveAllowedInputPath(workspace, input, label) {
  const path = isAbsolute(input) ? resolve(input) : resolve(workspace, input)
  const [canonical, canonicalWorkspace, canonicalTemp] = await Promise.all([
    realpath(path).catch(() => path),
    realpath(workspace).catch(() => workspace),
    realpath(tmpdir()).catch(() => tmpdir()),
  ])
  if (within(canonicalWorkspace, canonical)) return canonical
  const parent = dirname(canonical)
  if (within(canonicalTemp, parent) && basename(parent).startsWith('modlens-dsh-paste-')) return canonical
  throw new Error(`${label} must be inside the current session workspace or a Modlens paste directory`)
}

function sniffImage(bytes) {
  return IMAGE_TYPES.find((type) => type.test(bytes))
}

async function readLimitedFile(path, maxBytes, label) {
  const handle = await open(path, 'r')
  try {
    const stat = await handle.stat()
    if (!stat.isFile()) throw new Error(`${label} is not a regular file`)
    if (stat.size === 0) throw new Error(`${label} is empty`)
    if (stat.size > maxBytes) throw new Error(`${label} exceeds the ${maxBytes}-byte limit`)
    const bytes = Buffer.alloc(stat.size)
    let offset = 0
    while (offset < stat.size) {
      const { bytesRead } = await handle.read(bytes, offset, stat.size - offset, offset)
      if (bytesRead === 0) throw new Error(`${label} changed while it was being read`)
      offset += bytesRead
    }
    return bytes
  } finally {
    await handle.close()
  }
}

async function responseBytes(response, maxBytes, label) {
  const declared = Number(response.headers.get('content-length'))
  if (Number.isFinite(declared) && declared > maxBytes) throw new Error(`${label} exceeds the ${maxBytes}-byte limit`)
  if (!response.body) throw new Error(`${label} returned no body`)
  const chunks = []
  let total = 0
  for await (const chunk of response.body) {
    const bytes = Buffer.from(chunk)
    total += bytes.length
    if (total > maxBytes) throw new Error(`${label} exceeds the ${maxBytes}-byte limit`)
    chunks.push(bytes)
  }
  return Buffer.concat(chunks)
}

async function readImageInput(value, workspace, maxBytes, signal, label) {
  const input = nonBlank(value, label)
  if (/^https:\/\//i.test(input)) {
    const response = await fetch(input, { signal, redirect: 'follow' })
    if (!response.ok) throw new Error(`${label} download failed (HTTP ${response.status})`)
    const bytes = await responseBytes(response, maxBytes, label)
    const type = sniffImage(bytes)
    if (!type) throw new Error(`${label} is not a recognized PNG, JPEG, WebP, or GIF image`)
    return { bytes, type, source: input }
  }
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(input)) throw new Error(`${label} URL must use HTTPS`)
  const path = await resolveAllowedInputPath(workspace, input, label)
  const bytes = await readLimitedFile(path, maxBytes, label)
  const type = sniffImage(bytes)
  if (!type) throw new Error(`${label} is not a recognized PNG, JPEG, WebP, or GIF image`)
  return { bytes, type, source: path }
}

async function resolveApiKey(ctx, ref) {
  const credentials = ctx.get('credentials')
  let value
  if (credentials !== undefined) value = (await credentials.resolve(ref))?.value
  if ((value === undefined || value.length === 0) && typeof process.env[ref] === 'string') value = process.env[ref]
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Image API credential is missing; configure ${ref} in DSH credentials or the launch environment`)
  }
  return value
}

function endpoint(baseURL, operation) {
  return `${baseURL.replace(/\/+$/u, '')}/images/${operation}`
}

function apiError(status, body) {
  let detail = ''
  try {
    const parsed = JSON.parse(body)
    detail = parsed?.error?.message ?? parsed?.message ?? parsed?.error ?? ''
  } catch {}
  const clean = typeof detail === 'string' ? detail.trim().slice(0, 500) : ''
  return new Error(clean || `Image API request failed (HTTP ${status})`)
}

async function requestWithRetry(url, init, signal, maxRetries) {
  let attempt = 0
  for (;;) {
    let response
    try {
      response = await fetch(url, { ...init, signal })
    } catch (error) {
      if (signal?.aborted || attempt >= maxRetries) throw error
      attempt += 1
      continue
    }
    if (response.ok) return response
    const body = await response.text().catch(() => '')
    if (!RETRYABLE_STATUS.has(response.status) || attempt >= maxRetries) throw apiError(response.status, body)
    attempt += 1
  }
}

function optionalGenerationFields(args) {
  return {
    ...(args.size ? { size: args.size } : {}),
    ...(args.quality ? { quality: args.quality } : {}),
    ...(args.background ? { background: args.background } : {}),
    ...(args.output_format ? { output_format: args.output_format } : {}),
  }
}

async function parseApiImages(response, maxBytes, signal) {
  let payload
  try {
    payload = await response.json()
  } catch (error) {
    throw new Error(`Image API returned invalid JSON: ${error instanceof Error ? error.message : String(error)}`)
  }
  if (!Array.isArray(payload?.data) || payload.data.length === 0) throw new Error('Image API response contains no images')
  const outputs = []
  for (const item of payload.data) {
    let bytes
    if (typeof item?.b64_json === 'string' && item.b64_json.length > 0) {
      bytes = Buffer.from(item.b64_json, 'base64')
      if (bytes.length > maxBytes) throw new Error(`Generated image exceeds the ${maxBytes}-byte limit`)
    } else if (typeof item?.url === 'string' && /^https:\/\//i.test(item.url)) {
      const imageResponse = await fetch(item.url, { signal, redirect: 'follow' })
      if (!imageResponse.ok) throw new Error(`Generated image download failed (HTTP ${imageResponse.status})`)
      bytes = await responseBytes(imageResponse, maxBytes, 'Generated image')
    } else {
      throw new Error('Image API item has neither b64_json nor an HTTPS url')
    }
    const type = sniffImage(bytes)
    if (!type) throw new Error('Image API returned an unrecognized image format')
    outputs.push({ bytes, type, revisedPrompt: typeof item.revised_prompt === 'string' ? item.revised_prompt : undefined })
  }
  return outputs
}

function stamp() {
  return new Date().toISOString().replace(/[-:]/gu, '').replace(/\.\d{3}Z$/u, 'Z')
}

async function persistImages(workspace, operation, prompt, model, parameters, source, images, batch = `${stamp()}-${randomBytes(4).toString('hex')}`) {
  const directory = join(workspace, OUTPUT_DIRECTORY)
  await mkdir(directory, { recursive: true, mode: 0o700 })
  const results = []
  for (let index = 0; index < images.length; index += 1) {
    const image = images[index]
    const stem = `${operation}-${batch}-${index + 1}`
    const path = join(directory, `${stem}${image.type.ext}`)
    const temp = join(directory, `.${stem}.${randomBytes(3).toString('hex')}.tmp`)
    await writeFile(temp, image.bytes, { mode: 0o600, flag: 'wx' })
    await rename(temp, path)
    const metadataPath = join(directory, `${stem}.json`)
    const metadata = {
      operation,
      createdAt: new Date().toISOString(),
      model,
      prompt,
      parameters,
      ...(source ? { source } : {}),
      ...(image.revisedPrompt ? { revisedPrompt: image.revisedPrompt } : {}),
      image: {
        path,
        mimeType: image.type.mime,
        bytes: image.bytes.length,
        sha256: createHash('sha256').update(image.bytes).digest('hex'),
      },
    }
    await writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`, { mode: 0o600, flag: 'wx' })
    results.push({ path, metadataPath, mimeType: image.type.mime, bytes: image.bytes.length, ...(image.revisedPrompt ? { revisedPrompt: image.revisedPrompt } : {}) })
  }
  return results
}

function outputSchema() {
  return {
    type: 'object',
    additionalProperties: false,

    properties: {
      model: {
        type: 'string',
      },

      images: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,

          properties: {
            path: {
              type: 'string',
            },
            metadataPath: {
              type: 'string',
            },
            mimeType: {
              type: 'string',
            },
            bytes: {
              type: 'integer',
            },
            revisedPrompt: {
              type: 'string',
            },
            image: {
              type: 'object',
              additionalProperties: false,

              properties: {
                attachmentId: {
                  type: 'string',
                },
                mediaType: {
                  type: 'string',
                },
                bytes: {
                  type: 'integer',
                },
                width: {
                  type: 'integer',
                },
                height: {
                  type: 'integer',
                },
                name: {
                  type: 'string',
                },
              },
            },
          },

          // images 数组中的每个元素都是 object，
          // 所以 required 放在这里
          required: [
            'path',
            'metadataPath',
            'mimeType',
            'bytes',
          ],
        },
      },
    },

    // 最外层也是 object，
    // 所以 required 放在这里
    required: [
      'model',
      'images',
    ],
  }
}

function renderResult(value) {
  const blocks = [{ type: 'text', text: [`Created ${value.images.length} image(s) with ${value.model}:`, ...value.images.map((image) => `- ${image.path}\n  metadata: ${image.metadataPath}`), 'Use modlens_read_image on a result path when visual verification is needed.'].join('\n') }]
  for (const image of value.images) {
    if (image.image !== undefined) {
      blocks.push({ type: 'image', attachment: image.image })
    }
  }
  return blocks
}

/**
* Register generated images as durable DSH attachments so the Web GUI can
* render them inline. Mirrors the native `read_image` attachment protocol:
* `attachments.saveImage` commits immutable bytes and returns a content
* addressed ref that the frontend resolves via `media/<attachmentId>`.
* @param ctx - the plugin context; `attachments` service is optional.
* @param operation - tool operation name used in the attachment display name.
* @param batch - shared batch stamp so attachment names match saved filenames.
* @param images - the parsed API images (raw bytes + sniffed type).
* @param saved - the persisted results to annotate with `image` refs.
*/
async function attachImages(ctx, operation, batch, images, saved) {
  const attachments = ctx.get('attachments')
  if (attachments === void 0) return
  const refs = await Promise.all(images.map(async (image, index) => {
    try {
      return await attachments.saveImage({
        data: image.bytes,
        mediaType: image.type.mime,
        name: `${operation}-${batch}-${index + 1}${image.type.ext}`,
      })
    } catch {
      return void 0
    }
  }))
  for (let index = 0; index < saved.length; index += 1) {
    const ref = refs[index]
    if (ref !== void 0) {
      saved[index].image = {
        attachmentId: ref.attachmentId,
        mediaType: ref.mediaType,
        bytes: ref.bytes,
        width: ref.width,
        height: ref.height,
        ...(ref.name === void 0 ? {} : { name: ref.name }),
      }
    }
  }
}

function presentCall(title, args, locations = []) {
  return { card: 'generic', title, kind: 'write', rawInput: args, ...(locations.length > 0 ? { locations } : {}) }
}

export function apply(ctx, rawConfig = {}) {
  const config = {
    baseURL: rawConfig.baseURL ?? DEFAULT_BASE_URL,
    apiKeyEnv: rawConfig.apiKeyEnv ?? DEFAULT_API_KEY_ENV,
    model: rawConfig.model ?? DEFAULT_MODEL,
    timeoutMs: rawConfig.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    maxInputBytes: rawConfig.maxInputBytes ?? DEFAULT_MAX_INPUT_BYTES,
    maxOutputBytes: rawConfig.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES,
    maxRetries: rawConfig.maxRetries ?? 1,
  }

  ctx.tools.register({
    name: 'generate_image',
    description: 'Generate one or more images from a text instruction. Saves results under the current workspace and returns local paths suitable for modlens_read_image.',
    parameters: {
      type: 'object',
      properties: {
        prompt: { type: 'string', description: 'Detailed image generation instruction.' },
        size: { type: 'string', description: 'Optional provider-supported size such as 1024x1024, 1536x1024, or auto.' },
        quality: { type: 'string', description: 'Optional provider-supported quality such as low, medium, high, or auto.' },
        background: { type: 'string', description: 'Optional provider-supported background mode such as transparent, opaque, or auto.' },
        output_format: { type: 'string', description: 'Optional output format such as png, jpeg, or webp.' },
        n: { type: 'integer', description: 'Number of images, from 1 to 4. Defaults to 1.' },
      },
      required: ['prompt'],
    },
    output: { schema: outputSchema(), render: (_args, value) => renderResult(value) },
    timeoutMs: config.timeoutMs,
    isConcurrencySafe: () => true,
    presentCall: (args) => presentCall('Generate image', args),
    async execute(args, exec) {
      const prompt = nonBlank(args.prompt, 'prompt')
      const n = args.n === undefined ? 1 : args.n
      if (!Number.isInteger(n) || n < 1 || n > 4) throw new Error('n must be an integer from 1 to 4')
      const workspace = workspaceOf(exec)
      const apiKey = await resolveApiKey(ctx, config.apiKeyEnv)
      const parameters = { n, ...optionalGenerationFields(args) }
      const body = { model: config.model, prompt, n, ...optionalGenerationFields(args) }
      const response = await requestWithRetry(endpoint(config.baseURL, 'generations'), { method: 'POST', headers: { authorization: `Bearer ${apiKey}`, 'content-type': 'application/json' }, body: JSON.stringify(body) }, exec.signal, config.maxRetries)
      const images = await parseApiImages(response, config.maxOutputBytes, exec.signal)
      const batch = `${stamp()}-${randomBytes(4).toString('hex')}`
      const saved = await persistImages(workspace, 'generate', prompt, config.model, parameters, undefined, images, batch)
      await attachImages(ctx, 'generate', batch, images, saved)
      return { model: config.model, images: saved }
    },
  })

  ctx.tools.register({
    name: 'edit_image',
    description: 'Edit an existing image according to a text instruction. Accepts a workspace-local image or HTTPS URL and an optional workspace-local/HTTPS mask, then saves edited images under the current workspace.',
    parameters: {
      type: 'object',
      properties: {
        image: { type: 'string', description: 'Source image path inside the current workspace, or an HTTPS URL.' },
        prompt: { type: 'string', description: 'Precise editing instruction including what must remain unchanged.' },
        mask: { type: 'string', description: 'Optional mask image path inside the workspace, or HTTPS URL.' },
        size: { type: 'string', description: 'Optional provider-supported output size.' },
        quality: { type: 'string', description: 'Optional provider-supported quality.' },
        output_format: { type: 'string', description: 'Optional output format such as png, jpeg, or webp.' },
        n: { type: 'integer', description: 'Number of edited images, from 1 to 4. Defaults to 1.' },
      },
      required: ['image', 'prompt'],
    },
    output: { schema: outputSchema(), render: (_args, value) => renderResult(value) },
    timeoutMs: config.timeoutMs,
    isConcurrencySafe: () => true,
    presentCall: (args) => presentCall('Edit image', args, typeof args?.image === 'string' && !/^https:\/\//i.test(args.image) ? [{ path: args.image }] : []),
    async execute(args, exec) {
      const prompt = nonBlank(args.prompt, 'prompt')
      const n = args.n === undefined ? 1 : args.n
      if (!Number.isInteger(n) || n < 1 || n > 4) throw new Error('n must be an integer from 1 to 4')
      const workspace = workspaceOf(exec)
      const source = await readImageInput(args.image, workspace, config.maxInputBytes, exec.signal, 'image')
      const mask = args.mask ? await readImageInput(args.mask, workspace, config.maxInputBytes, exec.signal, 'mask') : undefined
      const apiKey = await resolveApiKey(ctx, config.apiKeyEnv)
      const form = new FormData()
      form.set('model', config.model)
      form.set('prompt', prompt)
      form.set('n', String(n))
      form.set('image', new Blob([source.bytes], { type: source.type.mime }), basename(source.source).slice(0, 120) || `image${source.type.ext}`)
      if (mask) form.set('mask', new Blob([mask.bytes], { type: mask.type.mime }), basename(mask.source).slice(0, 120) || `mask${mask.type.ext}`)
      for (const [key, value] of Object.entries(optionalGenerationFields(args))) form.set(key, value)
      const response = await requestWithRetry(endpoint(config.baseURL, 'edits'), { method: 'POST', headers: { authorization: `Bearer ${apiKey}` }, body: form }, exec.signal, config.maxRetries)
      const images = await parseApiImages(response, config.maxOutputBytes, exec.signal)
      const parameters = { n, ...optionalGenerationFields(args), mask: mask?.source }
      const batch = `${stamp()}-${randomBytes(4).toString('hex')}`
      const saved = await persistImages(workspace, 'edit', prompt, config.model, parameters, source.source, images, batch)
      await attachImages(ctx, 'edit', batch, images, saved)
      return { model: config.model, images: saved }
    },
  })
}
