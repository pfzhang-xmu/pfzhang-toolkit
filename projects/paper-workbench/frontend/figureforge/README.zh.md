# FigureForge

FigureForge 是一个在浏览器本地运行的科研图片编辑器，用于 PNG 图片合成、标注和论文配图导出。项目采用 Vite + React + TypeScript 构建，不会自动将图片上传到服务器。

## 功能

- 上传 PNG 底图并查看尺寸和 DPI 信息。
- 导入本地图片元素，移动、缩放、裁剪。
- 框选切出和擦除区域，并使用周围背景色覆盖原位置。
- 添加和编辑文字，支持字体、字号、颜色、旋转、加粗和变细。
- Ctrl/Cmd+Z 撤销，Delete/Backspace 删除选中对象。
- 导出 PNG、TIFF；保存和加载兼容 `.nanopro.json` 的工程文件。
- 图片元素默认固定长宽比例缩放，也可在左侧面板关闭固定比例。

## 环境要求

- Node.js 18 或更高版本（推荐 Node.js 20 LTS）
- npm 9 或更高版本
- Chrome、Edge、Firefox 或 Safari 等现代桌面浏览器

## 安装与运行

进入本目录后执行：

```bash
npm install
npm run dev
```

Vite 通常会自动打开开发地址；如果没有自动打开，请访问终端输出的地址（通常是 http://127.0.0.1:5173/）。

生产构建：

```bash
npm run build
npm run preview
```

生成的 `dist/` 是静态文件，可部署到 Nginx、GitHub Pages、Vercel 或其他静态服务器。

## 使用方法

1. 选择 PNG 底图。
2. 在左侧选择工具。
3. 使用「移动对象」选中文字或本地图片元素。拖动右下角较大的蓝色手柄可以缩放图片元素；「固定长宽比例」默认开启。
4. 使用「框选切出」「擦除区域」「裁剪图片」完成区域操作。
5. 导出图片，或保存工程 JSON 以便之后继续编辑。

## 隐私与安全

- 图片、导入元素和工程文件通过浏览器 FileReader 与 Canvas 在本地处理。
- 项目没有后端 API、账号系统、统计脚本或自动图片上传功能。
- 工程 JSON 会内嵌图片 Data URL；涉及保密数据时请妥善保存。
- 只加载可信来源的工程 JSON；加载时会在浏览器中解码其中的图片数据。

## 仓库开发

```bash
npm install
npm run build
```

构建会先执行 TypeScript 检查，再执行 Vite 生产打包。不要将 `dist/` 和 `node_modules/` 提交到仓库。

## 许可证

MIT，详见 [LICENSE](./LICENSE)。
