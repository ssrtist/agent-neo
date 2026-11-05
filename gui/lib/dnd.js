// Drag-and-drop of links / paths into the composer: a context item travels as JSON under CTX_MIME
// plus a text/plain fallback; dropped items become chips that expand to plain text when the prompt is sent.
export const CTX_MIME = 'application/x-neo-ctx';

/** Plain-text form of a context item (Shift+drop, and how chips are expanded on send). */
export function plainRef(c) {
  if (c.ref) return c.ref;
  switch (c.kind) {
    case 'file': return '@' + c.value;
    case 'filetext': return `File ${c.label}:\n\`\`\`\n${c.value}\n\`\`\``;
    case 'code': return c.value;
    default: return c.value;
  }
}

export function setDrag(e, c) {
  if (!e.dataTransfer) return;
  e.dataTransfer.setData(CTX_MIME, JSON.stringify(c));
  e.dataTransfer.setData('text/plain', plainRef(c));
  if (c.kind === 'url') e.dataTransfer.setData('text/uri-list', c.value);
  e.dataTransfer.effectAllowed = 'copy';
}

export function getDrag(dt) {
  const raw = dt?.getData(CTX_MIME);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

/** The context item for a link element (data-art="kind|value"). */
export function ctxForLink(a) {
  const art = a.getAttribute('data-art') || '';
  const i = art.indexOf('|');
  if (i < 0) return null;
  const kind = art.slice(0, i), value = art.slice(i + 1);
  const label = kind === 'url' ? value.replace(/^https?:\/\//, '').slice(0, 60) : value.split('\\').pop() || value;
  return { kind, value, label };
}

/** The prompt text sent for a draft: the typed text, then one line per chip ("Context items" block). */
export function expandDraft(text, chips) {
  const t = (text ?? '').trim();
  if (!chips.length) return t;
  return (t ? t + '\n\n' : '') + '---\nContext items attached by the user:\n' + chips.map((c) => '- ' + plainRef(c)).join('\n');
}
