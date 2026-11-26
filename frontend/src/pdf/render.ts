import type { PhraseChange } from '../api/wire';
import { renderHelpPages } from './appendix';
import { loadPreviewFont, previewFont } from './fonts';
import { drawPageGlosses, nativeGlosses, type GlossLine } from './glosses';
import { fitPhraseSubset, placePhrases, withoutLegacyBadge, type PhrasePlacement } from './phrases';
import { imageRaster, pixelRectangle, releaseRaster, textRectangle, wrapLine } from './raster';
import type { Raster, Rectangle } from './raster';
import type { Box, ConvertedPdfContent, PdfBlock, PdfHelpContent, PdfPage, PdfPreview, PdfProtectedRegion } from './types';

interface Overlay {
  font: string;
  size: number;
  lines: { x: number; baseline: number; text: string; start: number }[];
}

interface ReflowSpace {
  bottom: number;
  widthAt: (x: number, baseline: number, size: number, width: number) => number;
}

function chooseFont(block: PdfBlock, size: number): string {
  const specialty = { sans: '', serif: 'serif, ', hand: 'cursive, ', round: '' }[block.family];
  return `${block.weight || 400} ${size}px ${specialty}"${previewFont}", sans-serif`;
}

function fitText(
  context: CanvasRenderingContext2D, page: PdfPage, block: PdfBlock, text: string,
  reflow: ReflowSpace, allows: (overlay: Overlay) => boolean,
): Overlay | null {
  const top = block.y - (block.lineHeight - 1) * block.fontSize / 2;
  const bottom = Math.min(block.container?.y1 ?? page.height,
    top + block.lineCount * block.lineHeight * block.fontSize + block.room);
  const availableWidth = (block.container?.x1 ?? page.width) - block.x;
  const originalWidth = block.lineCount === 1 ? block.width * 1.03 + block.fontSize * 0.5 : block.width + 0.5;
  // Give long text an extra safe row at its original size before reducing the type.
  const steps = [
    { fontScale: 1, extra: 0, flow: false }, { fontScale: 1, extra: 1.4, flow: false },
    { fontScale: 1, extra: 1.4, flow: true }, { fontScale: 1, extra: 1.4, flow: true, down: 0.2 },
    { fontScale: 0.95, extra: 1.4, flow: false }, { fontScale: 0.95, extra: 1.4, flow: true },
    { fontScale: 0.9, extra: 1.4, flow: false }, { fontScale: 0.9, extra: 1.4, flow: true },
  ];
  if (!block.inner && block.fontSize * 0.85 >= 8) {
    steps.push({ fontScale: 0.85, extra: 1.4, flow: true });
  }
  for (const { fontScale, extra, flow, down = 0 } of steps) {
    const size = block.fontSize * fontScale;
    const fullWidth = Math.min(availableWidth, originalWidth + extra * block.fontSize);
    const originalPitch = block.lineHeight * block.fontSize;
    const pitch = flow && fontScale < 1 && !block.inner
      ? Math.min(originalPitch, Math.max(1.25 * size, originalPitch * fontScale)) : originalPitch;
    const limit = flow ? reflow.bottom : bottom + 1.5;
    const font = chooseFont(block, size);
    const firstBaseline = block.y + block.fontSize * (0.88 + down);
    context.font = font;
    const lines: Overlay['lines'] = [];
    let fits = true;
    let offset = 0;
    for (const paragraph of text.split('\n')) {
      let remaining = paragraph.trim();
      let start = offset + paragraph.length - paragraph.trimStart().length;
      do {
        const baseline = firstBaseline + lines.length * pitch;
        if (baseline + size * 0.22 > limit) { fits = false; break; }
        const indent = !lines.length ? block.indent : 0;
        const x = block.x + indent;
        const width = block.wrap && baseline > block.wrap.y && baseline - size < block.wrap.y1
          ? Math.min(fullWidth, block.wrap.x - block.x) : fullWidth;
        const lineWidth = flow ? reflow.widthAt(x, baseline, size, width - indent) : width - indent;
        const [line, rest] = wrapLine(context, remaining, lineWidth, flow);
        if (!line && remaining) { fits = false; break; }
        lines.push({ x, baseline, text: line, start });
        start += remaining.length - rest.length;
        remaining = rest;
      } while (remaining);
      if (!fits) break;
      offset += paragraph.length + 1;
    }
    const overlay = { font, size, lines };
    if (fits && allows(overlay)) return overlay;
  }
  return null;
}

