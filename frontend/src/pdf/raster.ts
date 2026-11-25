import type { Box, Rgb, TextRect } from './types';

export interface Raster {
  canvas: HTMLCanvasElement;
  context: CanvasRenderingContext2D;
}

export function createRaster(width: number, height: number): Raster {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('지면 그림을 만들지 못했습니다.');
  return { canvas, context };
}

export function releaseRaster(raster: Raster) {
  raster.canvas.width = 0;
  raster.canvas.height = 0;
}

export async function imageRaster(url: string): Promise<Raster> {
  const image = new Image();
  image.src = url;
  await image.decode();
  const raster = createRaster(image.naturalWidth, image.naturalHeight);
  raster.context.drawImage(image, 0, 0);
  return raster;
}

export function rgb(color: Rgb): string {
  return `rgb(${color.join(',')})`;
}

// Quantized dominant color along the perimeter of the text rectangle.
export function ringMode(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  offset: number,
): { color: Rgb; share: number } | null {
  const counts = new Map<number, number>();
  let best = -1;
  let most = 0;
  let total = 0;
  const take = (x: number, y: number) => {
    if (x < 0 || y < 0 || x >= width || y >= height) return;
    const i = (y * width + x) * 4;
    const key = ((pixels[i] >> 3) << 10) | ((pixels[i + 1] >> 3) << 5) | (pixels[i + 2] >> 3);
    const count = (counts.get(key) ?? 0) + 1;
    counts.set(key, count);
    total++;
    if (count > most) { most = count; best = i; }
  };
  const step = Math.max(1, Math.round((x1 - x0 + y1 - y0) / 400));
  for (let x = x0; x <= x1; x += step) {
    take(x, y0 - offset);
    take(x, y1 + offset);
  }
  for (let y = y0; y <= y1; y += step) {
    take(x0 - offset, y);
    take(x1 + offset, y);
  }
  return best === -1 ? null : {
    color: [pixels[best], pixels[best + 1], pixels[best + 2]], share: most / total,
  };
}

export interface Rectangle {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export function pixelRectangle(box: Box, scale: number, width: number, height: number, bleed = 0): Rectangle | null {
  const x0 = Math.max(0, Math.floor(box.x * scale - bleed));
  const x1 = Math.min(width - 1, Math.ceil(box.x1 * scale + bleed));
  const y0 = Math.max(0, Math.floor(box.y * scale - bleed));
  const y1 = Math.min(height - 1, Math.ceil(box.y1 * scale + bleed));
  return x1 >= x0 && y1 >= y0 ? { x0, y0, x1, y1 } : null;
}

export function textRectangle(
  rect: TextRect,
  scale: number,
  width: number,
  height: number,
  padding: number,
): Rectangle | null {
  const [x, y, w, size] = rect;
  const pad = size * padding;
  return pixelRectangle({
    x: x - pad, y: y - size * 0.06 - pad,
    x1: x + w + pad, y1: y + size * 1.08 + pad,
  }, scale, width, height);
}

export function inkColor(source: Raster, masked: Raster, scale: number, rectangle: TextRect): Rgb | null {
  const rect = textRectangle(rectangle, scale, source.canvas.width, source.canvas.height, 0);
  if (!rect) return null;
  const { x0, y0, x1, y1 } = rect;
  const width = x1 - x0 + 1;
  const height = y1 - y0 + 1;
  const original = source.context.getImageData(x0, y0, width, height).data;
  const background = masked.context.getImageData(x0, y0, width, height).data;
  const difference = (i: number) =>
    Math.abs(original[i] - background[i]) +
    Math.abs(original[i + 1] - background[i + 1]) +
    Math.abs(original[i + 2] - background[i + 2]);
  let maximum = 0;
  for (let i = 0; i < original.length; i += 4) maximum = Math.max(maximum, difference(i));

  let count = 0;
  const sum: Rgb = [0, 0, 0];
  for (let i = 0; i < original.length; i += 4) {
    const delta = difference(i);
    if (delta <= 150 || delta < maximum * 0.85) continue;
    for (let channel = 0; channel < 3; channel++) sum[channel] += original[i + channel];
    count++;
  }
  return count < 3 ? null : [
    Math.round(sum[0] / count), Math.round(sum[1] / count), Math.round(sum[2] / count),
  ];
}

export function wrapLine(context: CanvasRenderingContext2D, text: string, width: number): [string, string] {
  if (width <= 0) return ['', text];
  if (context.measureText(text).width <= width) return [text, ''];
  const chars = Array.from(text);
  let low = 0;
  let high = chars.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (context.measureText(chars.slice(0, middle).join('')).width <= width) low = middle;
    else high = middle - 1;
  }
  if (!low) return ['', text];
  const prefix = chars.slice(0, low).join('');
  const separator = prefix.lastIndexOf(' ');
  const count = separator > 0 ? Array.from(prefix.slice(0, separator)).length : low;
  return [chars.slice(0, count).join('').trimEnd(), chars.slice(count).join('').trimStart()];
}
