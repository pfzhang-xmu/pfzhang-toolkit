# DSH Image Tools

Host-side Cordis plugin for DeepSeek Harness that registers OpenAI Images-compatible `generate_image` and `edit_image` tools for DSH agents, and saves outputs under `<session workspace>/.dsh-images/`.

This plugin talks to any OpenAI Images-compatible endpoint. The endpoint URL, API key, and model are **placeholders in the published source** — you must configure your own.

## Configuration

Pick a host plugin entry for `dsh-image-tools/index.js` (e.g. in your profile's `cordis.patch.yml`):

```yaml
- insert:
    - id: image-tools
      name: /absolute/path/to/dsh-image-tools/index.js
      config:
        baseURL: https://your-openai-compatible-host/v1   # REQUIRED — replace with your endpoint
        apiKeyEnv: IMAGE_API_KEY                           # env var name that holds the API key
        model: gpt-image-1                                 # your provider's image model id
        timeoutMs: 180000
        maxRetries: 1
```

Store the API key in the DSH credential store under your `apiKeyEnv` name (e.g. `IMAGE_API_KEY`), or export that variable before launching DSH. **Never put the key in the Cordis composition.**

> The shipped defaults are placeholders: `baseURL: https://api.example.com/v1`, `apiKeyEnv: IMAGE_API_KEY`, `model: gpt-image-1`. A request fails with a clear error until you set a real endpoint and key.

## Tools

- `generate_image`: sends JSON to `<baseURL>/images/generations`.
- `edit_image`: sends multipart form data to `<baseURL>/images/edits`, with `image` and optional `mask` files.

Both tools accept only provider-supported optional parameters. Local edit inputs must remain inside the active session workspace or a private `modlens-dsh-paste-*` temporary directory. HTTPS image inputs are supported. Responses may contain `b64_json` or HTTPS `url` fields.

Each output has a neighboring JSON metadata file containing the operation, prompt, model, parameters, source path for edits, MIME type, byte count, and SHA-256 digest. Credentials are resolved per request and never written to output metadata.

## Security boundaries

- The API key is read from the DSH credential store or the environment on every request; it is never written into files, metadata, or the Cordis composition.
- Local file inputs are resolved and validated to stay inside the session workspace (or a Modlens paste directory).
- Output bytes are size-limited (25 MB input / 50 MB output by default) and MIME-sniffed to PNG/JPEG/WebP/GIF.

## Verification

```bash
node --test dsh-image-tools/index.test.js
```

After changing the Host profile, restart the running DSH Web process and refresh `http://127.0.0.1:3080`. This is a Host-only plugin, so no client bundle rebuild is required.

## Usage

Both tools are ordinary agent tools: the model calls them directly in a turn, and the results are returned as local paths (plus inline attachments when the host supports them).

**Generate an image**

```text
生成一张 16:9 的赛博朋克城市夜景插画，霓虹蓝紫配色，无文字。
```

The agent calls `generate_image({ prompt, size: "1536x1024" })` and replies with the saved path under `.dsh-images/` (e.g. `generate-20260101T000000Z-xxxx-1.png`), ready for `modlens_read_image` or further editing.

**Edit / refine an image**

```text
把 /Users/me/project/.dsh-images/xxx.png 里的红色箭头全部去掉，保持其余内容不变
```

The agent calls `edit_image({ image: <path>, prompt: "…" })`; a new result is saved next to the original. `mask` is optional for region-restricted edits.

**Typical agent workflow with a canvas (dsh-cowart)**

1. `generate_image` produces a bitmap at the target aspect ratio.
2. `cowart_insert_image` (from the `dsh-cowart` plugin) copies it into the canvas page assets and replaces the selected AI image frame.
3. After annotation, `edit_image` re-renders the image from the annotated screenshot; `cowart_insert_image` places the result beside the original.

See the repo root README for the full **generate → annotate → refine** walkthrough.
