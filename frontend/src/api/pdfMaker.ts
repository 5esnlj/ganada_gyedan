import { config } from '../config';
import { ApiError, request, requestWithMeta } from './client';
import type { AreaCode, AreaResult, DiagnosticState, ListOut, MaterialOut, MaterialParagraph, PartResult, SourceOut } from './wire';
import type { ConversionPage } from '../pdf/types';

const classPath = (classId: string) => `/classes/${encodeURIComponent(classId)}`;
const materialPath = (materialId: string) => `/materials/${encodeURIComponent(materialId)}`;
const MAX_CONVERTED_PDF_BYTES = 20 * 1024 * 1024;

export type PdfSourceOut = Omit<SourceOut, 'unit'> & {
  unit: string;
  fileId: string | null;
  sourceText: string;
  sentences: { id: string; text: string }[];
};

export type StudentTarget = {
  id: string;
  displayName: string;
  nativeLanguage: string;
  diagnosticStatus: DiagnosticState;
  partResults: PartResult[];
  areaResults: AreaResult[];
};

export type Credential = {
  id: string;
  label: string;
  maskedSuffix: string;
  status: 'pending' | 'active' | 'failed' | 'revoked';
};

export type GenerationJob = {
  id: string;
  sourceId: string;
  studentMemberId: string;
  operation: 'convert';
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown';
  resultId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
};

export type MaterialContent = {
  paragraphs: MaterialParagraph[];
  translations: Record<string, string>[];
  omittedPages: number[];
  limitedSupportPages?: number[];
  hiddenBlockIds?: string[];
  studyNotes?: {
    blockId: string;
    text?: string | null;
    visual?: { kind: 'rel' | 'seq' | 'list'; title: string; center?: string | null; items: string[] } | null;
  }[];
};

export type StudentEdition = MaterialOut & {
  sourceId: string;
  studentMemberId: string;
  status: 'draft' | 'reviewed' | 'published' | 'withdrawn';
  version: number;
  revision: number;
  content: MaterialContent;
  hasPdf?: boolean;
  originalContent?: string | null;
};

const downloadUrl = (materialId: string) => `${config.API_BASE_URL.replace(/\/$/, '')}${materialPath(materialId)}/download`;

async function fetchPdf(url: string): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(url, { credentials: 'include' });
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'PDF 서버에 연결할 수 없습니다.');
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { code?: string; message?: string; requestId?: string };
    throw new ApiError(response.status, payload.code ?? 'HTTP_ERROR', payload.message ?? 'PDF를 불러오지 못했습니다.', payload.requestId);
  }
  return response.blob();
}

export const pdfMakerApi = {
  sources: (classId: string, subject: AreaCode) =>
    request<ListOut<PdfSourceOut>>(`${classPath(classId)}/sources?subject=${encodeURIComponent(subject)}`),
  uploadSource: (classId: string, file: File, title: string, unit: string) => {
    const body = new FormData();
    body.set('file', file);
    body.set('title', title);
    body.set('unit', unit);
    return request<PdfSourceOut>(`${classPath(classId)}/sources/pdf`, { method: 'POST', body });
  },
  sourcePdf: (fileId: string) => fetchPdf(`${config.API_BASE_URL.replace(/\/$/, '')}/files/${encodeURIComponent(fileId)}/download`),
  materialPdf: (materialId: string) => fetchPdf(downloadUrl(materialId)),
  students: (classId: string) => request<ListOut<StudentTarget>>(`${classPath(classId)}/students`),
  credentials: () => request<ListOut<Credential>>('/ai-credentials'),
  saveCredential: (apiKey: string) => request<Credential>('/ai-credentials', {
    method: 'POST', body: JSON.stringify({ apiKey }),
  }),
  generate: (
    classId: string, sourceId: string, studentMemberIds: string[], pages: ConversionPage[], requestId: string,
  ) => request<ListOut<GenerationJob>>(`${classPath(classId)}/generation-jobs`, {
    method: 'POST', headers: { 'Idempotency-Key': requestId },
    body: JSON.stringify({ sourceId, operation: 'convert', studentMemberIds, pages }),
  }),
  jobs: (classId: string) => request<ListOut<GenerationJob>>(`${classPath(classId)}/generation-jobs`),
  editions: (classId: string, subject?: AreaCode) =>
    request<ListOut<StudentEdition>>(`${classPath(classId)}/materials${subject ? `?subject=${encodeURIComponent(subject)}` : ''}`),
  edition: (materialId: string) => requestWithMeta<StudentEdition>(materialPath(materialId)),
  edit: (materialId: string, content: MaterialContent, version: string) =>
    requestWithMeta<StudentEdition>(materialPath(materialId), {
      method: 'PATCH', headers: { 'If-Match': version }, body: JSON.stringify({ content }),
    }),
  transition: (materialId: string, action: 'review' | 'publish', version: string) =>
    requestWithMeta<StudentEdition>(`${materialPath(materialId)}/${action}`, {
      method: 'POST', headers: { 'If-Match': version },
    }),
  publishPdf: (materialId: string, pdf: Blob, version: string) => {
    if (pdf.size > MAX_CONVERTED_PDF_BYTES) {
      throw new Error('배포용 PDF가 20 MiB를 넘습니다. 원본으로 돌아가 변환할 쪽 범위를 나눠 주세요.');
    }
    const body = new FormData();
    body.set('file', pdf, 'converted.pdf');
    return requestWithMeta<StudentEdition>(`${materialPath(materialId)}/publish`, {
      method: 'POST', headers: { 'If-Match': version }, body,
    });
  },
  downloadUrl,
};
