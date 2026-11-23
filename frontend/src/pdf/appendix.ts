import { loadPreviewFont, previewFont } from './fonts';
import { createRaster, releaseRaster, wrapLine } from './raster';
import type { Raster } from './raster';
import type { PdfHelpContent, RenderedPage } from './types';

type AppendixPage = Omit<RenderedPage, 'number'>;

interface HelpRow {
  title: string;
  text: string;
}

interface HelpSection {
  heading: string;
  rows: HelpRow[];
}

function sectionsFrom(content: PdfHelpContent): HelpSection[] {
  const hidden = new Set(content.hiddenBlockIds);
  const visible = new Map(content.paragraphs.filter(item => !hidden.has(item.id)).map(item => [item.id, item.page]));
  const groups = new Map<number, HelpRow[]>();
  for (const note of content.studyNotes ?? []) {
    if (!visible.has(note.blockId)) continue;
    const page = visible.get(note.blockId) ?? 0;
    const rows = groups.get(page) ?? [];
    const text = note.text?.trim();
    if (text) rows.push({ title: '', text });
    const visual = note.visual;
    const items = visual?.items.map(item => item.trim()).filter(Boolean) ?? [];
    if (visual && items.length) {
      const title = visual.title.trim();
      const center = visual.center?.trim();
      const list = {
        seq: items.join(' → '),
        list: items.map(item => `• ${item}`).join('\n'),
        rel: [center, items.join(' · ')].filter(Boolean).join(' — '),
      }[visual.kind];
      rows.push({ title, text: list });
    }
    if (rows.length) groups.set(page, rows);
  }
  const sections = [...groups.entries()].sort(([a], [b]) => a - b)
    .map(([page, rows]) => ({ heading: page ? `원본 ${page}쪽 공부 도움` : '공부 도움', rows }));

  const terms = (content.translations ?? []).flatMap(entry => {
    const title = entry.term?.trim();
    const meaning = entry.meaning?.trim();
    const translation = entry.translation?.trim();
    const language = entry.language?.trim();
    if (!title || (!meaning && !translation)) return [];
    const translated = translation && (language ? `모국어 (${language})  ${translation}` : `모국어  ${translation}`);
    return [{ title, text: [meaning, translated].filter(Boolean).join('\n') }];
  });
  if (terms.length) sections.push({ heading: '핵심어와 모국어 뜻', rows: terms });
  return sections;
}

const width = 595.28;
const height = 841.89;
const scale = 2;
const margin = 48;
const contentWidth = width - 2 * margin;
const bottom = height - 52;
const ink = '#183133';

export async function renderHelpPages(content: PdfHelpContent): Promise<AppendixPage[]> {
  const sections = sectionsFrom(content);
  if (!sections.length) return [];
  await loadPreviewFont();

  const pages: AppendixPage[] = [];
  let raster: Raster | undefined;
  let y = 0;
  const drawLine = (line: string, size: number, weight: number, color = ink) => {
    const context = raster!.context;
    context.font = `${weight} ${size}px "${previewFont}", sans-serif`;
    context.fillStyle = color;
    context.fillText(line, margin, y);
  };
  const finishPage = () => {
    if (!raster) return;
    const canvas = raster.canvas;
    pages.push({ width, height, imageUrl: canvas.toDataURL('image/jpeg', 0.9) });
    releaseRaster(raster);
    raster = undefined;
  };
  const startPage = () => {
    finishPage();
    raster = createRaster(width * scale, height * scale);
    const context = raster.context;
    context.scale(scale, scale);
    context.fillStyle = '#fff';
    context.fillRect(0, 0, width, height);
    y = 63;
    drawLine('자료 읽기 도움말', 23, 700);
    context.fillStyle = '#d6e2e0';
    context.fillRect(margin, 82, contentWidth, 1);
    y = 111;
  };
  const ensureRoom = (size: number) => {
    if (y + size > bottom) startPage();
  };
  const drawText = (text: string, size: number, weight: number, pitch: number, color?: string) => {
    for (const line of text.split('\n')) {
      let remaining = line.trim();
      do {
        ensureRoom(pitch);
        const context = raster!.context;
        context.font = `${weight} ${size}px "${previewFont}", sans-serif`;
        const [part, rest] = wrapLine(context, remaining, contentWidth);
        if (!part && remaining) throw new Error('보조 설명의 글자를 PDF에 배치할 수 없습니다.');
        drawLine(part, size, weight, color);
        y += pitch;
        remaining = rest;
      } while (remaining);
    }
  };

  try {
    startPage();
    for (const section of sections) {
      ensureRoom(64);
      drawText(section.heading, 16, 700, 26, '#245d58');
      for (const row of section.rows) {
        ensureRoom(row.title ? 40 : 19);
        if (row.title) drawText(row.title, 13, 700, 21);
        drawText(row.text, 12, 400, 19);
        y += 9;
      }
      y += 12;
    }
    finishPage();
    return pages;
  } finally {
    if (raster) releaseRaster(raster);
  }
}
