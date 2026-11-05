// Path / file-link rules for Markdown and plain text; pure, no imports.

/** The path a Markdown link target names, for `file:///C:/...` and `C:\...` targets; '' otherwise. */
export function fileTargetPath(t) {
  const s = (t ?? '').trim().replace(/^<(.*)>$/, '$1');
  if (/^file:/i.test(s)) {
    let p = s.replace(/^file:\/*/i, '');
    try { p = decodeURIComponent(p); } catch { /* keep the raw text */ }
    p = p.replace(/\//g, '\\');
    return /^[A-Za-z]:\\/.test(p) ? p : '';
  }
  return /^[A-Za-z]:[\\/]/.test(s) ? s.replace(/\//g, '\\') : '';
}

/** `](file:///...)` and `](C:\...)` become `](#neo-file:<encoded path>)` before marked runs, so DOMPurify's allow-list
 *  stays strict and marked never eats a backslash. Fenced blocks and inline code are left alone. */
export function rewriteFileLinks(src) {
  return src.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/).map((part, i) => (i % 2 ? part
    : protectRawPaths(part.replace(/\]\(\s*(<[^>\n]+>|file:[^)\s]+|[A-Za-z]:[\\/][^)\n]*?)\s*\)/g, (m, t) => {
      const p = fileTargetPath(t);
      return p ? `](#neo-file:${encodeURIComponent(p)})` : m;
    })))).join('');
}

/** Markdown-escape a link label so marked prints it verbatim (every backslash survives). */
export const mdEscapeLabel = (s) => s.replace(/[\\`*_[\]<>~|!#]/g, '\\$&');

/** Plan v0.5 D1: a raw `C:\...` path in prose becomes an explicit file link before marked runs, so `\_`, `\*`, `\\`
 *  and friends are not eaten as Markdown escapes (C:\Users\alice.smith\_neo-home keeps its `\`). Existing
 *  Markdown links (label and target) and autolinks are left alone. */
export function protectRawPaths(text) {
  return text.split(/(!?\[[^\]\n]*\]\([^)\n]*\)|<[^>\n]*>)/).map((seg, j) => (j % 2 ? seg
    : seg.replace(new RegExp(PATH_RE.source, 'g'), (m, off, whole) => {
      if (off > 0 && /[\w\\/]/.test(whole[off - 1])) return m;   // part of a URL or a longer token
      const p = m.replace(TRAIL_RE, '');
      if (p.length <= 3) return m;
      return `[${mdEscapeLabel(p)}](#neo-file:${encodeURIComponent(p)})${m.slice(p.length)}`;
    }))).join('');
}

// The WHOLE path, with or without an extension or a trailing `\`; it stops at whitespace, quotes, backticks and brackets,
// and trailing sentence punctuation is trimmed after the match (TRAIL_RE).
export const PATH_RE = /\b[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n\s`'()\[\]{}]+\\)*[^\\/:*?"<>|\r\n\s`'()\[\]{}]*/;
export const TRAIL_RE = /[.,;:!?]+$/;
/** An inline-code span whose whole text is an absolute path (spaces allowed) is linked as a whole. */
export const CODE_PATH_RE = /^[A-Za-z]:\\[^\r\n*?"<>|]*$/;

/** Paths in plain text: every match, trailing punctuation trimmed. */
export function findPaths(s) {
  return [...s.matchAll(new RegExp(PATH_RE.source, 'g'))].map((m) => m[0].replace(TRAIL_RE, '')).filter((p) => p.length > 3);
}

/** Join a relative link target to a folder (Windows separators; `./` dropped). */
export function joinRel(cwd, rel) {
  const r = rel.replace(/\//g, '\\').replace(/^(\.\\)+/, '');
  return cwd.replace(/[\\/]+$/, '') + '\\' + r;
}

/** A relative Markdown link target (no scheme, no `#`, no leading slash), decoded; '' for anything else. */
export function relTarget(href) {
  if (!href || href.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(href) || /^[\\/]/.test(href)) return '';
  let h = href.split('#')[0].split('?')[0];
  try { h = decodeURIComponent(h); } catch { /* keep */ }
  return /[<>"|*]/.test(h) || !h.trim() ? '' : h;
}
