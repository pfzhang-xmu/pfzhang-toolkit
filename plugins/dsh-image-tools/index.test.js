import assert from 'node:assert/strict'
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { apply } from './index.js'

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')

function harness(config = {}) {
  const tools = new Map()
  const ctx = {
    tools: { register(tool) { tools.set(tool.name, tool); return () => tools.delete(tool.name) } },
    get(name) {
      if (name === 'credentials') return { async resolve(ref) { return ref === 'TEST_IMAGE_KEY' ? { value: 'test-secret' } : undefined } }
      return undefined
    },
  }
  apply(ctx, { baseURL: 'https://images.example/v1', apiKeyEnv: 'TEST_IMAGE_KEY', model: 'gpt-image-1', maxRetries: 0, ...config })
  return tools
}

async function withWorkspace(run) {
  const workspace = await mkdtemp(join(tmpdir(), 'dsh-image-tools-test-'))
  try {
    return await run(workspace)
  } finally {
    await rm(workspace, { recursive: true, force: true })
  }
}

function exec(workspace) {
  return { signal: new AbortController().signal, agent: { session: { header: { cwd: workspace } } } }
}

test('generate_image sends JSON and persists image plus safe metadata', async () => {
  await withWorkspace(async (workspace) => {
    const originalFetch = globalThis.fetch
    let request
    globalThis.fetch = async (url, init) => {
      request = { url, init }
      return new Response(JSON.stringify({ data: [{ b64_json: PNG.toString('base64'), revised_prompt: 'refined' }] }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    try {
      const tool = harness().get('generate_image')
      const result = await tool.execute({ prompt: 'A precise test image', size: '1024x1024' }, exec(workspace))
      assert.equal(request.url, 'https://images.example/v1/images/generations')
      assert.equal(request.init.headers.authorization, 'Bearer test-secret')
      assert.deepEqual(JSON.parse(request.init.body), { model: 'gpt-image-1', prompt: 'A precise test image', n: 1, size: '1024x1024' })
      assert.equal(result.images.length, 1)
      assert.deepEqual(await readFile(result.images[0].path), PNG)
      const metadata = JSON.parse(await readFile(result.images[0].metadataPath, 'utf8'))
      assert.equal(metadata.operation, 'generate')
      assert.equal(metadata.model, 'gpt-image-1')
      assert.equal(metadata.image.mimeType, 'image/png')
      assert.equal(JSON.stringify(metadata).includes('test-secret'), false)
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})

test('edit_image sends multipart source and optional mask', async () => {
  await withWorkspace(async (workspace) => {
    await writeFile(join(workspace, 'source.png'), PNG)
    await writeFile(join(workspace, 'mask.png'), PNG)
    const originalFetch = globalThis.fetch
    let request
    globalThis.fetch = async (url, init) => {
      request = { url, init }
      return new Response(JSON.stringify({ data: [{ b64_json: PNG.toString('base64') }] }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    try {
      const tool = harness().get('edit_image')
      const result = await tool.execute({ image: 'source.png', mask: 'mask.png', prompt: 'Change only the background', output_format: 'png' }, exec(workspace))
      assert.equal(request.url, 'https://images.example/v1/images/edits')
      assert.equal(request.init.headers.authorization, 'Bearer test-secret')
      assert.equal(request.init.headers['content-type'], undefined)
      assert.equal(request.init.body.get('model'), 'gpt-image-1')
      assert.equal(request.init.body.get('prompt'), 'Change only the background')
      assert.equal(request.init.body.get('image').type, 'image/png')
      assert.equal(request.init.body.get('mask').type, 'image/png')
      assert.equal(result.images.length, 1)
      const metadata = JSON.parse(await readFile(result.images[0].metadataPath, 'utf8'))
      assert.equal(metadata.operation, 'edit')
      assert.equal(metadata.source, await realpath(join(workspace, 'source.png')))
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})

test('edit_image accepts Modlens paste paths', async () => {
  await withWorkspace(async (workspace) => {
    const pasteDirectory = await mkdtemp(join(tmpdir(), 'modlens-dsh-paste-'))
    const source = join(pasteDirectory, 'paste.png')
    await writeFile(source, PNG)
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => new Response(JSON.stringify({ data: [{ b64_json: PNG.toString('base64') }] }), { status: 200 })
    try {
      const tool = harness().get('edit_image')
      const result = await tool.execute({ image: source, prompt: 'edit' }, exec(workspace))
      assert.equal(result.images.length, 1)
    } finally {
      globalThis.fetch = originalFetch
      await rm(pasteDirectory, { recursive: true, force: true })
    }
  })
})

test('edit_image rejects unrelated files outside the session workspace', async () => {
  await withWorkspace(async (workspace) => {
    const tool = harness().get('edit_image')
    await assert.rejects(() => tool.execute({ image: '/tmp/outside.png', prompt: 'edit' }, exec(workspace)), /workspace or a Modlens paste directory/)
  })
})

test('missing credential fails without exposing a literal secret', async () => {
  const tools = new Map()
  apply({ tools: { register(tool) { tools.set(tool.name, tool) } }, get() { return undefined } }, { apiKeyEnv: 'UNSET_TEST_KEY' })
  await withWorkspace(async (workspace) => {
    await assert.rejects(() => tools.get('generate_image').execute({ prompt: 'test' }, exec(workspace)), /configure UNSET_TEST_KEY/)
  })
})