function fitParagraph(
  context: CanvasRenderingContext2D, page: PdfPage, block: PdfBlock, text: string, changes: readonly PhraseChange[],
  reflow: ReflowSpace, allows: (overlay: Overlay, placements: readonly PhrasePlacement[]) => boolean,
) {
  context.font = chooseFont(block, block.fontSize);
  const widestFirst = changes.map(change => ({
    change,
    growth: context.measureText(change.replacement).width - context.measureText(change.original).width,
  })).sort((a, b) => b.growth - a.growth).map(({ change }) => change);
  const fitted = fitPhraseSubset(widestFirst, deferred => {
    const plan = placePhrases(text, changes, deferred);
    const overlay = fitText(context, page, block, plan.text, reflow, candidate => allows(candidate, plan.placements));
    return overlay ? { overlay, placements: plan.placements } : null;
  });
  return fitted ? { ...fitted.value, deferred: fitted.deferred } : null;
}

function drawOverlay(context: CanvasRenderingContext2D, block: PdfBlock, overlay: Overlay, placements: PhrasePlacement[]) {
  context.font = overlay.font;
  context.fillStyle = block.color;
  for (const line of overlay.lines) context.fillText(line.text, line.x, line.baseline);
  if (!placements.length) return;

  context.fillStyle = '#a54436';
  for (const line of overlay.lines) {
    for (const placement of placements) {
      const start = Math.max(placement.start, line.start) - line.start;
      const end = Math.min(placement.end - line.start, line.text.length);
      if (start >= end) continue;
      const left = context.measureText(line.text.slice(0, start)).width;
      const right = context.measureText(line.text.slice(0, end)).width;
      context.fillRect(line.x + left, line.baseline + overlay.size * 0.12, right - left,
        Math.max(0.6, overlay.size * 0.055));
    }
  }
}

export class PdfRedactionError extends Error {
  readonly code = 'PDF_REDACTION_UNSAFE';

  constructor(readonly page?: number) {
    const location = page === undefined ? '선택한 PDF' : `${page}쪽`;
    super(`${location}의 교사용 문구를 안전하게 가릴 수 없습니다. 원본으로 돌아가 이 자료를 다시 변환해 주세요.`);
    this.name = 'PdfRedactionError';
  }
}

const overlaps = (a: Rectangle, b: Rectangle) =>
  a.x0 <= b.x1 && b.x0 <= a.x1 && a.y0 <= b.y1 && b.y0 <= a.y1;

const contains = (box: Rectangle, x: number, y: number) =>
  x >= box.x0 && x <= box.x1 && y >= box.y0 && y <= box.y1;

function runBounds(page: PdfPage, raster: Raster, block: PdfBlock, padding: number) {
  return block.runs.map(run => textRectangle(run, page.scale, raster.canvas.width, raster.canvas.height, padding));
}

function pageObstacles(page: PdfPage, raster: Raster) {
  const { width, height } = raster.canvas;
  const images: Rectangle[] = [];
  const regions: readonly PdfProtectedRegion[] = page.protectedRegions ?? page.images.map(box => ({ ...box, kind: 'image' }));
  const graphics = regions.flatMap(region => {
    const bounds = pixelRectangle(region, page.scale, width, height);
    if (bounds && region.kind === 'image' && !region.blockId) images.push(bounds);
    return bounds ? [bounds] : [];
  });
  const text = page.blocks.flatMap(block => runBounds(page, raster, block, 0.02)
    .flatMap(bounds => bounds ? [{ bounds, owner: block.id }] : []));
  return { graphics, text, images };
}

function imageOverlapIsWhite(box: Rectangle, image: Rectangle, background: Uint8ClampedArray, width: number) {
  for (let y = Math.max(box.y0, image.y0); y <= Math.min(box.y1, image.y1); y++) {
    for (let x = Math.max(box.x0, image.x0); x <= Math.min(box.x1, image.x1); x++) {
      const i = (y * width + x) * 4;
      if (background[i] !== 255 || background[i + 1] !== 255 || background[i + 2] !== 255 || background[i + 3] !== 255) return false;
    }
  }
  return true;
}

