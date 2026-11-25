import { AnnotationMode, OPS, Util } from 'pdfjs-dist';
import type { PageViewport, PDFPageProxy } from 'pdfjs-dist';
import { rgb } from './raster';
import type { Box, FontFamily, PdfBlock, PdfPage, PdfProtectedRegion, Rgb, TextRect } from './types';

export interface TextRun {
  text: string;
  x: number;
  y: number;
  width: number;
  size: number;
  family: FontFamily;
  weight: number;
  bold: boolean;
  figure: boolean;
  onImage: boolean;
  color: Rgb | null;
}

export interface Shape extends Box {
  segments: number;
  closed: boolean;
}

export const runRect = (run: TextRun): TextRect => [run.x, run.y, run.width, run.size];

export function fontInfo(fontName: string): { family: FontFamily; weight: number } {
  const name = fontName.replace(/^[A-Z]{6}\+/, '');
  let family: FontFamily = 'sans';
  if (/myeong|mj|batang|serif|ming|song|명조|바탕/i.test(name)) family = 'serif';
  else if (/pen|script|hand|brush|calli|손글씨/i.test(name)) family = 'hand';
  else if (/round|dunggeun|둥근/i.test(name)) family = 'round';
  let weight = 0;
  if (/black|heavy|extra.?bold|ultra.?bold|EB$|XB$/i.test(name)) weight = 800;
  else if (/B$|-B\b|_B\b/.test(name) || /bold/i.test(name)) weight = 700;
  else if (/medium|M$/.test(name)) weight = 500;
  else if (/L$/.test(name) || /light|thin/i.test(name)) weight = 300;
  return { family, weight };
}

const areaOf = (box: Box) => Math.max(0, box.x1 - box.x) * Math.max(0, box.y1 - box.y);
const within = (box: Box, x: number, y: number) => x > box.x && x < box.x1 && y > box.y && y < box.y1;
export const boxContains = (box: Box, block: PdfBlock) =>
  within(box, block.x + block.width / 2, block.y + block.height / 2);

// PDF.js 6 packs the path operations with a bounding box on the paint operator.
function pathShape(path: ArrayLike<number>) {
  const lengths = [3, 3, 7, 5, 1]; // move, line, cubic, quadratic, close
  let segments = 0;
  let closed = false;
  for (let i = 0; i < path.length; i += lengths[path[i]] ?? path.length) {
    segments++;
    closed ||= path[i] === 4;
  }
  return { segments, closed };
}

export async function analyzeShapes(page: PDFPageProxy, viewport: PageViewport) {
  const operators = await page.getOperatorList({ annotationMode: AnnotationMode.DISABLE });
  const imageOps = new Set<number>([
    OPS.paintImageXObject, OPS.paintInlineImageXObject, OPS.paintImageMaskXObject,
  ]);
  const matrices: number[][] = [];
  const images: Box[] = [];
  const shapes: Shape[] = [];
  let textClipping = false;
  let current = [1, 0, 0, 1, 0, 0];
  const toBox = (bounds: ArrayLike<number>, matrix = current): Box => {
    const result = [Infinity, Infinity, -Infinity, -Infinity];
    Util.axialAlignedBoundingBox(Array.from(bounds), Util.transform(viewport.transform, matrix), result);
    return {
      x: Math.max(0, result[0]), y: Math.max(0, result[1]),
      x1: Math.min(viewport.width, result[2]), y1: Math.min(viewport.height, result[3]),
    };
  };
  const addRepeatedImages = (matrix: number[], positions: ArrayLike<number>) => {
    for (let i = 0; i + 1 < positions.length; i += 2) {
      const transform = [...matrix, positions[i], positions[i + 1]];
      images.push(toBox([0, 0, 1, 1], Util.transform(current, transform)));
    }
  };
  const addImageGroup = (group: { transform: number[] }[]) => {
    for (const { transform } of group) {
      images.push(toBox([0, 0, 1, 1], Util.transform(current, transform)));
    }
  };
  for (let i = 0; i < operators.fnArray.length; i++) {
    const operation = operators.fnArray[i];
    const args = operators.argsArray[i];
    if (operation === OPS.save || operation === OPS.paintFormXObjectBegin) {
      matrices.push(current);
      if (operation === OPS.paintFormXObjectBegin && args?.[0]) current = Util.transform(current, args[0]);
    } else if (operation === OPS.restore || operation === OPS.paintFormXObjectEnd) {
      current = matrices.pop() ?? current;
    } else if (operation === OPS.transform) {
      current = Util.transform(current, args);
    } else if (operation === OPS.setTextRenderingMode && (args[0] & 4)) {
      textClipping = true;
    } else if (operation === OPS.constructPath) {
      const [paint, data, bounds] = args;
      const path = data?.[0] as ArrayLike<number> | null;
      if (paint !== OPS.endPath && path && bounds) {
        const shape = pathShape(path);
        if (paint === OPS.fill || paint === OPS.eoFill) shape.closed = true;
        shapes.push({ ...toBox(bounds), ...shape });
      }
    } else if (imageOps.has(operation)) {
      images.push(toBox([0, 0, 1, 1]));
    } else if (operation === OPS.paintImageXObjectRepeat) {
      const [, sx, sy, positions] = args as [string, number, number, ArrayLike<number>];
      addRepeatedImages([sx, 0, 0, sy], positions);
    } else if (operation === OPS.paintImageMaskXObjectRepeat) {
      const [, sx, skewX, skewY, sy, positions] = args as
        [unknown, number, number, number, number, ArrayLike<number>];
      addRepeatedImages([sx, skewX, skewY, sy], positions);
    } else if (operation === OPS.paintInlineImageXObjectGroup) {
      addImageGroup(args[1]);
    } else if (operation === OPS.paintImageMaskXObjectGroup) {
      addImageGroup(args[0]);
    }
  }
  const area = viewport.width * viewport.height;
  return {
    images: images.filter(box => areaOf(box) > area * 0.004 && areaOf(box) < area * 0.85),
    shapes: shapes.filter(box => box.x1 > box.x - 0.01 && box.y1 > box.y - 0.01),
    area,
    textClipping,
  };
}

