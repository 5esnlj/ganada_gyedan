import { AnnotationMode, getDocument, GlobalWorkerOptions, OPS, PasswordException, Util } from 'pdfjs-dist';
import type { PDFPageProxy, PageViewport } from 'pdfjs-dist';
import type { OperationsFilter } from 'pdfjs-dist/types/src/display/api';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { addLayoutAids, analyzeShapes, classifyLayout, figureRegions, fontInfo, runRect, toBlocks } from './geometry';
import type { TextRun } from './geometry';
import { createRaster, inkColor, releaseRaster, rgb, ringMode, textRectangle } from './raster';
import type { Raster } from './raster';
import type { FontFamily, PdfPage, PdfSource, TextRect } from './types';

GlobalWorkerOptions.workerSrc = workerUrl;

const dataFiles = import.meta.glob<string>([
  '../../node_modules/pdfjs-dist/cmaps/*.bcmap',
  '../../node_modules/pdfjs-dist/standard_fonts/*.{ttf,pfb}',
  '../../node_modules/pdfjs-dist/wasm/*.wasm',
], { eager: true, query: '?url', import: 'default' });

const directory = {
  cMapUrl: 'cmaps', standardFontDataUrl: 'standard_fonts', wasmUrl: 'wasm',
} as const;

class LocalPdfData {
  async fetch({ kind, filename }: { kind: keyof typeof directory; filename: string }) {
    const file = dataFiles[`../../node_modules/pdfjs-dist/${directory[kind]}/${filename}`];
    if (!file) throw new Error('PDF 보조 데이터를 찾을 수 없습니다.');
    const response = await fetch(file);
    if (!response.ok) throw new Error('PDF 보조 데이터를 불러오지 못했습니다.');
    return new Uint8Array(await response.arrayBuffer());
  }
}

function renderScale(viewport: PageViewport): number {
  return Math.min(2, 1600 / viewport.width, 3200 / viewport.height,
    Math.sqrt(3.2e6 / (viewport.width * viewport.height)));
}

const textPaint = new Set([
  OPS.showText, OPS.showSpacedText, OPS.nextLineShowText, OPS.nextLineSetSpacingShowText,
]);
const omitText: OperationsFilter = (index, operators) => !textPaint.has(operators.fnArray[index]);

async function renderOriginal(
  page: PDFPageProxy, viewport: PageViewport, operationsFilter?: OperationsFilter,
): Promise<Raster> {
  const raster = createRaster(viewport.width, viewport.height);
  try {
    await page.render({
      canvas: raster.canvas, canvasContext: raster.context, viewport,
      background: '#fff', annotationMode: AnnotationMode.DISABLE,
      operationsFilter,
    }).promise;
    return raster;
  } catch (error) {
    releaseRaster(raster);
    throw error;
  }
}

const isWordChar = (char = '') => /[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9]/.test(char);
const corruptedChar = /[\uFFFD\uE000-\uF8FF\u0000-\u001F]/;

interface RawRun {
  text: string;
  font: string;
  width: number;
  transform: number[];
  size: number;
  family: FontFamily;
  weight: number;
}

async function extractRuns(page: PDFPageProxy, viewport: PageViewport) {
  const content = await page.getTextContent();
  const raw: RawRun[] = [];
  let total = 0;
  let bad = 0;
  for (const item of content.items) {
    if (!('str' in item) || !item.str) continue;
    for (const char of item.str) {
      if (/\s/.test(char)) continue;
      total++;
      if (corruptedChar.test(char)) bad++;
    }
    const transform = Util.transform(viewport.transform, item.transform);
    if (Math.abs(transform[1]) > Math.abs(transform[0]) + 0.01) continue;
    const size = Math.hypot(transform[2], transform[3]) || Math.abs(item.height) || 10;
    const text = item.str.replace(/[\uE000-\uF8FF\u00a0\u3000\u2002-\u200b]/g, ' ');
    const font = page.commonObjs.has(item.fontName)
      ? page.commonObjs.get(item.fontName) as { name?: string } | null
      : null;
    const style = fontInfo(font?.name ?? content.styles[item.fontName]?.fontFamily ?? '');
    raw.push({ text, font: item.fontName, width: item.width, transform, size, ...style });
  }
  if (total > 20 && bad / total > 0.15) return { garbled: true, items: [] };

  // The prototype treats bullet-like spacer glyphs as spaces only for affected fonts.
  const dotFonts = new Map<string, { spaces: number; total: number }>();
  raw.forEach((run, i) => {
    if (!run.text.includes('●')) return;
    const previous = raw[i - 1]?.text ?? '';
    const next = raw[i + 1]?.text ?? '';
    const count = dotFonts.get(run.font) ?? { spaces: 0, total: 0 };
    for (let j = 0; j < run.text.length; j++) {
      if (run.text[j] !== '●') continue;
      count.total++;
      if (isWordChar(j > 0 ? run.text[j - 1] : previous.slice(-1)) && isWordChar(run.text[j + 1] ?? next[0])) {
        count.spaces++;
      }
    }
    dotFonts.set(run.font, count);
  });
  const items = raw.map((run): TextRun => {
    const dots = dotFonts.get(run.font);
    const text = dots && dots.spaces > 0 && dots.spaces >= dots.total * 0.4
      ? run.text.replace(/●/g, ' ') : run.text;
    return {
      text, x: run.transform[4], y: run.transform[5] - run.size * 0.88, width: run.width,
      size: run.size, family: run.family, weight: run.weight, bold: run.weight >= 700,
      figure: false, onImage: false, color: null,
    };
  });
  return { garbled: false, items };
}

