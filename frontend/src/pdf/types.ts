import type { MaterialParagraph, PhraseChange } from '../api/wire';

export interface Box {
  x: number;
  y: number;
  x1: number;
  y1: number;
}

export type Rgb = [number, number, number];
export type FontFamily = 'sans' | 'serif' | 'hand' | 'round';
export type TextRect = [x: number, y: number, width: number, fontSize: number];

export interface PdfBlock {
  id: string;
  text: string;
  preserve: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  fontSize: number;
  lineHeight: number;
  lineCount: number;
  family: FontFamily;
  weight: number;
  indent: number;
  color: string;
  runs: TextRect[];
  lines: TextRect[];
  room: number;
  inner: boolean;
  wrap?: { x: number; y: number; y1: number };
}

export interface RenderedPage {
  number: number;
  width: number;
  height: number;
  imageUrl: string;
}

export interface PdfPage extends RenderedPage {
  scale: number;
  blocks: PdfBlock[];
  figures: Box[];
  images: Box[];
  shapes: Box[];
  boxes: Box[];
  fallback?: string;
}

export interface ConversionPage {
  number: number;
  blocks: { id: string; text: string; preserve: boolean }[];
}

export interface ConvertedPdfContent {
  paragraphs: readonly MaterialParagraph[];
  omittedPages?: readonly number[];
  limitedSupportPages?: readonly number[];
  hiddenBlockIds?: readonly string[];
}

export interface PdfPhraseResult {
  blockId: string;
  page: number;
  change: PhraseChange;
  applied: boolean;
}

export interface PdfPreview {
  pages: RenderedPage[];
  notices: string[];
  unplacedBlockIds: string[];
  phrases: PdfPhraseResult[];
}

export interface PdfSource {
  pageCount: number;
  readPages(
    from: number,
    to: number,
    onProgress?: (completed: number, total: number) => void,
  ): Promise<PdfPage[]>;
  close(): Promise<void>;
}

export interface PdfHelpContent {
  paragraphs: readonly { id: string; page?: number | null }[];
  translations?: readonly Record<string, string>[];
  hiddenBlockIds?: readonly string[];
  studyNotes?: readonly {
    blockId: string;
    text?: string | null;
    visual?: {
      kind: 'rel' | 'seq' | 'list';
      title: string;
      center?: string | null;
      items: readonly string[];
    } | null;
  }[];
}