export function figureRegions(images: Box[], shapes: Shape[], items: TextRun[], area: number): Box[] {
  const pieces = shapes.filter(shape => {
    const w = shape.x1 - shape.x;
    const h = shape.y1 - shape.y;
    if (Math.min(w, h) < 2.2 && Math.max(w, h) > 12) return false; // rules and underlines
    if (w * h > area * 0.35 || w * h <= 0.5) return false;
    return !(shape.segments <= 10 && w * h > area * 0.01 && items.some(item =>
      item.text.trim() && within(shape, item.x + item.width / 2, item.y + item.size / 2)));
  });
  const boxes = [
    ...images.map(box => ({ ...box, image: 1 })),
    ...pieces.map(box => ({ ...box, image: 0 })),
  ].sort((a, b) => a.y - b.y);
  const parent = boxes.map((_, i) => i);
  const find = (i: number) => {
    while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; }
    return i;
  };
  for (let i = 0; i < boxes.length; i++) {
    const a = boxes[i];
    for (let j = i + 1; j < boxes.length && boxes[j].y - 6 < a.y1; j++) {
      const b = boxes[j];
      if (a.x - 6 < b.x1 && b.x - 6 < a.x1 && a.y - 6 < b.y1) {
        const left = find(i);
        const right = find(j);
        if (left !== right) parent[left] = right;
      }
    }
  }

  const groups = new Map<number, Box & { count: number; image: number }>();
  boxes.forEach((box, i) => {
    const root = find(i);
    const group = groups.get(root);
    if (!group) {
      groups.set(root, { ...box, count: 1 });
    } else {
      group.x = Math.min(group.x, box.x);
      group.y = Math.min(group.y, box.y);
      group.x1 = Math.max(group.x1, box.x1);
      group.y1 = Math.max(group.y1, box.y1);
      group.count++;
      group.image += box.image;
    }
  });
  return [...groups.values()]
    .filter(group => (group.image || (group.count >= 5 && areaOf(group) > area * 0.01)) && areaOf(group) < area * 0.7)
    .map(group => ({ x: group.x - 3, y: group.y - 3, x1: group.x1 + 3, y1: group.y1 + 3 }));
}

type Votes<T> = Map<T, number>;
const vote = <T>(votes: Votes<T>, key: T, weight: number) =>
  votes.set(key, (votes.get(key) ?? 0) + weight);
