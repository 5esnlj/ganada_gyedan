import type { PhraseChange } from '../api/wire';
import { renderHelpPages } from './appendix';
import { loadPreviewFont, previewFont } from './fonts';
import { placePhrases, type PhrasePlacement } from './phrases';
import { imageRaster, pixelRectangle, releaseRaster, textRectangle, wrapLine } from './raster';
import type { Raster, Rectangle } from './raster';
import type { Box, ConvertedPdfContent, PdfBlock, PdfHelpContent, PdfPage, PdfPreview } from './types';

interface Overlay {
  font: string;
  size: number;
  lines: { x: number; baseline: number; text: string; start: number }[];
}

function chooseFont(block: PdfBlock, size: number): string {
  const specialty = { sans: '', serif: 'serif, ', hand: 'cursive, ', round: '' }[block.family];
  return `${block.weight || 400} ${size}px ${specialty}"${previewFont}", sans-serif`;
}

// Try the prototype's font/line-spacing reductions before keeping the original block.
function fitText(
  context: CanvasRenderingContext2D, page: PdfPage, block: PdfBlock, text: string,
  allows: (overlay: Overlay) => boolean,
): Overlay | null {
  const top = block.y - (block.lineHeight - 1) * block.fontSize / 2;
  const bottom = Math.min(block.container?.y1 ?? page.height,
    top + block.lineCount * block.lineHeight * block.fontSize + block.room);
  const fullWidth = Math.min((block.container?.x1 ?? page.width) - block.x,
    block.lineCount === 1 ? block.width * 1.03 + block.fontSize * 0.5 : block.width + 0.5);
  const steps = [[1, 1], [0.95, 1], [0.9, 1]];
  if (!block.inner) steps.push([0.9, 0.95], [0.9, 0.9]);
  for (const [fontScale, lineScale] of steps) {
    if (lineScale < 1 && block.lineHeight * lineScale < 1.2 * fontScale) continue;
    const size = block.fontSize * fontScale;
    const pitch = block.lineHeight * block.fontSize * lineScale;
    const font = chooseFont(block, size);
    const firstBaseline = block.y + block.fontSize * 0.88;
    context.font = font;
    const lines: Overlay['lines'] = [];
    let fits = true;
    let offset = 0;
    for (const paragraph of text.split('\n')) {
      let remaining = paragraph.trim();
      let start = offset + paragraph.length - paragraph.trimStart().length;
      do {
        const baseline = firstBaseline + lines.length * pitch;
        if (baseline + size * 0.22 > bottom + 1.5) { fits = false; break; }
        const indent = !lines.length ? block.indent : 0;
        const width = block.wrap && baseline > block.wrap.y && baseline - size < block.wrap.y1
          ? Math.min(fullWidth, block.wrap.x - block.x) : fullWidth;
        const [line, rest] = wrapLine(context, remaining, width - indent);
        if (!line && remaining) { fits = false; break; }
        lines.push({ x: block.x + indent, baseline, text: line, start });
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
  allows: (overlay: Overlay, placements: readonly PhrasePlacement[]) => boolean,
) {
  const deferred = new Set<PhraseChange>();
  let plan = placePhrases(text, changes, deferred);
  const currentOverlay = () => fitText(context, page, block, plan.text, candidate => allows(candidate, plan.placements));
  let overlay = currentOverlay();
  if (!overlay && changes.length) {
    context.font = chooseFont(block, block.fontSize);
    const revertOrder = changes.map((change) => ({
      change,
      growth: context.measureText(change.replacement).width - context.measureText(change.original).width,
    })).sort((a, b) => b.growth - a.growth);
    for (const { change } of revertOrder) {
      deferred.add(change);
      plan = placePhrases(text, changes, deferred);
      if (!plan.placements.length) break;
      overlay = currentOverlay();
      if (overlay) break;
    }
  }
  return overlay ? { overlay, placements: plan.placements, deferred } : null;
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
  const graphics = (page.protectedRegions ?? page.images).flatMap(region => {
    const bounds = pixelRectangle(region, page.scale, width, height);
    return bounds ? [bounds] : [];
  });
  const text = page.blocks.flatMap(block => runBounds(page, raster, block, 0.02)
    .flatMap(bounds => bounds ? [{ bounds, owner: block.id }] : []));
  return { graphics, text };
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

function overlayFits(
  page: PdfPage, raster: Raster, block: PdfBlock, obstacles: Rectangle[],
  overlay: Overlay, placements: readonly PhrasePlacement[],
) {
  const { width, height } = raster.canvas;
  const region: Box = block.container ?? { x: 0, y: 0, x1: page.width, y1: page.height };
  const slack = 1 / page.scale;
  return overlayBounds(raster.context, overlay, placements).every(box => {
    if (box.x - slack < region.x || box.y - slack < region.y ||
        box.x1 + slack > region.x1 || box.y1 + slack > region.y1) return false;
    const bounds = pixelRectangle(box, page.scale, width, height, 1);
    return bounds && !obstacles.some(other => overlaps(bounds, other));
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
): readonly Rectangle[] | null {
  if (!patches.length || !patches.every((patch): patch is Rectangle => patch !== null)) return null;
  if (patches.some(patch => obstacles.some(other => overlaps(patch, other)))) return null;

  for (const patch of patches) {
    let ink = false;
    for (let y = patch.y0; y <= patch.y1; y++) {
      for (let x = patch.x0; x <= patch.x1; x++) {
        const i = (y * width + x) * 4;
        if (original[i] === background[i] && original[i + 1] === background[i + 1] &&
            original[i + 2] === background[i + 2] && original[i + 3] === background[i + 3]) continue;
        ink = true;
        const atEdge = x === patch.x0 || x === patch.x1 || y === patch.y0 || y === patch.y1;
        if (atEdge && !patches.some(other => other !== patch && contains(other, x, y))) return null;
      }
    }
    if (!ink) return null;
  }
  return patches;
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

function withoutLegacyBadge(block: PdfBlock, text: string, phrases: readonly PhraseChange[]) {
  const prefix = block.structuralPrefix;
  const trimmed = text.trimStart();
  const tail = prefix && trimmed.startsWith(prefix) ? trimmed.slice(prefix.length) : '';
  if (!/^\s/u.test(tail)) return { text, phrases };
  const body = tail.trimStart();
  const size = Array.from(text.slice(0, text.length - body.length)).length;
  return {
    text: body,
    phrases: phrases.filter(change => change.start >= size).map(change => ({ ...change, start: change.start - size })),
  };
}

export async function renderConvertedPages(pages: readonly PdfPage[], content: ConvertedPdfContent): Promise<PdfPreview> {
  await loadPreviewFont();
  const output: PdfPreview = { pages: [], notices: [], unplacedBlockIds: [], phrases: [] };
  const paragraphs = new Map(content.paragraphs.map(paragraph => [paragraph.id, paragraph]));
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
    if (!changes.length && !privateContent) {
      output.pages.push(original);
      continue;
    }

    let background: Raster | undefined;
    let raster: Raster | undefined;
    try {
      if (page.backgroundUrl) {
        background = await imageRaster(page.backgroundUrl).catch(() => undefined);
        raster = background && await imageRaster(page.imageUrl);
      }
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
      let committed = false;
      let fallbacks = 0;
      let deferredCount = 0;
      for (const { block, text, phrases } of changes) {
        let fitted = null;
        if (raster && background && pixels && backgroundPixels && obstacles && canEditPdfBlock(page, block, content)) {
          const currentRaster = raster;
          const barriers = obstacles.graphics.concat(
            obstacles.text.filter(other => other.owner !== block.id).map(other => other.bounds), drawn);
          const patches = safeRunPatches(runBounds(page, raster, block, 0.15), barriers,
            pixels, backgroundPixels, raster.canvas.width);
          if (patches) {
            fitted = fitParagraph(raster.context, page, block, text, phrases,
              (overlay, placements) => overlayFits(page, currentRaster, block, barriers, overlay, placements));
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
      output.pages.push((committed || privateContent) && raster
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
