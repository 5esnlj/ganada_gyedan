import { renderHelpPages } from './appendix';
import { loadPreviewFont, previewFont } from './fonts';
import { imageRaster, releaseRaster, rgb, ringMode, textRectangle, wrapLine } from './raster';
import type { Raster, Rectangle } from './raster';
import type { Box, ConvertedPdfContent, PdfBlock, PdfHelpContent, PdfPage, PdfPreview, TextRect } from './types';

interface Overlay {
  font: string;
  lines: { x: number; baseline: number; text: string }[];
}

function chooseFont(block: PdfBlock, size: number): string {
  const specialty = { sans: '', serif: 'serif, ', hand: 'cursive, ', round: '' }[block.family];
  return `${block.weight || 400} ${size}px ${specialty}"${previewFont}", sans-serif`;
}

// Try the prototype's font/line-spacing reductions before keeping the original block.
function fitText(context: CanvasRenderingContext2D, page: PdfPage, block: PdfBlock, text: string): Overlay | null {
  const top = block.y - (block.lineHeight - 1) * block.fontSize / 2;
  const bottom = Math.min(page.height, top + block.lineCount * block.lineHeight * block.fontSize + block.room);
  const fullWidth = Math.min(page.width - block.x,
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
    for (const paragraph of text.split('\n')) {
      let remaining = paragraph.trim();
      do {
        const baseline = firstBaseline + lines.length * pitch;
        if (baseline + size * 0.22 > bottom + 1.5) { fits = false; break; }
        const indent = !lines.length ? block.indent : 0;
        const width = block.wrap && baseline > block.wrap.y && baseline - size < block.wrap.y1
          ? Math.min(fullWidth, block.wrap.x - block.x) : fullWidth;
        const [line, rest] = wrapLine(context, remaining, width - indent);
        if (!line && remaining) { fits = false; break; }
        lines.push({ x: block.x + indent, baseline, text: line });
        remaining = rest;
      } while (remaining);
      if (!fits) break;
    }
    if (fits) return { font, lines };
  }
  return null;
}

interface Patch extends Rectangle {
  color: string;
}

function samplePatch(
  raster: Raster,
  pixels: Uint8ClampedArray,
  bounds: Rectangle,
  force: boolean,
): Patch | null {
  const sample = ringMode(pixels, raster.canvas.width, raster.canvas.height,
    bounds.x0, bounds.y0, bounds.x1, bounds.y1, force ? 3 : 2);
  if (!force && (!sample || sample.share < 0.45)) return null;
  return { ...bounds, color: sample ? rgb(sample.color) : '#fff' };
}

function fillPatches(context: CanvasRenderingContext2D, patches: Patch[]) {
  for (const patch of patches) {
    context.fillStyle = patch.color;
    context.fillRect(patch.x0, patch.y0, patch.x1 - patch.x0 + 1, patch.y1 - patch.y0 + 1);
  }
}

function rectPatch(
  raster: Raster,
  pixels: Uint8ClampedArray,
  bounds: Box,
  scale: number,
): Patch | null {
  const rect = {
    x0: Math.max(0, Math.floor(bounds.x * scale)), y0: Math.max(0, Math.floor(bounds.y * scale)),
    x1: Math.min(raster.canvas.width - 1, Math.ceil(bounds.x1 * scale)),
    y1: Math.min(raster.canvas.height - 1, Math.ceil(bounds.y1 * scale)),
  };
  return rect.x1 > rect.x0 && rect.y1 > rect.y0 ? samplePatch(raster, pixels, rect, true) : null;
}

function runPatches(raster: Raster, pixels: Uint8ClampedArray, rects: TextRect[], scale: number, hide: boolean) {
  return rects.map(rect => {
    const bounds = textRectangle(rect, scale, raster.canvas.width, raster.canvas.height, hide ? 0.18 : 0.06);
    return bounds && samplePatch(raster, pixels, bounds, hide);
  });
}

function inside(box: Box, block: PdfBlock): boolean {
  const x = block.x + block.width / 2;
  const y = block.y + block.height / 2;
  return x > box.x && x < box.x1 && y > box.y && y < box.y1;
}

