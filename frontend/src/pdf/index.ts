import type { ConversionPage, PdfPage, PdfSource } from './types';

export type { ConversionPage, ConvertedPdfContent, PdfBlock, PdfHelpContent, PdfPage, PdfPreview, PdfSource } from './types';
export { canEditPdfBlock, exportPreviewPdf, renderConvertedPages } from './render';

export async function openSourcePdf(file: Blob): Promise<PdfSource> {
  const source = await import('./source');
  return source.openSourcePdf(file);
}

export function toConversionPages(pages: readonly PdfPage[]): ConversionPage[] {
  return pages.map(page => ({
    number: page.number,
    blocks: page.fallback ? [] : page.blocks.map(({ id, text, preserve }) => ({ id, text, preserve })),
  }));
}