function overlayBounds(context: CanvasRenderingContext2D, overlay: Overlay, placements: readonly PhrasePlacement[]) {
  context.font = overlay.font;
  return overlay.lines.flatMap(line => {
    if (!line.text) return [];
    const metrics = context.measureText(line.text);
    const underlined = placements.some(placement =>
      placement.start < line.start + line.text.length && placement.end > line.start);
    return [{
      x: line.x - Math.max(0, metrics.actualBoundingBoxLeft),
      y: line.baseline - metrics.actualBoundingBoxAscent,
      x1: line.x + Math.max(metrics.width, metrics.actualBoundingBoxRight),
      y1: line.baseline + Math.max(metrics.actualBoundingBoxDescent,
        underlined ? overlay.size * 0.12 + Math.max(0.6, overlay.size * 0.055) : 0),
    }];
  });
}

function reflowSpace(page: PdfPage, block: PdfBlock, barriers: readonly Rectangle[], sourceBottom: number): ReflowSpace {
  const { scale } = page;
  const x0 = block.x * scale;
  const x1 = (block.x + block.width) * scale;
  const pitch = block.lineHeight * block.fontSize;
  let bottom = Math.min(block.container?.y1 ?? page.height - block.fontSize / 2, sourceBottom + 2 * pitch);
  for (const obstacle of barriers) {
    const top = obstacle.y0 / scale;
    if (top >= sourceBottom && obstacle.x1 >= x0 && obstacle.x0 <= x1) {
      bottom = Math.min(bottom, top - 2 / scale);
    }
  }
  return {
    bottom,
    widthAt(x, baseline, size, width) {
      let right = (x + width) * scale;
      const left = x * scale;
      const top = (baseline - size * 0.86) * scale - 1;
      const bottom = (baseline + size * 0.18) * scale + 1;
      for (const obstacle of barriers) {
        if (obstacle.x1 < left - 1 || obstacle.y1 < top || obstacle.y0 > bottom) continue;
        right = Math.min(right, obstacle.x0 - 2);
      }
      return (right - left) / scale;
    },
  };
}

// Rows added below the source can only use undisturbed, evenly colored space.
function blankExtension(
  bounds: Rectangle, firstNewRow: number, pixels: Uint8ClampedArray, background: Uint8ClampedArray, width: number,
) {
  const y0 = Math.max(firstNewRow, bounds.y0);
  const reference = (y0 * width + bounds.x0) * 4;
  for (let y = y0; y <= bounds.y1; y++) {
    for (let x = bounds.x0; x <= bounds.x1; x++) {
      const i = (y * width + x) * 4;
      if (pixels[i + 3] !== 255 || background[i + 3] !== 255) return false;
      for (let channel = 0; channel < 3; channel++) {
        if (pixels[i + channel] !== background[i + channel] ||
            Math.abs(background[i + channel] - background[reference + channel]) > 10) return false;
      }
    }
  }
  return true;
}

function overlayFits(
  page: PdfPage, raster: Raster, block: PdfBlock, obstacles: Rectangle[],
  overlay: Overlay, placements: readonly PhrasePlacement[],
  blankImageMargins: readonly Rectangle[], pixels: Uint8ClampedArray, backgroundPixels: Uint8ClampedArray,
  firstNewRow: number,
) {
  const { width, height } = raster.canvas;
  const region: Box = block.container ?? { x: 0, y: 0, x1: page.width, y1: page.height };
  const slack = 1 / page.scale;
  return overlayBounds(raster.context, overlay, placements).every(box => {
    if (box.x - slack < region.x || box.y - slack < region.y ||
        box.x1 + slack > region.x1 || box.y1 + slack > region.y1) return false;
    const bounds = pixelRectangle(box, page.scale, width, height, 1);
    return bounds && !obstacles.some(other => overlaps(bounds, other)) &&
      blankImageMargins.every(image => imageOverlapIsWhite(bounds, image, backgroundPixels, width)) &&
      blankExtension(bounds, firstNewRow, pixels, backgroundPixels, width);
  });
}

// The second render omits text paint. Restoring its pixels retains every
// underlying decoration and avoids guessing a fill color from its neighbors.
function replaceRuns(context: CanvasRenderingContext2D, background: Raster, patches: readonly Rectangle[]) {
  for (const { x0, x1, y0, y1 } of patches) {
    const width = x1 - x0 + 1;
    const height = y1 - y0 + 1;
    context.putImageData(background.context.getImageData(x0, y0, width, height), x0, y0);
  }
}