function winner<T>(votes: Votes<T>): T | undefined {
  let key: T | undefined;
  let largest = 0;
  for (const [option, count] of votes) if (count > largest) { key = option; largest = count; }
  return key;
}
const farColor = (a: Rgb | null, b: Rgb | null) =>
  a !== null && b !== null && a.reduce((sum, value, i) => sum + Math.abs(value - b[i]), 0) > 90;

interface Line {
  text: string;
  x: number;
  x1: number;
  end: number;
  y: number;
  size: number;
  bold: boolean;
  color: Rgb | null;
  colors: Votes<string>;
  fonts: Votes<string>;
  figure: boolean;
  imageChars: number;
  charCount: number;
  runs: TextRun[];
}

interface BlockDraft extends Box {
  text: string;
  size: number;
  bold: boolean;
  color: Rgb | null;
  colors: Votes<string>;
  fonts: Votes<string>;
  preserve: boolean;
  figureOnly: boolean;
  firstX: number;
  lines: Line[];
  runs: TextRun[];
}

const rgbKey = (color: Rgb) => color.join(',');
function colorWinner(votes: Votes<string>): Rgb | null {
  const color = winner(votes);
  return color ? color.split(',').map(Number) as Rgb : null;
}

function mergeLines(items: TextRun[], images: Box[]): Line[] {
  items.sort((a, b) => (a.y + a.size) - (b.y + b.size) || a.x - b.x);
  const lines: Line[] = [];
  const imageGap = (line: Line, item: TextRun) => images.some(box =>
    box.x < item.x && box.x1 > line.end && box.y < item.y + item.size && box.y1 > item.y &&
    box.y1 - box.y < item.size * 3 && box.x1 - box.x < item.size * 4);
  for (const item of items) {
    const whitespace = !item.text.trim();
    const line = lines.slice(-6).reverse().find(line =>
      Math.abs(line.y + line.size - item.y - item.size) < Math.max(line.size, item.size) * 0.45 &&
      item.x >= line.x1 - item.size * 0.8 &&
      (item.x - line.x1 < item.size * 1.4 || (item.x - line.x1 < item.size * 5 && imageGap(line, item))) &&
      Math.abs(line.size - item.size) < Math.max(line.size, item.size) * 0.35 &&
      (whitespace || line.figure === item.figure) &&
      !(farColor(line.color, item.color) && item.x - line.end > item.size * 0.5));
    if (whitespace) {
      if (line && !/\s$/.test(line.text)) line.text += ' ';
      continue;
    }
    const count = item.text.length;
    const font = `${item.family}|${item.weight}`;
    if (line) {
      const gap = item.x - line.x1;
      if (gap > item.size * 0.18 && !/\s$/.test(line.text) && !/^\s/.test(item.text)) line.text += ' ';
      line.text += item.text;
      line.x1 = Math.max(line.x1, item.x + item.width);
      line.end = item.x + item.width;
      line.y = Math.min(line.y, item.y);
      line.size = Math.max(line.size, item.size);
      line.bold = line.bold && item.bold;
      if (item.color) vote(line.colors, rgbKey(item.color), count);
      line.color = colorWinner(line.colors);
      vote(line.fonts, font, count);
      if (item.onImage) line.imageChars += count;
      line.charCount += count;
      line.runs.push(item);
    } else {
      const colors: Votes<string> = new Map();
      if (item.color) vote(colors, rgbKey(item.color), count);
      lines.push({
        text: item.text, x: item.x, x1: item.x + item.width, end: item.x + item.width,
        y: item.y, size: item.size, bold: item.bold, color: item.color, colors,
        fonts: new Map([[font, count]]), figure: item.figure,
        imageChars: item.onImage ? count : 0, charCount: count, runs: [item],
      });
    }
  }
  return lines.sort((a, b) => a.y - b.y || a.x - b.x);
}

const hardEnd = /[.!?。」”)]\s*$/;
const beginsList = /^[•·▪◦○●■□▶◆◇※\-–]|^\d+[.)]|^[①-⑳]/;

