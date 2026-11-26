import { previewFont } from './fonts';
import { pixelRectangle } from './raster';
import type { Raster, Rectangle } from './raster';
import type { Box, PdfGlossResult, PdfPage } from './types';

export interface GlossLine {
  text: string;
  x: number;
  baseline: number;
  font: string;
  size: number;
  width?: number;
  inkTop?: number;
  inkBottom?: number;
  container?: Box;
}

interface NativeGloss {
  term: string;
  translation: string;
  language: string;
}

export function nativeGlosses(translations?: readonly Record<string, string>[]) {
  const glosses = new Map<string, NativeGloss>();
  for (const entry of translations ?? []) {
    const term = entry.term?.trim();
    const translation = entry.translation?.trim().replace(/\s+/g, ' ');
    if (term && translation && term !== translation && !glosses.has(term)) {
      glosses.set(term, { term, translation, language: entry.language?.toLowerCase() ?? '' });
    }
  }
  return [...glosses.values()].sort((a, b) => b.term.length - a.term.length);
}

const KO_SUFFIX = /^(?:은|는|이|가|을|를|과|와|에|에서|에게|에는|에도|의|께|으로|로|으로는|로는|부터|까지|만|도|이나|나|이다|입니다|이었다|이었다가)$/u;
const OVERLAP = (a: Rectangle, b: Rectangle) =>
  a.x0 <= b.x1 && a.x1 >= b.x0 && a.y0 <= b.y1 && a.y1 >= b.y0;

function* termOffsets(text: string, term: string) {
  let offset = text.indexOf(term);
  while (offset >= 0) {
    const end = offset + term.length;
    const before = text.slice(0, offset);
    const suffix = /^[\p{L}\p{N}]+/u.exec(text.slice(end))?.[0];
    if (!/[\p{L}\p{N}]$/u.test(before) && (!suffix || (/[가-힣]$/u.test(term) && KO_SUFFIX.test(suffix)))) {
      yield offset;
    }
    offset = text.indexOf(term, end);
  }
}

// Canvas paints the same glosses into the preview raster and the exported PDF.
// Restrict them to light, unoccupied pixels even if the PDF's geometry missed a decoration.
function lightBlank(pixels: Uint8ClampedArray, width: number, bounds: Rectangle) {
  const first = (bounds.y0 * width + bounds.x0) * 4;
  if (pixels[first + 3] < 250 || Math.min(pixels[first], pixels[first + 1], pixels[first + 2]) < 180) return false;
  for (let y = bounds.y0; y <= bounds.y1; y++) {
    for (let x = bounds.x0; x <= bounds.x1; x++) {
      const i = (y * width + x) * 4;
      if (pixels[i + 3] < 250 || Math.abs(pixels[i] - pixels[first]) > 16 ||
          Math.abs(pixels[i + 1] - pixels[first + 1]) > 16 || Math.abs(pixels[i + 2] - pixels[first + 2]) > 16) {
        return false;
      }
    }
  }
  return true;
}

function labelFont(language: string, size: number) {
  const han = /^(zh-(tw|hk|mo|hant)|yue)(-|$)/.test(language)
    ? '"Noto Sans CJK TC", "Microsoft JhengHei", '
    : /^zh(-|$)/.test(language) ? '"Noto Sans CJK SC", "Microsoft YaHei", ' : '';
  return `600 ${size}px "${previewFont}", ${han}"Noto Sans", "Noto Sans Arabic", "Noto Sans Thai", "Noto Sans Mongolian", "Arial", sans-serif`;
}

export function drawPageGlosses(
  raster: Raster, page: Pick<PdfPage, 'number' | 'width' | 'height' | 'scale'>,
  lines: readonly GlossLine[], glosses: readonly NativeGloss[], obstacles: readonly Rectangle[],
): PdfGlossResult[] {
  if (!lines.length || !glosses.length) return [];
  const { context, canvas } = raster;
  const { width, height } = canvas;
  const pixels = context.getImageData(0, 0, width, height).data;
  const occupied = [...obstacles];
  const placed = new Map<string, PdfGlossResult>();

  context.save();
  try {
    context.scale(page.scale, page.scale);
    context.textBaseline = 'alphabetic';
    context.textAlign = 'left';
    context.fillStyle = '#155752';

    for (const line of lines) {
      for (const gloss of glosses) {
        if (placed.has(gloss.term) || !line.text.includes(gloss.term)) continue;
        for (const offset of termOffsets(line.text, gloss.term)) {
          context.direction = 'ltr';
          context.font = line.font;
          const metrics = context.measureText(line.text);
          const widthScale = line.width != null && metrics.width > 0 ? line.width / metrics.width : 1;
          const to = offset + gloss.term.length;
          const center = line.x + widthScale * (context.measureText(line.text.slice(0, offset)).width
            + context.measureText(line.text.slice(0, to)).width) / 2;
          const inkTop = line.inkTop ?? line.baseline - metrics.actualBoundingBoxAscent;
          const inkBottom = line.inkBottom ?? line.baseline + metrics.actualBoundingBoxDescent;
          const region = line.container ?? { x: 0, y: 0, x1: page.width, y1: page.height };
          const gap = Math.max(1, 1.5 / page.scale);

          context.direction = /^(ar|fa|he|ur|ps|yi)(-|$)/.test(gloss.language) ? 'rtl' : 'ltr';
          const preferredSize = Math.min(9, Math.max(6, line.size * 0.62));
          for (const size of new Set([preferredSize, Math.max(6, preferredSize * 0.86)])) {
            context.font = labelFont(gloss.language, size);
            const label = context.measureText(gloss.translation);
            const labelLeft = Math.min(0, -label.actualBoundingBoxLeft);
            const labelRight = Math.max(label.width, label.actualBoundingBoxRight);
            const labelWidth = labelRight - labelLeft;
            const x = Math.min(region.x1 - labelWidth - gap, Math.max(region.x + gap, center - labelWidth / 2));
            if (x > center || x + labelWidth < center || x - gap < region.x) continue;

            for (const baseline of [inkTop - gap - label.actualBoundingBoxDescent,
              inkBottom + gap + label.actualBoundingBoxAscent]) {
              const box = {
                x, x1: x + labelWidth,
                y: baseline - label.actualBoundingBoxAscent, y1: baseline + label.actualBoundingBoxDescent,
              };
              if (box.y - gap < region.y || box.y1 + gap > region.y1) continue;
              const bounds = pixelRectangle(box, page.scale, width, height, 1);
              if (!bounds || occupied.some(other => OVERLAP(bounds, other)) || !lightBlank(pixels, width, bounds)) continue;
              context.fillText(gloss.translation, x - labelLeft, baseline);
              occupied.push(bounds);
              placed.set(gloss.term, { page: page.number, term: gloss.term, translation: gloss.translation });
              break;
            }
            if (placed.has(gloss.term)) break;
          }
          if (placed.has(gloss.term)) break;
        }
      }
    }
  } finally {
    context.restore();
  }
  return [...placed.values()];
}