function readBackground(run: TextRun, masked: Raster, pixels: Uint8ClampedArray, scale: number) {
  const { width, height } = masked.canvas;
  const box = textRectangle(runRect(run), scale, width, height, 0.12);
  if (!box) return false;
  const { x0, y0, x1, y1 } = box;
  const sample = ringMode(pixels, width, height, x0, y0, x1, y1, 2);
  if (!sample || sample.share < 0.45) return false;
  masked.context.fillStyle = rgb(sample.color);
  masked.context.fillRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1);
  return true;
}

function plainBackground(rect: TextRect, pixels: Uint8ClampedArray, width: number, height: number, scale: number) {
  const box = textRectangle(rect, scale, width, height, 0);
  if (!box) return false;
  const { x0, y0, x1, y1 } = box;
  const sample = ringMode(pixels, width, height, x0, y0, x1, y1, 1);
  if (!sample || sample.share < 0.9) return false;
  let count = 0;
  let different = 0;
  for (let y = y0; y <= y1; y += 2) {
    for (let x = x0; x <= x1; x += 2) {
      const i = (y * width + x) * 4;
      if (sample.color.some((channel, c) => Math.abs(channel - pixels[i + c]) > 14)) different++;
      count++;
    }
  }
  return different < count * 0.02;
}

