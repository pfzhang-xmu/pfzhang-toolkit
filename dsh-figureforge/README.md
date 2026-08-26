# FigureForge

A browser-local scientific figure editor for PNG composition, annotations, and publication-ready exports. FigureForge is a self-contained Vite + React + TypeScript application and does not upload image data to a server.

## Features

- Upload a PNG base image and inspect dimensions/DPI.
- Move, resize, crop, and import local image elements.
- Rectangular cutout and erase tools using surrounding-color coverage.
- Add and edit text with font family, size, color, rotation, bold, and thin styles.
- Undo with Ctrl/Cmd+Z; delete with Delete/Backspace.
- Export PNG and TIFF; save/load `.nanopro.json`-compatible project files.
- Fixed-aspect image-element resizing enabled by default, with an optional free-transform mode.

## Requirements

- Node.js 18 or newer (Node.js 20 LTS recommended)
- npm 9 or newer
- A modern desktop browser: Chrome, Edge, Firefox, or Safari

## Install and run

From this directory:

```bash
npm install
npm run dev
``

Vite opens the development URL automatically. If it does not, open the URL printed in the terminal (normally http://127.0.0.1:5173/).

For a production build:

```bash
npm run build
npm run preview
```

The generated `dist/` directory is static and can be served by Nginx, GitHub Pages, Vercel, or any static web server.

## Usage

1. Choose a PNG base image.
2. Select a tool from the left panel.
3. Use **移动对象** to select and move text or local image elements. Drag the larger blue handle at the lower-right corner to resize an image element. The **固定长宽比例** option is enabled by default.
4. Use **框选切出**, **擦除区域**, and **裁剪图片** for region operations.
5. Export the finished figure or save a project JSON for later editing.

## Privacy and security

- Image data, imported elements, and project files are processed in the browser with FileReader and Canvas.
- The app has no backend API, account system, analytics, or automatic image upload.
- Project JSON contains embedded Data URLs; treat saved project files as sensitive if the source image is confidential.
- Only open project files you trust; loading a project JSON decodes embedded image data into the browser.

## Repository development

```bash
npm install
npm run build
```

The build runs TypeScript checking followed by Vite production bundling. Keep generated `dist/` and dependency `node_modules/` out of commits.

## License

MIT. See [LICENSE](./LICENSE).
