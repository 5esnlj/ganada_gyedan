import fontUrl from './fonts/PretendardVariable.woff2?url';

export const previewFont = 'GanadaPdfSans';
let fontReady: Promise<void> | undefined;

export function loadPreviewFont(): Promise<void> {
  fontReady ??= new FontFace(previewFont, `url("${fontUrl}")`, { weight: '45 920' }).load()
    .then(face => { document.fonts.add(face); })
    .catch(() => { fontReady = undefined; });
  return fontReady;
}