function hideBlocks(raster: Raster, pixels: Uint8ClampedArray, page: PdfPage, hidden: Set<string>) {
  const { scale } = page;
  const privateBlocks = page.blocks.filter(block => hidden.has(block.id));
  const others = page.blocks.filter(block => !hidden.has(block.id));
  const areas: Box[] = [];
  for (const block of privateBlocks) {
    const margins = block.fontSize * 1.8;
    const nearby = {
      x: block.x - margins, y: block.y - margins,
      x1: block.x + block.width + margins, y1: block.y + block.height + margins,
    };
    const current = areas.find(box =>
      nearby.x < box.x1 && box.x < nearby.x1 && nearby.y < box.y1 && box.y < nearby.y1);
    if (!current) areas.push(nearby);
    else {
      current.x = Math.min(current.x, nearby.x);
      current.y = Math.min(current.y, nearby.y);
      current.x1 = Math.max(current.x1, nearby.x1);
      current.y1 = Math.max(current.y1, nearby.y1);
    }
  }

  const privateFigures = [...page.shapes, ...page.images].filter(figure =>
    areas.some(area => figure.x >= area.x && figure.y >= area.y && figure.x1 <= area.x1 &&
      figure.y1 <= area.y1 && !others.some(block => inside(area, block))));
  const privateBoxes = page.boxes.filter(box =>
    privateBlocks.some(block => inside(box, block)) && !others.some(block => inside(box, block)));
  for (const box of [...privateFigures, ...privateBoxes]) {
    const patch = rectPatch(raster, pixels, {
      x: box.x - 2, y: box.y - 2, x1: box.x1 + 2, y1: box.y1 + 2,
    }, scale);
    if (patch) fillPatches(raster.context, [patch]);
  }
  for (const block of privateBlocks) {
    const patches = runPatches(raster, pixels, block.runs, scale, true);
    fillPatches(raster.context, patches.filter(patch => patch !== null));
    if (patches.length && patches.every(patch => patch !== null)) continue;
    const patch = rectPatch(raster, pixels, {
      x: block.x - block.fontSize * 0.2, y: block.y - block.fontSize * 0.2,
      x1: block.x + block.width + block.fontSize * 0.2,
      y1: block.y + block.height + block.fontSize * 0.2,
    }, scale);
    if (!patch) throw new Error(`${page.number}쪽의 교사용 문단을 가릴 수 없습니다.`);
    fillPatches(raster.context, [patch]);
  }
}

export async function renderConvertedPages(pages: readonly PdfPage[], content: ConvertedPdfContent): Promise<PdfPreview> {
  await loadPreviewFont();
  const output: PdfPreview = { pages: [], notices: [] };
  const paragraphs = new Map(content.paragraphs.map(paragraph => [paragraph.id, paragraph]));
  const omitted = new Set(content.omittedPages);
  const hidden = new Set(content.hiddenBlockIds);
  const ids = new Set(pages.flatMap(page => page.blocks.map(block => block.id)));
  for (const id of hidden) {
    if (!ids.has(id)) throw new Error('교사용 문단과 원본 PDF를 맞출 수 없습니다. 사용한 PDF와 쪽 범위를 확인해 주세요.');
  }
  for (const page of pages) {
    const original = { number: page.number, width: page.width, height: page.height, imageUrl: page.imageUrl };
    const omittedPage = Boolean(page.fallback) || omitted.has(page.number);
    if (omittedPage) output.notices.push(page.fallback ?? `${page.number}쪽은 원본 지면을 유지했습니다.`);
    const changes = omittedPage ? [] : page.blocks.flatMap(block => {
      const paragraph = paragraphs.get(block.id);
      const text = paragraph?.text.replace(/‸/g, ' ').trim();
      const sourceText = block.text.replace(/‸/g, ' ').trim();
      if (block.preserve || hidden.has(block.id) || !text || text === sourceText || paragraph?.page !== page.number) {
        return [];
      }
      return [{ block, text }];
    });
    if (!changes.length && !page.blocks.some(block => hidden.has(block.id))) {
      output.pages.push(original);
      continue;
    }

    const raster = await imageRaster(page.imageUrl);
    const { canvas, context } = raster;
    try {
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const overlays: { block: PdfBlock; overlay: Overlay }[] = [];
      let fallbacks = 0;
      for (const { block, text } of changes) {
        const overlay = fitText(context, page, block, text);
        const patches = runPatches(raster, pixels, block.runs, page.scale, false);
        if (!overlay || !patches.length || patches.some(patch => patch === null)) { fallbacks++; continue; }
        fillPatches(context, patches.filter(patch => patch !== null));
        overlays.push({ block, overlay });
      }
      hideBlocks(raster, pixels, page, hidden);
      context.save();
      context.scale(page.scale, page.scale);
      context.textBaseline = 'alphabetic';
      for (const { block, overlay } of overlays) {
        context.font = overlay.font;
        context.fillStyle = block.color;
        for (const line of overlay.lines) context.fillText(line.text, line.x, line.baseline);
      }
      context.restore();
      if (fallbacks) {
        output.notices.push(`${page.number}쪽 문단 ${fallbacks}개는 배치에 맞지 않거나 배경이 복잡해 원본을 유지했습니다.`);
      }
      output.pages.push({ ...original, imageUrl: canvas.toDataURL('image/jpeg', 0.9) });
    } finally {
      releaseRaster(raster);
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
  const appendix = help ? await renderHelpPages(help) : [];
  [...preview.pages, ...appendix].forEach((page, index) => {
    if (index) pdf.addPage([page.width, page.height], orientation(page));
    pdf.addImage(page.imageUrl, 'JPEG', 0, 0, page.width, page.height);
  });
  return pdf.output('blob');
}