export function toBlocks(items: TextRun[], images: Box[], shapes: Box[]): (PdfBlock & { figureOnly: boolean })[] {
  const lines = mergeLines(items, images);
  const marks = [
    ...lines.filter(line => /^\s*([0-9]{1,2}|[①-⑳❶-❿➊-➓])\s*$/.test(line.text))
      .map(line => ({ x: line.x, x1: line.x1, y: line.y, y1: line.y + line.size, number: true })),
    ...[...images, ...shapes].map(box => ({ ...box, number: false })),
  ];
  const startsParagraph = (block: BlockDraft, line: Line) => {
    const previous = block.lines[block.lines.length - 1];
    if (hardEnd.test(previous.text) && line.x - block.x > line.size * 0.6) return true;
    return marks.some(mark => {
      const w = mark.x1 - mark.x;
      const h = mark.y1 - mark.y;
      const size = line.size;
      if (!mark.number && (w < size * 0.6 || h < size * 0.6 || w > size * 3 ||
          h > size * 3 || w / h > 1.6 || h / w > 1.6)) return false;
      return mark.x1 <= line.x + size * 0.6 && mark.x1 > line.x - size * 4 &&
        mark.y < line.y + size * 0.9 && mark.y1 > line.y + size * 0.1 &&
        !(mark.y < previous.y + previous.size * 0.5 && mark.y1 > previous.y + previous.size * 0.5);
    });
  };

  const drafts: BlockDraft[] = [];
  for (const line of lines) {
    const preserve = line.figure || line.imageChars > line.charCount / 2;
    const block = drafts.slice(-40).reverse().find(block =>
      Math.abs(block.size - line.size) < block.size * 0.2 && block.bold === line.bold &&
      line.y - block.y1 < line.size * 1.25 && line.y - block.y1 > -line.size * 0.4 &&
      line.x < block.x1 && line.x1 > block.x && Math.abs(line.x - block.x) < line.size * 3 &&
      !farColor(block.color, line.color) && block.preserve === preserve && !startsParagraph(block, line));
    if (!block) {
      drafts.push({
        text: line.text, x: line.x, x1: line.x1, y: line.y, y1: line.y + line.size,
        size: line.size, bold: line.bold, color: line.color, colors: new Map(line.colors),
        fonts: new Map(line.fonts), preserve, figureOnly: line.figure, firstX: line.x,
        lines: [line], runs: [...line.runs],
      });
      continue;
    }

    const previous = block.lines[block.lines.length - 1];
    const short = previous.end - block.x < (Math.max(block.x1, line.x1) - block.x) * 0.9;
    const keepLine = (hardEnd.test(previous.text) && short) || beginsList.test(line.text.trim());
    block.text = block.text.trimEnd() + (keepLine ? '\n' : /\s$/.test(previous.text) ? ' ' : '‸') + line.text.trim();
    for (const [key, weight] of line.colors) vote(block.colors, key, weight);
    for (const [key, weight] of line.fonts) vote(block.fonts, key, weight);
    block.lines.push(line);
    for (const run of line.runs) block.runs.push(run);
    block.figureOnly = block.figureOnly && line.figure;
    block.x = Math.min(block.x, line.x);
    block.x1 = Math.max(block.x1, line.x1);
    block.y1 = line.y + line.size;
  }

  return drafts.map(block => {
    const count = block.lines.length;
    const pitch = count > 1 ? (block.lines[count - 1].y - block.lines[0].y) / (count - 1) : block.size * 1.45;
    const lineHeight = Math.min(2.4, Math.max(1.1, pitch / block.size));
    const color = colorWinner(block.colors);
    const [family, weight] = (winner(block.fonts) ?? 'sans|0').split('|');
    return {
      id: '',
      text: block.text.replace(/[ \t]+/g, ' ').replace(/ ?\n ?/g, '\n').trim(),
      x: block.x, y: block.y, width: block.x1 - block.x,
      height: ((count - 1) * lineHeight + 1) * block.size,
      fontSize: block.size, lineHeight, lineCount: count,
      family: family as FontFamily, weight: Number(weight) || (block.bold ? 700 : 0),
      indent: Math.max(0, block.firstX - block.x), color: color ? rgb(color) : '#222',
      preserve: block.preserve || block.runs.some(run => run.onImage), figureOnly: block.figureOnly,
      runs: block.runs.map(runRect),
      lines: block.lines.map((line): TextRect => [line.x, line.y, line.x1 - line.x, line.size]),
      room: 0, inner: false,
    };
  }).filter(block => block.text);
}