function safeRunPatches(
  patches: readonly (Rectangle | null)[], obstacles: readonly Rectangle[],
  original: Uint8ClampedArray, background: Uint8ClampedArray, width: number,
  inkOnly = false,
): readonly Rectangle[] | null {
  if (!patches.length || !patches.every((patch): patch is Rectangle => patch !== null)) return null;

  const safe = [];
  for (const patch of patches) {
    const touching = obstacles.filter(other => overlaps(patch, other));
    if (touching.length && !inkOnly) return null;
    const ink = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    for (let y = patch.y0; y <= patch.y1; y++) {
      for (let x = patch.x0; x <= patch.x1; x++) {
        const i = (y * width + x) * 4;
        if (original[i] === background[i] && original[i + 1] === background[i + 1] &&
            original[i + 2] === background[i + 2] && original[i + 3] === background[i + 3]) continue;
        if (touching.some(other => contains(other, x, y))) return null;
        const atEdge = x === patch.x0 || x === patch.x1 || y === patch.y0 || y === patch.y1;
        if (atEdge && !patches.some(other => other !== patch && contains(other, x, y))) return null;
        ink.x0 = Math.min(ink.x0, x);
        ink.y0 = Math.min(ink.y0, y);
        ink.x1 = Math.max(ink.x1, x);
        ink.y1 = Math.max(ink.y1, y);
      }
    }
    if (ink.x0 !== Infinity) safe.push(inkOnly ? ink : patch);
    else if (!inkOnly) return null;
  }
  return safe.length ? safe : null;
}

function studentRunPatches(
  page: PdfPage, raster: Raster, block: PdfBlock, barriers: readonly Rectangle[],
  drawn: readonly Rectangle[], pixels: Uint8ClampedArray, backgroundPixels: Uint8ClampedArray,
  blankImageMargins: readonly Rectangle[],
) {
  const { width, height } = raster.canvas;
  // A separate mark above a run may touch its padding. Retry with a tighter
  // top edge and still reject any ink at that edge or inside the obstacle.
  for (const [margin, cropTop] of [[0.15, false], [0.22, false], [0.3, false], [0.15, true], [0.22, true]] as const) {
    const runs = block.runs.map(run => {
      const bounds = textRectangle(run, page.scale, width, height, margin);
      if (!bounds || !cropTop) return bounds;
      const maximum = (run[1] + run[3] * 0.18) * page.scale;
      const y0 = barriers.reduce((top, other) => overlaps(bounds, other) && other.y1 < maximum
        ? Math.max(top, other.y1 - 1) : top, bounds.y0);
      return { ...bounds, y0 };
    });
    const images = blankImageMargins.filter(image => runs.some(run => run && !imageOverlapIsWhite(run, image, backgroundPixels, width)));
    const patches = safeRunPatches(runs, barriers.concat(images), pixels, backgroundPixels, width, true);
    if (patches && !patches.some(patch => drawn.some(overlay => overlaps(patch, overlay)))) return patches;
  }
  return null;
}

function redact(
  page: PdfPage, raster: Raster, background: Raster, originalPixels: Uint8ClampedArray,
  backgroundPixels: Uint8ClampedArray, obstacles: ReturnType<typeof pageObstacles>, hidden: ReadonlySet<string>,
) {
  const privateBlocks = page.blocks.filter(block => hidden.has(block.id));
  if (!privateBlocks.length) return;
  if (privateBlocks.some(block => block.layout && block.layout !== 'text')) {
    throw new PdfRedactionError(page.number);
  }
  const barriers = obstacles.graphics.concat(
    obstacles.text.filter(obstacle => !hidden.has(obstacle.owner)).map(obstacle => obstacle.bounds));
  const privateRuns = privateBlocks.flatMap(block => runBounds(page, raster, block, 0.17));
  const patches = safeRunPatches(privateRuns, barriers, originalPixels, backgroundPixels, raster.canvas.width);
  if (!patches) throw new PdfRedactionError(page.number);
  replaceRuns(raster.context, background, patches);
}

export function canEditPdfBlock(
  page: PdfPage,
  block: PdfBlock,
  content: Pick<ConvertedPdfContent, 'omittedPages' | 'hiddenBlockIds'>,
): boolean {
  return !page.fallback && !block.preserve && (block.layout === undefined || block.layout === 'text') && /\p{L}/u.test(block.text) && !content.omittedPages?.includes(page.number)
    && !content.hiddenBlockIds?.includes(block.id);
}

