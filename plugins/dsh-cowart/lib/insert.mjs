import { copyFile, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { basename, extname, join, relative, resolve, sep } from "node:path";
import { generateKeyBetween } from "fractional-indexing";

import {
  nonEmptyString,
  pageAssetUrl,
  pageDirName,
  pathResolve,
  readCowartCanvasState,
  readCowartPageAsset,
  readCowartSelectionState,
  readCowartViewState,
  resolveCanvasDir,
  resolveCowartPaths,
  saveCowartCanvasSnapshot,
  writeCowartPageAsset,
} from "./canvas-storage.mjs";

const PAGE_ID_PREFIX = "page:";
const COWART_HTML_DRAFT_URL_ORIGIN = "http://cowart.local";

function finiteNumber(value, fallback) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function isSafeChildPath(parent, child) {
  const pathToChild = relative(parent, child);
  return pathToChild && !pathToChild.startsWith("..") && !pathToChild.includes(`..${sep}`);
}

function sanitizeFileName(name, fallbackName = "image.png") {
  const rawName = basename(String(name || fallbackName));
  const extension = extname(rawName) || extname(fallbackName) || ".png";
  const baseName = rawName
    .slice(0, rawName.length - extname(rawName).length)
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${baseName || "image"}${extension}`;
}

function sanitizeDirectoryName(name, fallbackName = "Cowart Export") {
  return basename(String(name || fallbackName))
    .replace(/[<>:"/\\|?*\u0000-\u001f]+/g, "-")
    .replace(/[. ]+$/g, "")
    .trim()
    .slice(0, 120) || fallbackName;
}

function sanitizeHtmlFileName(name, fallbackName = "draft.html") {
  const safeName = sanitizeFileName(name, fallbackName);
  return /\.html?$/i.test(safeName) ? safeName : `${safeName.replace(/\.[^.]+$/, "")}.html`;
}

function sanitizeIdPart(value, fallback = "image") {
  return String(value || fallback)
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80) || fallback;
}

function mimeTypeForFile(filePath) {
  switch (extname(filePath).toLowerCase()) {
    case ".apng":
      return "image/apng";
    case ".avif":
      return "image/avif";
    case ".gif":
      return "image/gif";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".png":
      return "image/png";
    case ".webp":
      return "image/webp";
    case ".svg":
      return "image/svg+xml";
    case ".htm":
    case ".html":
      return "text/html";
    default:
      return "application/octet-stream";
  }
}

function parseDownloadDataUrl(dataUrl) {
  const match = /^data:([^;,]+)?((?:;[^,]*)?),(.*)$/s.exec(String(dataUrl || ""));
  if (!match) throw new Error("Invalid download dataUrl.");
  const mimeType = nonEmptyString(match[1]) || "application/octet-stream";
  const parameters = match[2] || "";
  const payload = match[3] || "";
  const buffer = /;base64(?:;|$)/i.test(parameters)
    ? Buffer.from(payload, "base64")
    : Buffer.from(decodeURIComponent(payload), "utf8");
  return { buffer, mimeType };
}

async function uniqueFilePath(dir, requestedName) {
  const safeName = sanitizeFileName(requestedName);
  const ext = extname(safeName);
  const base = safeName.slice(0, safeName.length - ext.length);
  let candidate = safeName;
  let counter = 2;
  while (true) {
    const candidatePath = join(dir, candidate);
    try {
      await stat(candidatePath);
      candidate = `${base}-v${counter}${ext}`;
      counter += 1;
    } catch (error) {
      if (error?.code === "ENOENT") return { fileName: candidate, filePath: candidatePath };
      throw error;
    }
  }
}

async function uniqueDirectoryPath(dir, requestedName) {
  const safeName = sanitizeDirectoryName(requestedName);
  let candidate = safeName;
  let counter = 2;
  while (true) {
    const candidatePath = join(dir, candidate);
    try {
      await stat(candidatePath);
      candidate = `${safeName}-${counter}`;
      counter += 1;
    } catch (error) {
      if (error?.code === "ENOENT") {
        return { directoryName: candidate, directoryPath: candidatePath };
      }
      throw error;
    }
  }
}

function uniqueRecordId(store, prefix, seed) {
  const cleanSeed = sanitizeIdPart(seed);
  let candidate = `${prefix}:${cleanSeed}`;
  let counter = 2;
  while (store[candidate]) {
    candidate = `${prefix}:${cleanSeed}-${counter}`;
    counter += 1;
  }
  return candidate;
}

function getRecord(store, id, label) {
  const record = store[id];
  if (!record) throw new Error(`Missing ${label}: ${id}`);
  return record;
}

function findPageIdForShape(store, shapeId) {
  let record = getRecord(store, shapeId, "shape");
  const visited = new Set();
  while (record && !visited.has(record.id)) {
    visited.add(record.id);
    if (record.typeName === "page") return record.id;
    const parentId = record.parentId;
    if (!parentId) break;
    const parent = store[parentId];
    if (parent?.typeName === "page") return parent.id;
    record = parent;
  }
  return null;
}

function getPageShapes(store, pageId) {
  const shapes = [];
  const byParent = new Map();
  for (const record of Object.values(store)) {
    if (record?.typeName !== "shape") continue;
    const siblings = byParent.get(record.parentId) ?? [];
    siblings.push(record);
    byParent.set(record.parentId, siblings);
  }
  const queue = [...(byParent.get(pageId) ?? [])];
  while (queue.length > 0) {
    const shape = queue.shift();
    shapes.push(shape);
    queue.push(...(byParent.get(shape.id) ?? []));
  }
  return shapes;
}

function localBoundsForShape(shape) {
  if (!shape || shape.typeName !== "shape") return null;
  if (shape.type === "arrow") {
    const start = shape.props?.start ?? { x: 0, y: 0 };
    const end = shape.props?.end ?? { x: 0, y: 0 };
    const minX = Math.min(start.x ?? 0, end.x ?? 0);
    const minY = Math.min(start.y ?? 0, end.y ?? 0);
    const maxX = Math.max(start.x ?? 0, end.x ?? 0);
    const maxY = Math.max(start.y ?? 0, end.y ?? 0);
    return { x: minX, y: minY, w: Math.max(1, maxX - minX), h: Math.max(1, maxY - minY) };
  }
  const w = finiteNumber(shape.props?.w, shape.type === "text" ? 160 : 1);
  const h = finiteNumber(shape.props?.h, shape.type === "text" ? 40 : 1);
  return { x: 0, y: 0, w, h };
}

function pageBoundsForShape(store, shape) {
  const local = localBoundsForShape(shape);
  if (!local) return null;
  let x = finiteNumber(shape.x, 0) + local.x;
  let y = finiteNumber(shape.y, 0) + local.y;
  let parent = store[shape.parentId];
  const visited = new Set([shape.id]);
  while (parent?.typeName === "shape" && !visited.has(parent.id)) {
    visited.add(parent.id);
    x += finiteNumber(parent.x, 0);
    y += finiteNumber(parent.y, 0);
    parent = store[parent.parentId];
  }
  return { x, y, w: local.w, h: local.h };
}

function rectsOverlap(a, b, padding = 0) {
  return !(
    a.x + a.w + padding <= b.x ||
    b.x + b.w + padding <= a.x ||
    a.y + a.h + padding <= b.y ||
    b.y + b.h + padding <= a.y
  );
}

function chooseIndex(store, parentId) {
  const siblingIndexes = Object.values(store)
    .filter((record) => record?.typeName === "shape" && record.parentId === parentId && typeof record.index === "string")
    .map((record) => record.index)
    .sort();
  return generateKeyBetween(siblingIndexes.at(-1) ?? null, null);
}

function firstSelectedShapeId(selection) {
  return selection?.selectedShapes?.length === 1 ? selection.selectedShapes[0]?.id : null;
}

function isAiImageHolderShape(shape) {
  return shape?.typeName === "shape" && shape.meta?.cowartAiImageHolder === true;
}

function isAiDraftHolderShape(shape) {
  return shape?.typeName === "shape" && shape.meta?.cowartAiDraftHolder === true;
}

function isAiSlidesShape(shape) {
  return shape?.typeName === "shape" && shape.meta?.cowartAiSlides === true;
}

function isCowartHtmlDraftShape(shape) {
  return shape?.typeName === "shape" && shape.type === "embed" && (
    shape.meta?.cowartHtmlDraft === true ||
    /^data:text\/html(?:;[^,]*)?,/i.test(String(shape.props?.url || ""))
  );
}

function cowartHtmlDraftVirtualUrl(assetUrl) {
  return `${COWART_HTML_DRAFT_URL_ORIGIN}${assetUrl}`;
}

function cowartHtmlDraftDataUrl(htmlContent) {
  return `data:text/html;base64,${Buffer.from(String(htmlContent || ""), "utf8").toString("base64")}`;
}

function collectDescendantShapeIds(store, shapeId) {
  if (!shapeId) return [];
  const byParent = new Map();
  for (const record of Object.values(store)) {
    if (record?.typeName !== "shape") continue;
    const children = byParent.get(record.parentId) ?? [];
    children.push(record.id);
    byParent.set(record.parentId, children);
  }

  const descendants = [];
  const queue = [...(byParent.get(shapeId) ?? [])];
  const visited = new Set([shapeId]);
  while (queue.length > 0) {
    const childId = queue.shift();
    if (!childId || visited.has(childId)) continue;
    visited.add(childId);
    descendants.push(childId);
    queue.push(...(byParent.get(childId) ?? []));
  }
  return descendants;
}

function choosePlacement({ store, pageId, parentId, anchorShape, width, height, margin, placement }) {
  const anchorBounds = anchorShape ? pageBoundsForShape(store, anchorShape) : null;
  let x = anchorBounds ? anchorBounds.x + anchorBounds.w + margin : 0;
  let y = anchorBounds ? anchorBounds.y : 0;

  if (placement === "left" && anchorBounds) x = anchorBounds.x - width - margin;
  if (placement === "below" && anchorBounds) {
    x = anchorBounds.x;
    y = anchorBounds.y + anchorBounds.h + margin;
  }

  const pageShapes = getPageShapes(store, pageId);
  const obstacles = pageShapes
    .filter((shape) => shape.parentId === parentId && shape.id !== anchorShape?.id)
    .map((shape) => pageBoundsForShape(store, shape))
    .filter(Boolean);

  const stepX = Math.max(width + margin, 1);
  const stepY = Math.max(height + margin, 1);
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const candidate = { x, y, w: width, h: height };
    if (!obstacles.some((bounds) => rectsOverlap(candidate, bounds, margin / 2))) return candidate;
    if (placement === "below") y += stepY;
    else if (placement === "left") x -= stepX;
    else x += stepX;
  }

  return { x, y, w: width, h: height };
}

async function getImageDimensions(filePath) {
  const buffer = await readFile(filePath);
  if (buffer.length >= 24 && buffer.toString("ascii", 1, 4) === "PNG") {
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  }
  if (buffer.length >= 10 && buffer[0] === 0xff && buffer[1] === 0xd8) {
    let offset = 2;
    while (offset < buffer.length) {
      if (buffer[offset] !== 0xff) break;
      const marker = buffer[offset + 1];
      const size = buffer.readUInt16BE(offset + 2);
      if ((marker >= 0xc0 && marker <= 0xc3) || (marker >= 0xc5 && marker <= 0xc7) || (marker >= 0xc9 && marker <= 0xcb) || (marker >= 0xcd && marker <= 0xcf)) {
        return { width: buffer.readUInt16BE(offset + 7), height: buffer.readUInt16BE(offset + 5) };
      }
      offset += 2 + size;
    }
  }
  if (buffer.length >= 30 && buffer.toString("ascii", 0, 4) === "RIFF" && buffer.toString("ascii", 8, 12) === "WEBP") {
    const chunk = buffer.toString("ascii", 12, 16);
    if (chunk === "VP8X") {
      return {
        width: 1 + buffer.readUIntLE(24, 3),
        height: 1 + buffer.readUIntLE(27, 3),
      };
    }
  }
  throw new Error(`Could not read image dimensions for ${filePath}. Pass displayWidth/displayHeight and use a PNG/JPEG/WebP source.`);
}

async function insertCowartImage(args = {}) {
  const imagePath = nonEmptyString(args.imagePath);
  if (!imagePath) throw new Error("imagePath is required.");

  const sourceImagePath = pathResolve(imagePath);
  const sourceStat = await stat(sourceImagePath);
  if (!sourceStat.isFile()) throw new Error(`imagePath is not a file: ${sourceImagePath}`);

  const canvasState = await readCowartCanvasState(args, { hydrateAssets: false });
  const snapshot = canvasState.snapshot;
  if (!snapshot || typeof snapshot !== "object" || !snapshot.schema || !snapshot.store) {
    throw new Error("No Cowart canvas snapshot exists yet. Open the Cowart widget for the target project and create or save the canvas before inserting images.");
  }

  const store = snapshot.store;
  const { selection } = await readCowartSelectionState(args);
  const { viewState } = await readCowartViewState(args);

  const anchorShapeId = nonEmptyString(args.anchorShapeId) || nonEmptyString(args.sourceShapeId) || firstSelectedShapeId(selection);
  const anchorShape = anchorShapeId ? getRecord(store, anchorShapeId, "anchor shape") : null;
  const pageId =
    nonEmptyString(args.pageId) ||
    (anchorShape ? findPageIdForShape(store, anchorShape.id) : null) ||
    nonEmptyString(viewState?.currentPageId) ||
    Object.values(store).find((record) => record?.typeName === "page")?.id;
  if (!pageId || !store[pageId]) throw new Error("Could not determine target pageId.");

  const imageSize = await getImageDimensions(sourceImagePath);
  const anchorBounds = anchorShape ? pageBoundsForShape(store, anchorShape) : null;
  const shouldTargetAiImageHolder = args.matchAnchor !== false && isAiImageHolderShape(anchorShape) && anchorBounds;
  const shouldReplaceAiImageHolder = shouldTargetAiImageHolder && args.replaceAiImageHolder !== false;
  const shouldFillAiImageHolder = shouldTargetAiImageHolder && !shouldReplaceAiImageHolder;
  const matchAnchor = args.matchAnchor !== false && anchorBounds;
  const width = shouldTargetAiImageHolder
    ? anchorBounds.w
    : finiteNumber(args.displayWidth, matchAnchor ? anchorBounds.w : Math.min(imageSize.width, 512));
  const height = shouldTargetAiImageHolder
    ? anchorBounds.h
    : finiteNumber(
      args.displayHeight,
      matchAnchor ? anchorBounds.h : Math.round(width * (imageSize.height / imageSize.width)),
    );
  const margin = Math.max(0, finiteNumber(args.margin, 40));
  const placement = ["right", "left", "below"].includes(args.placement) ? args.placement : "right";
  let parentId = anchorShape?.parentId && store[anchorShape.parentId] ? anchorShape.parentId : pageId;
  let rotation = 0;
  let bounds = null;

  if (shouldFillAiImageHolder && anchorShape.type === "frame") {
    parentId = anchorShape.id;
    bounds = { x: 0, y: 0, w: width, h: height };
  } else if (shouldTargetAiImageHolder) {
    parentId = anchorShape.parentId && store[anchorShape.parentId] ? anchorShape.parentId : pageId;
    rotation = finiteNumber(anchorShape.rotation, 0);
    bounds = {
      x: finiteNumber(anchorShape.x, 0),
      y: finiteNumber(anchorShape.y, 0),
      w: width,
      h: height,
    };
  } else {
    parentId = anchorShape?.parentId && store[anchorShape.parentId]?.typeName === "page" ? anchorShape.parentId : pageId;
    bounds = choosePlacement({ store, pageId, parentId, anchorShape, width, height, margin, placement });
  }

  const canvasDir = resolveCanvasDir(args);
  const assetsDir = join(canvasDir, "pages", pageDirName(pageId), "assets");
  if (!isSafeChildPath(resolveCanvasDir(args), assetsDir)) {
    throw new Error(`Unsafe page assets directory: ${assetsDir}`);
  }
  const { fileName, filePath } = await uniqueFilePath(assetsDir, args.fileName || basename(sourceImagePath));
  const recordSeed = sanitizeIdPart(fileName);
  const assetId = uniqueRecordId(store, "asset", recordSeed);
  const shapeId = uniqueRecordId(store, "shape", recordSeed);
  const replacedShapeIds = shouldReplaceAiImageHolder && anchorShapeId
    ? [anchorShapeId, ...collectDescendantShapeIds(store, anchorShapeId)]
    : [];
  const replacedImageShapeIds = replacedShapeIds.filter((id) => store[id]?.typeName === "shape" && store[id]?.type === "image");
  const index = shouldReplaceAiImageHolder && typeof anchorShape?.index === "string"
    ? anchorShape.index
    : chooseIndex(store, parentId);
  const mimeType = mimeTypeForFile(fileName);

  const assetRecord = {
    id: assetId,
    typeName: "asset",
    type: "image",
    props: {
      name: fileName,
      src: pageAssetUrl(pageId, fileName),
      w: imageSize.width,
      h: imageSize.height,
      fileSize: sourceStat.size,
      mimeType,
      isAnimated: false,
    },
    meta: args.assetMeta && typeof args.assetMeta === "object" ? args.assetMeta : {},
  };

  const shapeMeta = args.shapeMeta && typeof args.shapeMeta === "object" ? { ...args.shapeMeta } : {};
  if (anchorShapeId && !shapeMeta.cowartAnnotationSourceShapeId) {
    shapeMeta.cowartAnnotationSourceShapeId = anchorShapeId;
  }
  if (shouldTargetAiImageHolder && anchorShapeId && !shapeMeta.cowartGeneratedForAiImageHolder) {
    shapeMeta.cowartGeneratedForAiImageHolder = anchorShapeId;
  }
  if (shouldReplaceAiImageHolder && anchorShapeId) {
    shapeMeta.cowartReplacedAiImageHolder = true;
  }
  if (nonEmptyString(args.annotationScreenshot) && !shapeMeta.cowartAnnotationScreenshot) {
    shapeMeta.cowartAnnotationScreenshot = nonEmptyString(args.annotationScreenshot);
  }

  const shapeRecord = {
    x: bounds.x,
    y: bounds.y,
    rotation,
    isLocked: false,
    opacity: 1,
    meta: shapeMeta,
    id: shapeId,
    type: "image",
    props: {
      w: width,
      h: height,
      assetId,
      playing: true,
      url: "",
      crop: null,
      flipX: false,
      flipY: false,
      altText: nonEmptyString(args.altText) || "Cowart inserted image",
    },
    parentId,
    index,
    typeName: "shape",
  };

  if (!args.dryRun) {
    await mkdir(assetsDir, { recursive: true });
    await copyFile(sourceImagePath, filePath);
    for (const replacedShapeId of replacedShapeIds) {
      delete store[replacedShapeId];
    }
    store[assetId] = assetRecord;
    store[shapeId] = shapeRecord;
    const saveArgs = replacedImageShapeIds.length > 0
      ? {
          ...args,
          acknowledgedImageShapeDeletes: Array.from(new Set([
            ...(Array.isArray(args.acknowledgedImageShapeDeletes) ? args.acknowledgedImageShapeDeletes : []),
            ...replacedImageShapeIds,
          ])),
        }
      : args;
    await saveCowartCanvasSnapshot(saveArgs, snapshot);
  }

  return {
    canvasDir,
    cowartUrl: nonEmptyString(args.cowartUrl),
    pageId,
    parentId,
    anchorShapeId,
    assetId,
    shapeId,
    index,
    sourceImagePath,
    assetFile: filePath,
    assetUrl: assetRecord.props.src,
    imageSize,
    bounds,
    replacedAiImageHolder: shouldReplaceAiImageHolder,
    replacedShapeIds,
    dryRun: Boolean(args.dryRun),
  };
}

async function insertCowartHtmlDraft(args = {}) {
  const htmlContent = nonEmptyString(args.htmlContent);
  const htmlPath = nonEmptyString(args.htmlPath);
  if (!htmlContent && !htmlPath) {
    throw new Error("htmlContent or htmlPath is required.");
  }

  const sourceHtmlPath = htmlPath ? pathResolve(htmlPath) : null;
  const finalHtml = htmlContent ?? await readFile(sourceHtmlPath, "utf8");
  if (!nonEmptyString(finalHtml)) {
    throw new Error("HTML draft content is empty.");
  }
  if (sourceHtmlPath) {
    const sourceStat = await stat(sourceHtmlPath);
    if (!sourceStat.isFile()) throw new Error(`htmlPath is not a file: ${sourceHtmlPath}`);
  }

  const canvasState = await readCowartCanvasState(args, { hydrateAssets: false });
  const snapshot = canvasState.snapshot;
  if (!snapshot || typeof snapshot !== "object" || !snapshot.schema || !snapshot.store) {
    throw new Error("No Cowart canvas snapshot exists yet. Open the Cowart widget for the target project and create or save the canvas before inserting HTML drafts.");
  }

  const store = snapshot.store;
  const { selection } = await readCowartSelectionState(args);
  const { viewState } = await readCowartViewState(args);

  const draftShapeId = nonEmptyString(args.draftShapeId) || nonEmptyString(args.anchorShapeId) || firstSelectedShapeId(selection);
  const draftShape = draftShapeId ? getRecord(store, draftShapeId, "AI draft holder shape") : null;
  const pageId =
    nonEmptyString(args.pageId) ||
    (draftShape ? findPageIdForShape(store, draftShape.id) : null) ||
    nonEmptyString(viewState?.currentPageId) ||
    Object.values(store).find((record) => record?.typeName === "page")?.id;
  if (!pageId || !store[pageId]) throw new Error("Could not determine target pageId.");

  const anchorBounds = draftShape ? pageBoundsForShape(store, draftShape) : null;
  const shouldUpdateExistingDraft = args.updateExistingDraft !== false && isCowartHtmlDraftShape(draftShape) && anchorBounds;
  const shouldTargetDraftHolder = args.matchAnchor !== false && isAiDraftHolderShape(draftShape) && anchorBounds;
  const shouldTargetAiSlides = isAiSlidesShape(draftShape);
  const shouldReplaceDraftHolder = shouldTargetDraftHolder && args.replaceDraftHolder !== false;
  const matchAnchor = args.matchAnchor !== false && anchorBounds;
  const width = shouldUpdateExistingDraft || shouldTargetDraftHolder
    ? anchorBounds.w
    : finiteNumber(args.displayWidth, shouldTargetAiSlides ? 1024 : matchAnchor ? anchorBounds.w : 512);
  const height = shouldUpdateExistingDraft || shouldTargetDraftHolder
    ? anchorBounds.h
    : finiteNumber(args.displayHeight, shouldTargetAiSlides ? 576 : matchAnchor ? anchorBounds.h : 683);
  const margin = Math.max(0, finiteNumber(args.margin, 40));
  const placement = ["right", "left", "below"].includes(args.placement) ? args.placement : "right";
  let parentId = draftShape?.parentId && store[draftShape.parentId] ? draftShape.parentId : pageId;
  let rotation = 0;
  let bounds = null;

  if (shouldTargetAiSlides) {
    const padding = Math.max(0, finiteNumber(draftShape.meta?.cowartAiSlidesPadding, 12));
    const gap = Math.max(0, finiteNumber(draftShape.meta?.cowartAiSlidesGap, 32));
    const slideItems = Object.values(store)
      .filter((record) => record?.typeName === "shape" && record.parentId === draftShape.id)
      .sort((a, b) => String(a.index || "").localeCompare(String(b.index || "")));
    const nextX = slideItems.reduce(
      (cursor, item) => Math.max(cursor, finiteNumber(item.x, padding) + finiteNumber(item.props?.w, 0) + gap),
      padding,
    );
    parentId = draftShape.id;
    rotation = 0;
    bounds = { x: nextX, y: padding, w: width, h: height };
  } else if (shouldUpdateExistingDraft || shouldTargetDraftHolder) {
    parentId = draftShape.parentId && store[draftShape.parentId] ? draftShape.parentId : pageId;
    rotation = finiteNumber(draftShape.rotation, 0);
    bounds = {
      x: finiteNumber(draftShape.x, 0),
      y: finiteNumber(draftShape.y, 0),
      w: width,
      h: height,
    };
  } else {
    parentId = draftShape?.parentId && store[draftShape.parentId]?.typeName === "page" ? draftShape.parentId : pageId;
    bounds = choosePlacement({ store, pageId, parentId, anchorShape: draftShape, width, height, margin, placement });
  }

  const canvasDir = resolveCanvasDir(args);
  const assetsDir = join(canvasDir, "pages", pageDirName(pageId), "assets");
  if (!isSafeChildPath(canvasDir, assetsDir)) {
    throw new Error(`Unsafe page assets directory: ${assetsDir}`);
  }
  const existingAssetUrl = shouldUpdateExistingDraft
    ? nonEmptyString(draftShape.meta?.cowartHtmlDraftAssetUrl)
    : null;
  const expectedAssetPrefix = `/page-assets/${pageDirName(pageId)}/`;
  let existingFileName = null;
  if (existingAssetUrl?.startsWith(expectedAssetPrefix)) {
    try {
      existingFileName = decodeURIComponent(existingAssetUrl.slice(expectedAssetPrefix.length).split(/[?#]/)[0]);
    } catch (_error) {
      existingFileName = null;
    }
  }
  const shouldForkSharedAsset = Boolean(
    shouldUpdateExistingDraft &&
      existingAssetUrl &&
      Object.values(store).some(
        (record) =>
          record?.id !== draftShape.id &&
          isCowartHtmlDraftShape(record) &&
          nonEmptyString(record.meta?.cowartHtmlDraftAssetUrl) === existingAssetUrl,
      ),
  );
  const requestedName = sanitizeHtmlFileName(
    existingFileName || args.fileName,
    `draft-${Date.now()}.html`,
  );
  const fileTarget = shouldUpdateExistingDraft && existingFileName && !shouldForkSharedAsset
    ? { fileName: requestedName, filePath: join(assetsDir, requestedName) }
    : await uniqueFilePath(assetsDir, requestedName);
  const { fileName, filePath } = fileTarget;
  if (!isSafeChildPath(assetsDir, filePath)) {
    throw new Error(`Unsafe HTML draft file path: ${filePath}`);
  }
  const recordSeed = sanitizeIdPart(fileName, "html-draft");
  const shapeId = shouldUpdateExistingDraft ? draftShape.id : uniqueRecordId(store, "shape", recordSeed);
  const replacedShapeIds = shouldReplaceDraftHolder && draftShapeId
    ? [draftShapeId, ...collectDescendantShapeIds(store, draftShapeId)]
    : [];
  const index = shouldUpdateExistingDraft && typeof draftShape?.index === "string"
    ? draftShape.index
    : shouldReplaceDraftHolder && typeof draftShape?.index === "string"
    ? draftShape.index
    : chooseIndex(store, parentId);
  const assetUrl = pageAssetUrl(pageId, fileName);
  const shapeMeta = args.shapeMeta && typeof args.shapeMeta === "object" ? { ...args.shapeMeta } : {};
  if (shouldTargetDraftHolder && draftShapeId && !shapeMeta.cowartGeneratedForAiDraftHolder) {
    shapeMeta.cowartGeneratedForAiDraftHolder = draftShapeId;
  }
  if (shouldReplaceDraftHolder && draftShapeId) {
    shapeMeta.cowartReplacedAiDraftHolder = true;
  }
  if (shouldTargetAiSlides && draftShapeId && !shapeMeta.cowartAiSlidesParentShapeId) {
    shapeMeta.cowartAiSlidesParentShapeId = draftShapeId;
  }

  const shapeRecord = {
    x: bounds.x,
    y: bounds.y,
    rotation,
    isLocked: false,
    opacity: 1,
    meta: {
      ...(shouldUpdateExistingDraft && draftShape.meta && typeof draftShape.meta === "object" ? draftShape.meta : {}),
      cowartHtmlDraft: true,
      cowartHtmlDraftAssetUrl: assetUrl,
      ...shapeMeta,
    },
    id: shapeId,
    type: "embed",
    props: {
      ...(shouldUpdateExistingDraft && draftShape.props && typeof draftShape.props === "object" ? draftShape.props : {}),
      w: width,
      h: height,
      url: cowartHtmlDraftDataUrl(finalHtml),
    },
    parentId,
    index,
    typeName: "shape",
  };

  if (!args.dryRun) {
    await mkdir(assetsDir, { recursive: true });
    await writeFile(filePath, finalHtml);
    for (const replacedShapeId of replacedShapeIds) {
      delete store[replacedShapeId];
    }
    store[shapeId] = shapeRecord;
    await saveCowartCanvasSnapshot(args, snapshot);
  }

  return {
    canvasDir,
    pageId,
    parentId,
    draftShapeId,
    shapeId,
    index,
    assetFile: filePath,
    assetUrl,
    virtualUrl: cowartHtmlDraftVirtualUrl(assetUrl),
    displayUrlKind: "data:text/html;base64",
    bounds,
    updatedExistingHtmlDraft: Boolean(shouldUpdateExistingDraft),
    forkedSharedHtmlDraftAsset: shouldForkSharedAsset,
    replacedAiDraftHolder: shouldReplaceDraftHolder,
    replacedShapeIds,
    dryRun: Boolean(args.dryRun),
  };
}

async function saveCowartReferenceImage(args = {}) {
  const canvasState = await readCowartCanvasState(args, { hydrateAssets: false });
  const snapshot = canvasState.snapshot;
  if (!snapshot || typeof snapshot !== "object" || !snapshot.schema || !snapshot.store) {
    throw new Error("No Cowart canvas snapshot exists yet. Open the Cowart widget for the target project and create or save the canvas before saving reference images.");
  }

  const store = snapshot.store;
  const { selection } = await readCowartSelectionState(args);
  const { viewState } = await readCowartViewState(args);
  const holderShapeId = nonEmptyString(args.holderShapeId) || nonEmptyString(args.anchorShapeId) || firstSelectedShapeId(selection);
  const holderShape = holderShapeId ? getRecord(store, holderShapeId, "AI image holder shape") : null;
  const pageId =
    nonEmptyString(args.pageId) ||
    (holderShape ? findPageIdForShape(store, holderShape.id) : null) ||
    nonEmptyString(viewState?.currentPageId) ||
    Object.values(store).find((record) => record?.typeName === "page")?.id;
  if (!pageId || !store[pageId]) throw new Error("Could not determine target pageId for the reference image.");

  const result = await writeCowartPageAsset(args, {
    pageId,
    fileName: args.fileName,
    dataUrl: args.dataUrl,
    dataBase64: args.dataBase64,
    mimeType: args.mimeType,
  });
  const { projectDir } = resolveCowartPaths(args);

  return {
    ...result,
    projectDir,
    holderShapeId: holderShape?.id ?? holderShapeId ?? null,
    assetPathRelativeToProject: relative(projectDir, result.assetPath),
    assetPathRelativeToCanvas: relative(result.canvasDir, result.assetPath),
  };
}

async function downloadCowartFile(args = {}) {
  const assetUrl = nonEmptyString(args.assetUrl);
  const dataUrl = nonEmptyString(args.dataUrl);
  const dataBase64 = nonEmptyString(args.dataBase64);
  let buffer = null;
  let mimeType = nonEmptyString(args.mimeType) || "application/octet-stream";
  let sourceFileName = null;

  if (assetUrl) {
    const asset = await readCowartPageAsset(args, { assetUrl });
    buffer = Buffer.from(asset.dataBase64, "base64");
    mimeType = asset.mimeType || mimeType;
    sourceFileName = basename(asset.assetPath);
  } else if (dataUrl) {
    const parsed = parseDownloadDataUrl(dataUrl);
    buffer = parsed.buffer;
    mimeType = nonEmptyString(args.mimeType) || parsed.mimeType;
  } else if (dataBase64) {
    buffer = Buffer.from(dataBase64, "base64");
  } else {
    throw new Error("assetUrl, dataUrl, or dataBase64 is required.");
  }

  if (!buffer.length) throw new Error("Cowart download data is empty.");

  const downloadsDir = join(resolveCowartPaths(args).projectDir, "Downloads");
  const requestedName = sanitizeFileName(
    nonEmptyString(args.fileName) || sourceFileName,
    `cowart-download-${Date.now()}.png`,
  );
  const requestedDirectoryName = nonEmptyString(args.directoryName);
  const requestedSubdirectory = nonEmptyString(args.subdirectory);
  let directoryName = requestedDirectoryName
    ? sanitizeDirectoryName(requestedDirectoryName)
    : null;
  let exportRoot = directoryName ? join(downloadsDir, directoryName) : downloadsDir;
  if (directoryName && args.uniqueDirectory === true) {
    const uniqueDirectory = await uniqueDirectoryPath(downloadsDir, directoryName);
    directoryName = uniqueDirectory.directoryName;
    exportRoot = uniqueDirectory.directoryPath;
  }
  const targetDir = requestedSubdirectory
    ? join(exportRoot, sanitizeDirectoryName(requestedSubdirectory, "pages"))
    : exportRoot;
  if (!isSafeChildPath(downloadsDir, targetDir) && targetDir !== downloadsDir) {
    throw new Error("Invalid Cowart download directory.");
  }
  await mkdir(targetDir, { recursive: true });
  const { fileName, filePath } = args.overwrite === true
    ? { fileName: requestedName, filePath: join(targetDir, requestedName) }
    : await uniqueFilePath(targetDir, requestedName);
  await writeFile(filePath, buffer);

  return {
    ok: true,
    fileName,
    filePath,
    directoryName,
    directoryPath: exportRoot,
    mimeType,
    fileSize: buffer.length,
  };
}


export {
  insertCowartImage,
  insertCowartHtmlDraft,
  saveCowartReferenceImage,
  downloadCowartFile,
}