const marker = /^(?:[0-9]{1,4}[.)]?|[①-⑳❶-❿➊-➓]|[•·▪◦○●■□▶◆◇※]+)$/u;
const sectionLabel = /^(?:처음|가운데|끝|시작|중간|마지막|본문|결론|항목|구분|번호|순서|단계)$/u;
const textBounds = ([x, y, w, fs]: TextRect): Box => ({ x, y, x1: x + w, y1: y + fs });
const containsBounds = (box: Box, inner: Box, tolerance = 0) =>
  inner.x >= box.x - tolerance && inner.x1 <= box.x1 + tolerance &&
  inner.y >= box.y - tolerance && inner.y1 <= box.y1 + tolerance;
const inflate = (box: Box, amount: number): Box =>
  ({ x: box.x - amount, y: box.y - amount, x1: box.x1 + amount, y1: box.y1 + amount });

function badgeFor(rect: TextRect, shapes: Shape[]): Shape | undefined {
  const [, , , size] = rect;
  const bounds = textBounds(rect);
  return shapes.find(shape => {
    const width = shape.x1 - shape.x;
    const height = shape.y1 - shape.y;
    return shape.closed && width >= size * 0.6 && width <= size * 2.3 &&
      height >= size * 0.6 && height <= size * 2.3 && containsBounds(shape, bounds, 1);
  });
}

function keepLeadingBadge(block: PdfBlock, shapes: Shape[]): Shape | undefined {
  const prefix = /^(\S+)\s+/u.exec(block.text);
  const [first, ...body] = block.runs;
  if (!prefix || !marker.test(prefix[1]) || !first || !body.length || first[2] > first[3] * 1.3) return;
  const badge = badgeFor(first, shapes);
  if (!badge || body[0][0] < badge.x1 - 1) return;

  const left = Math.min(...body.map(run => run[0]));
  const right = Math.max(...body.map(run => run[0] + run[2]));
  const firstLine = body.filter(run => Math.abs(run[1] - body[0][1]) < block.fontSize * 0.45);
  const firstLeft = Math.min(...firstLine.map(run => run[0]));
  const firstRight = Math.max(...firstLine.map(run => run[0] + run[2]));
  block.lines[0] = [firstLeft, body[0][1], firstRight - firstLeft, block.lines[0][3]];
  block.structuralRuns = [first];
  block.structuralPrefix = prefix[1];
  block.text = block.text.slice(prefix[0].length);
  block.runs = body;
  block.x = left;
  block.width = right - left;
  block.indent = Math.max(0, firstLeft - left);
  return badge;
}

function cellBounds(block: PdfBlock, enclosure: Box, rules: Box[]): Box {
  const inside = { ...enclosure };
  const glyphs = {
    x: block.x, x1: block.x + block.width, y: block.y, y1: block.y + block.height,
  };
  for (const rule of rules) {
    if (rule.x <= glyphs.x + 0.5 && rule.x1 >= glyphs.x1 - 0.5 && rule.y1 - rule.y < 2.2) {
      if (rule.y1 < glyphs.y) inside.y = Math.max(inside.y, rule.y1);
      if (rule.y > glyphs.y1) inside.y1 = Math.min(inside.y1, rule.y);
    }
    if (rule.y <= glyphs.y + 0.5 && rule.y1 >= glyphs.y1 - 0.5 && rule.x1 - rule.x < 2.2) {
      if (rule.x1 < glyphs.x) inside.x = Math.max(inside.x, rule.x1);
      if (rule.x > glyphs.x1) inside.x1 = Math.min(inside.x1, rule.x);
    }
  }
  return inflate(inside, -Math.max(1.5, block.fontSize * 0.12));
}