export async function renderConvertedPages(pages: readonly PdfPage[], content: ConvertedPdfContent): Promise<PdfPreview> {
  await loadPreviewFont();
  const output: PdfPreview = { pages: [], notices: [], unplacedBlockIds: [], phrases: [], glosses: [] };
  const paragraphs = new Map(content.paragraphs.map(paragraph => [paragraph.id, paragraph]));
  const translations = nativeGlosses(content.translations);
  const omitted = new Set(content.omittedPages);
  const limitedSupport = new Set(content.limitedSupportPages);
  const hidden = new Set(content.hiddenBlockIds);
  const ids = new Set(pages.flatMap(page => page.blocks.map(block => block.id)));
  for (const id of hidden) {
    if (!ids.has(id)) {
      const number = /^p([1-9][0-9]*)b/.exec(id)?.[1];
      throw new PdfRedactionError(number ? Number(number) : undefined);
    }
  }
  for (const page of pages) {
    const original = { number: page.number, width: page.width, height: page.height, imageUrl: page.imageUrl };
    const omittedPage = Boolean(page.fallback) || omitted.has(page.number);
    if (omittedPage) output.notices.push(page.fallback ?? `${page.number}쪽은 원본 지면을 유지했습니다.`);
    if (limitedSupport.has(page.number)) {
      output.notices.push(`${page.number}쪽은 일부 쉬운 표현이나 학습 도움말을 적용하지 못했습니다. 배포 전에 원본과 비교해 주세요.`);
    }
    const changes = omittedPage ? [] : page.blocks.flatMap(block => {
      const paragraph = paragraphs.get(block.id);
      if (hidden.has(block.id) || paragraph?.page !== page.number) return [];
      const { text, phrases } = withoutLegacyBadge(block, paragraph.text.replace(/‸/g, ' '), paragraph.phraseChanges ?? []);
      const sourceText = block.text.replace(/‸/g, ' ').trim();
      return text.trim() && text.trim() !== sourceText ? [{ block, text, phrases }] : [];
    });
    const privateContent = page.blocks.some(block => hidden.has(block.id));
    const glossaryBlocks = !translations.length || omittedPage ? [] : page.blocks.filter(block => {
      const paragraph = paragraphs.get(block.id);
      return paragraph?.page === page.number && canEditPdfBlock(page, block, content)
        && translations.some(({ term }) => paragraph.text.includes(term));
    });
    if (!changes.length && !privateContent && !glossaryBlocks.length) {
      output.pages.push(original);
      continue;
    }

    let background: Raster | undefined;
    let raster: Raster | undefined;
    try {
      if (page.backgroundUrl) {
        background = await imageRaster(page.backgroundUrl).catch(() => undefined);
      }
      if (background || glossaryBlocks.length) raster = await imageRaster(page.imageUrl);
      if (background && raster && (background.canvas.width !== raster.canvas.width ||
          background.canvas.height !== raster.canvas.height)) {
        releaseRaster(background);
        background = undefined;
      }
      if ((!background || !raster) && privateContent) throw new PdfRedactionError(page.number);

      const obstacles = raster && pageObstacles(page, raster);
      const pixels = raster?.context.getImageData(0, 0, raster.canvas.width, raster.canvas.height).data;
      const backgroundPixels = background?.context.getImageData(0, 0, background.canvas.width, background.canvas.height).data;
      if (privateContent && raster && background && pixels && backgroundPixels && obstacles) {
        redact(page, raster, background, pixels, backgroundPixels, obstacles, hidden);
      }

      const drawn: Rectangle[] = [];
      const overlays = new Map<string, Overlay>();
      let committed = false;
      let fallbacks = 0;
      let deferredCount = 0;
      for (const { block, text, phrases } of changes) {
        let fitted = null;
        if (raster && background && pixels && backgroundPixels && obstacles && canEditPdfBlock(page, block, content)) {
          const currentRaster = raster;
          const originalRuns = runBounds(page, raster, block, 0);
          // Some image bounds include a blank margin where the PDF already draws this text.
          // Only those margins can accept another student overlay, after checking its actual pixels below.
          const blankImageMargins = obstacles.images.filter(image =>
            originalRuns.some(run => run && overlaps(run, image)) && originalRuns.every(run =>
              run && imageOverlapIsWhite(run, image, backgroundPixels, currentRaster.canvas.width)));
          const barriers = obstacles.graphics.filter(region => !blankImageMargins.includes(region)).concat(
            obstacles.text.filter(other => other.owner !== block.id).map(other => other.bounds), drawn);
          const patches = studentRunPatches(page, raster, block, barriers, drawn, pixels, backgroundPixels, blankImageMargins);
          if (patches) {
            const sourceBottom = Math.max(...block.runs.map(([, y, , size]) => y + size * 1.08));
            const reflow = reflowSpace(page, block, barriers, sourceBottom);
            fitted = fitParagraph(raster.context, page, block, text, phrases, reflow,
              (overlay, placements) => overlayFits(page, currentRaster, block, barriers, overlay, placements,
                blankImageMargins, pixels, backgroundPixels, Math.ceil(sourceBottom * page.scale) + 1));
            if (fitted) {
              replaceRuns(raster.context, background, patches);
              for (const box of overlayBounds(raster.context, fitted.overlay, fitted.placements)) {
                const bounds = pixelRectangle(box, page.scale, raster.canvas.width, raster.canvas.height, 1);
                if (bounds) drawn.push(bounds);
              }
              raster.context.save();
              raster.context.scale(page.scale, page.scale);
              raster.context.textBaseline = 'alphabetic';
              drawOverlay(raster.context, block, fitted.overlay, fitted.placements);
              raster.context.restore();
              overlays.set(block.id, fitted.overlay);
              committed = true;
            }
          }
        }
        for (const change of phrases) {
          const applied = fitted !== null && !fitted.deferred.has(change);
          output.phrases.push({ blockId: block.id, page: page.number, change, applied });
          if (!applied) deferredCount++;
        }
        if (!fitted) {
          output.unplacedBlockIds.push(block.id);
          if (!phrases.length) fallbacks++;
        }
      }
      if (fallbacks) {
        output.notices.push(`${page.number}쪽 문단 ${fallbacks}개는 배치에 맞지 않거나 배경이 복잡해 원본을 유지했습니다.`);
      }
      if (deferredCount) {
        output.notices.push(`${page.number}쪽 표현 ${deferredCount}개는 원문을 남기고 PDF 도움말에 쉬운 표현을 담았습니다.`);
      }
      let glossaryCount = 0;
      if (raster && obstacles && glossaryBlocks.length) {
        const lines = glossaryBlocks.flatMap<GlossLine>(block => {
          const overlay = overlays.get(block.id);
          if (overlay) return overlay.lines.map(line => ({
            ...line, size: overlay.size, font: overlay.font, container: block.container,
          }));
          return (block.sourceLines ?? []).map(({ text, rect: [x, y, width, size] }) => ({
            text, x, width, size, baseline: y + size * 0.88, font: chooseFont(block, size),
            inkTop: y - size * 0.08, inkBottom: y + size * 1.10, container: block.container,
          }));
        });
        const glosses = drawPageGlosses(raster, page, lines, translations, obstacles.graphics);
        glossaryCount = glosses.length;
        output.glosses.push(...glosses);
      }
      output.pages.push((committed || privateContent || glossaryCount) && raster
        ? { ...original, imageUrl: raster.canvas.toDataURL('image/png') } : original);
    } finally {
      if (background) releaseRaster(background);
      if (raster) releaseRaster(raster);
    }
  }
  return output;
}

export async function exportPreviewPdf(preview: PdfPreview, help?: PdfHelpContent): Promise<Blob> {
  const first = preview.pages[0];
  if (!first) throw new Error('저장할 PDF 쪽이 없습니다.');
  const { jsPDF } = await import('jspdf');
  const orientation = (page: { width: number; height: number }) => page.width > page.height ? 'landscape' : 'portrait';
  const pdf = new jsPDF({ unit: 'pt', format: [first.width, first.height], orientation: orientation(first) });
  const appendix = help ? await renderHelpPages(help, preview.phrases.filter(phrase => !phrase.applied)) : [];
  [...preview.pages, ...appendix].forEach((page, index) => {
    if (index) pdf.addPage([page.width, page.height], orientation(page));
    const format = page.imageUrl.startsWith('data:image/png;') ? 'PNG' : 'JPEG';
    pdf.addImage(page.imageUrl, format, 0, 0, page.width, page.height, undefined, 'FAST');
  });
  return pdf.output('blob');
}