async function readPage(page: PDFPageProxy, number: number): Promise<PdfPage> {
  const viewport = page.getViewport({ scale: 1 });
  const scale = renderScale(viewport);
  const rasterViewport = page.getViewport({ scale });
  const original = await renderOriginal(page, rasterViewport);
  let masked: Raster | undefined;
  let background: Raster | undefined;
  try {
    const result: PdfPage = {
      number, width: viewport.width, height: viewport.height, scale,
      imageUrl: original.canvas.toDataURL('image/png'),
      blocks: [], figures: [], images: [], shapes: [], boxes: [],
    };

    try {
      const { items, garbled } = await extractRuns(page, viewport);
      if (garbled || items.reduce((sum, run) => sum + run.text.replace(/\s/g, '').length, 0) < 8) {
        result.fallback = garbled
          ? `${number}쪽은 글꼴 정보가 깨져 원본을 유지합니다.`
          : `${number}쪽은 선택 가능한 글자가 부족해 원본을 유지합니다.`;
        return result;
      }
      const { images, shapes, area, textClipping } = await analyzeShapes(page, viewport);
      const figures = figureRegions(images, shapes, items, area);
      const { width, height } = original.canvas;
      const pixels = original.context.getImageData(0, 0, width, height).data;
      const maskedPage = masked = createRaster(width, height);
      maskedPage.context.drawImage(original.canvas, 0, 0);
      for (const item of items) {
        if (!item.text.trim()) continue;
        const cx = item.x + item.width / 2;
        const cy = item.y + item.size / 2;
        item.figure = figures.some(box => cx > box.x && cx < box.x1 && cy > box.y && cy < box.y1);
        item.onImage = item.figure || !readBackground(item, maskedPage, pixels, scale);
        if (!item.onImage) item.color = inkColor(original, maskedPage, scale, runRect(item));
      }
      const sizes = items.filter(run => run.text.trim() && !run.figure).map(run => run.size).sort((a, b) => a - b);
      const middle = sizes[Math.floor(sizes.length / 2)] ?? 10;
      result.blocks = toBlocks(items, images, shapes).map(({ figureOnly, ...block }, i) => {
        block.id = `p${number}b${i + 1}`;
        // Long, sentence-like labels on a uniform background can still be rewritten.
        if (block.preserve && figureOnly) {
          const text = block.text.replace(/[\s‸]/g, '');
          const digits = (text.match(/[0-9]/g) ?? []).length;
          const sentence = text.length >= 8 && digits / text.length < 0.15 && /[\s‸]/.test(block.text) &&
            /[다요어지까네야게죠군나라자]\s*[.?!…~]*\s*$/.test(block.text);
          if ((text.length / block.lineCount >= 22 && block.fontSize >= middle * 0.9) ||
              (sentence && block.fontSize >= middle * 0.75)) {
            const samples = block.runs.map(rect => {
              const pixelRect = textRectangle(rect, scale, width, height, 0.12);
              return pixelRect && ringMode(pixels, width, height,
                pixelRect.x0, pixelRect.y0, pixelRect.x1, pixelRect.y1, 2);
            });
            if (samples.every(sample => sample && sample.share >= 0.45)) {
              block.preserve = false;
              samples.forEach((sample, j) => {
                const pixelRect = textRectangle(block.runs[j], scale, width, height, 0.12)!;
                maskedPage.context.fillStyle = rgb(sample!.color);
                maskedPage.context.fillRect(pixelRect.x0, pixelRect.y0,
                  pixelRect.x1 - pixelRect.x0 + 1, pixelRect.y1 - pixelRect.y0 + 1);
              });
              const colors = block.runs.map(rect => inkColor(original, maskedPage, scale, rect));
              const ink = colors.find(color => color !== null);
              if (ink) block.color = rgb(ink);
            }
          }
        }
        return block;
      });
      releaseRaster(maskedPage);
      masked = undefined;
      result.figures = figures;
      result.images = images;
      result.shapes = shapes.filter(box => (box.x1 - box.x) * (box.y1 - box.y) < area * 0.3);
      result.boxes = shapes.filter(box =>
        box.segments <= 10 && box.x1 - box.x > 20 && box.y1 - box.y > 10 &&
        (box.x1 - box.x) * (box.y1 - box.y) < area * 0.6);
      let backgroundPixels: Uint8ClampedArray | undefined;
      if (!textClipping) {
        try {
          background = await renderOriginal(page, rasterViewport, omitText);
          backgroundPixels = background.context.getImageData(0, 0, width, height).data;
          result.backgroundUrl = background.canvas.toDataURL('image/png');
        } catch {
          // Without trustworthy background pixels the renderer can preserve the original.
        }
      }
      classifyLayout(result, shapes, run => backgroundPixels !== undefined
        && plainBackground(run, backgroundPixels, width, height, scale));
      const cleanRaster = background;
      if (cleanRaster && backgroundPixels) {
        for (const block of result.blocks) {
          if (block.layout !== 'text' || block.color !== '#222') continue;
          const ink = block.runs.map(run => inkColor(original, cleanRaster, scale, run)).find(Boolean);
          if (ink) block.color = rgb(ink);
        }
      }
      addLayoutAids(result);
      return result;
    } catch {
      return { ...result, blocks: [], fallback: `${number}쪽 배치를 분석하지 못해 원본을 유지합니다.` };
    }
  } finally {
    if (masked) releaseRaster(masked);
    if (background) releaseRaster(background);
    releaseRaster(original);
    page.cleanup();
  }
}

export async function openSourcePdf(file: Blob): Promise<PdfSource> {
  const task = getDocument({
    data: new Uint8Array(await file.arrayBuffer()),
    useWorkerFetch: false, cMapPacked: true, BinaryDataFactory: LocalPdfData,
    canvasMaxAreaInBytes: 24 * 1024 * 1024,
  });
  const pdf = await task.promise.catch(async error => {
    await task.destroy().catch(() => undefined);
    throw new Error(error instanceof PasswordException
      ? '암호가 걸린 PDF입니다. 잠금을 해제한 파일을 올려 주세요.'
      : 'PDF를 열 수 없습니다. 파일 형식과 상태를 확인해 주세요.');
  });
  let closing: Promise<void> | undefined;
  return {
    pageCount: pdf.numPages,
    async previewPage(number) {
      if (closing) throw new Error('PDF가 닫혔습니다.');
      const page = await pdf.getPage(number);
      const originalSize = page.getViewport({ scale: 1 });
      try {
        const viewport = page.getViewport({ scale: renderScale(originalSize) });
        const raster = await renderOriginal(page, viewport);
        try {
          return { number, width: originalSize.width, height: originalSize.height, imageUrl: raster.canvas.toDataURL('image/png') };
        } finally {
          releaseRaster(raster);
        }
      } finally {
        page.cleanup();
      }
    },
    async readPages(from, to, onProgress) {
      if (closing) throw new Error('PDF가 닫혔습니다.');
      if (![from, to].every(Number.isInteger) || from < 1 || to < from || to > pdf.numPages || to - from >= 10) {
        throw new Error('연속된 최대 10쪽을 선택해 주세요.');
      }
      const total = to - from + 1;
      const pages: PdfPage[] = [];
      onProgress?.(0, total);
      for (let number = from; number <= to; number++) {
        pages.push(await readPage(await pdf.getPage(number), number));
        onProgress?.(pages.length, total);
      }
      return pages;
    },
    close() {
      closing ??= task.destroy();
      return closing;
    },
  };
}
