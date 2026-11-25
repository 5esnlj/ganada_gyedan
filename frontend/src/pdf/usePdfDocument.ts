import { useEffect, useState } from 'react';
import { openSourcePdf } from './index';
import type { PdfSource } from './types';

export function usePdfDocument(id: string | null, load: (id: string) => Promise<Blob>) {
  const [pdf, setPdf] = useState<PdfSource | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let mounted = true;
    let opened: PdfSource | null = null;
    setPdf(null);
    setError(null);
    if (id) {
      void load(id).then(openSourcePdf).then((document) => {
        if (!mounted) return document.close();
        opened = document;
        setPdf(document);
      }).catch((cause: unknown) => {
        if (mounted) setError(cause instanceof Error ? cause.message : 'PDF를 불러오지 못했습니다.');
      });
    }
    return () => {
      mounted = false;
      if (opened) void opened.close().catch(() => undefined);
    };
  }, [id, load, revision]);

  return { pdf, error, retry: () => setRevision((value) => value + 1) };
}