/** Classify the existing blocks without regrouping them or changing their IDs. */
export function classifyLayout(page: PdfPage, shapes: Shape[], plainRun: (run: TextRect) => boolean) {
  const area = page.width * page.height;
  const enclosures = shapes.filter(shape => shape.closed && shape.segments <= 12 &&
    shape.x1 - shape.x > 18 && shape.y1 - shape.y > 10 && areaOf(shape) < area * 0.6)
    .sort((a, b) => areaOf(a) - areaOf(b));
  const rules = shapes.filter(shape => {
    const width = shape.x1 - shape.x;
    const height = shape.y1 - shape.y;
    return Math.min(width, height) < 2.2 && Math.max(width, height) > 20;
  });
  const protectedRegions: PdfProtectedRegion[] = [
    ...page.images.map(box => ({ ...box, kind: 'image' as const })),
    ...rules.map(box => ({ ...inflate(box, 0.65), kind: 'rule' as const })),
  ];
  for (const block of page.blocks) {
    const badge = keepLeadingBadge(block, shapes);
    if (badge) protectedRegions.push({ ...badge, kind: 'badge', blockId: block.id });
    const enclosure = enclosures.find(box => block.runs.every(run => containsBounds(box, textBounds(run), 0.7)));
    const number = marker.test(block.text);
    const nearEdge = block.x < page.width * 0.1 || block.x + block.width > page.width * 0.9;
    const margin = number && nearEdge && (block.y < page.height * 0.085 || block.y > page.height * 0.92);
    const rowLabel = enclosure && sectionLabel.test(block.text) && enclosure.x1 - enclosure.x < page.width * 0.25;
    if (margin || number || rowLabel) {
      block.preserve = true;
      block.layout = margin ? 'margin' : 'structure';
      const markerShape = number && badgeFor(block.runs[0], shapes);
      if (markerShape) protectedRegions.push({ ...markerShape, kind: 'badge', blockId: block.id });
      for (const run of block.runs) {
        protectedRegions.push({ ...textBounds(run), kind: block.layout, blockId: block.id });
      }
      continue;
    }

    const onImage = page.images.some(box => block.runs.some(run =>
      within(box, run[0] + run[2] / 2, run[1] + run[3] / 2)));
    const clean = (onImage || (block.preserve && enclosure)) && block.runs.every(plainRun);
    if (onImage && !clean) block.preserve = true;
    if (enclosure && clean && /\p{L}/u.test(block.text)) block.preserve = false;
    block.layout = block.preserve ? 'image' : 'text';
    if (block.preserve) {
      for (const run of block.runs) protectedRegions.push({ ...textBounds(run), kind: 'image', blockId: block.id });
    } else if (enclosure) {
      block.container = cellBounds(block, enclosure, rules);
    }
  }
  page.protectedRegions = protectedRegions;
}

// Preserve original gaps below paragraphs and the space left by wrapped text next to figures.
export function addLayoutAids(page: PdfPage) {
  const obstacles = [
    ...page.blocks.map(block => ({
      x: block.x, y: block.y, x1: block.x + block.width, y1: block.y + block.height,
      id: block.id, line: false,
    })),
    ...[...page.figures, ...page.images].map(box => ({ ...box, id: '', line: false })),
    ...page.shapes.map(box => ({ ...box, id: '', line: box.y1 - box.y < 2.5 })),
  ];
  for (const block of page.blocks) {
    const top = block.y - (block.lineHeight - 1) * block.fontSize / 2;
    const bottom = top + block.lineCount * block.lineHeight * block.fontSize;
    const right = block.x + block.width;
    block.inner = page.blocks.some(other => other.id !== block.id && boxContains({
      x: block.x, y: block.y, x1: right, y1: block.y + block.height,
    }, other));
    let room = page.height - bottom - block.fontSize * 0.5;
    for (const obstacle of obstacles) {
      if (obstacle.id === block.id || obstacle.x >= right || obstacle.x1 <= block.x ||
          (obstacle.line && obstacle.y < bottom + block.fontSize * 0.6) ||
          obstacle.y < bottom - block.fontSize * 0.2) continue;
      room = Math.min(room, obstacle.y - bottom - block.fontSize * 0.25);
    }
    block.room = Math.max(0, Math.min(room, block.fontSize * 6));
    const lines = [...block.lines].sort((a, b) => a[1] - b[1]);
    const narrow = lines.map(line => right - line[0] - line[2] > block.fontSize * 2);
    for (let first = 0; first < lines.length - 1; first++) {
      if (!narrow[first]) continue;
      let last = first;
      while (last + 1 < lines.length && narrow[last + 1]) last++;
      if (last === first && last < lines.length - 1) continue;
      const edge = Math.max(...lines.slice(first, last + 1).map(line => line[0] + line[2])) + block.fontSize * 0.4;
      if (edge < right - block.fontSize) {
        block.wrap = {
          x: edge, y: top + first * block.lineHeight * block.fontSize,
          y1: last === lines.length - 1 ? bottom + block.fontSize * 6 : top + (last + 1) * block.lineHeight * block.fontSize,
        };
      }
      break;
    }
  }
}
