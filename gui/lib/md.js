// Markdown rendering + link detection: marked (GFM) + DOMPurify + highlight.js, the parked-slot trick for coloured
// code (sanitised once by the strict span-only pass), file-link rewriting, the code-block toolbar and the artifact
// linkifier (url and file artifact kinds).
import { marked } from '../vendor/marked/marked.esm.js';
import DOMPurify from '../vendor/dompurify/purify.es.mjs';
import { codeBlockHtml } from './highlight.js';
import { rewriteFileLinks, PATH_RE, TRAIL_RE, CODE_PATH_RE, joinRel, relTarget } from './paths.js';

marked.setOptions({ gfm: true, breaks: false });
let slots = null;
let nonce = '';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
marked.use({
  renderer: {
    code: (t) => codeBlockHtml(t.text, t.lang, !!t.escaped,
      slots ? (h) => { slots.push(h); return `<span data-hl-slot="${nonce}-${slots.length - 1}"></span>`; } : undefined),
    // never load remote images (a zero-click beacon could carry data out): show them as an ordinary link instead
    image: (t) => `<a href="${esc(t.href)}">${esc(t.text || 'image')} (image)</a>`,
  },
});
function newNonce() {
  const b = new Uint8Array(12);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

// Model HTML must not borrow the app's own classes (code review r2: class="modal" gave an answer a fixed full-window
// overlay). Only code-highlighting classes survive; linkify() adds its art-* classes after sanitising.
const SAFE_CLASS = /^(language-[\w+#.-]+|hljs(-[\w-]+)?)$/;
DOMPurify.addHook('uponSanitizeAttribute', (_node, data) => {
  if (data.attrName !== 'class') return;
  const keep = String(data.attrValue || '').split(/\s+/).filter((c) => SAFE_CLASS.test(c));
  if (keep.length) data.attrValue = keep.join(' ');
  else data.keepAttr = false;
});

/** Markdown -> sanitised HTML. */
export function renderMd(md) {
  const src = rewriteFileLinks(md ?? '');
  // Model output is untrusted (code review r1): no raw <style>/<img>/media/forms/frames, no style= attributes (they could
  // restyle or hide the approval cards), no data-art (only linkify() sets it, after sanitising), and no id/name (r3:
  // app.js finds its own controls by id; an answer's <span id="stop"> would capture the Stop button's lookups), and no
  // <label for=…> (r4: clicking such a label clicks the app button it names, e.g. yolo).
  const opts = {
    ADD_ATTR: ['target'],
    FORBID_TAGS: ['style', 'img', 'svg', 'math', 'form', 'input', 'button', 'textarea', 'select', 'iframe', 'frame',
      'object', 'embed', 'video', 'audio', 'source', 'picture', 'link', 'meta', 'base', 'dialog',
      'label', 'output'],
    FORBID_ATTR: ['style', 'data-art', 'srcset', 'background', 'poster', 'id', 'name', 'for'],
  };
  const parked = [];
  let html;
  slots = parked; nonce = newNonce();
  try { html = marked.parse(src, { async: false }); } finally { slots = null; }
  if (!parked.length) return DOMPurify.sanitize(html, opts);
  let used = 0;
  const out = DOMPurify.sanitize(html, opts).replace(new RegExp(`<span data-hl-slot="${nonce}-(\\d+)"></span>`, 'g'), (_m, i) => {
    used++;
    return parked[Number(i)] ?? '';
  });
  if (used === parked.length) return out;
  return DOMPurify.sanitize(marked.parse(src, { async: false }), opts);
}

/** The real target of an artifact link, so the href is never "#". */
export function artHref(kind, value) {
  if (kind === 'url') return value;
  if (kind === 'file') return 'file:///' + encodeURI(value.replace(/\\/g, '/')).replace(/#/g, '%23').replace(/\?/g, '%3F');
  return '#';
}

const URL_RE = /https?:\/\/[^\s<>"'`\]\)]+/;

function makeFileLink(a, path) {
  a.setAttribute('href', artHref('file', path));
  a.setAttribute('data-art', 'file|' + path);
  a.classList.add('art-link', 'art-file');
  a.draggable = true;
}

/** Fenced form of a rendered code block (for drag / insert into the prompt). */
export function codeOf(el) {
  const pre = el.closest('.codewrap')?.querySelector('pre');
  const code = pre?.querySelector('code');
  const lang = /language-(\S+)/.exec(code?.className ?? '')?.[1] ?? '';
  return '```' + lang + '\n' + (pre?.textContent ?? '').replace(/\n$/, '') + '\n```';
}

/** Turn URLs and Windows paths in rendered text into artifact links (data-art="kind|value"); wrap code blocks with a
 *  toolbar (copy, drag, → prompt). `workspace` resolves relative Markdown links. */
export function linkify(node, workspace = '') {
  node.querySelectorAll('pre').forEach((pre) => {
    if (pre.parentElement?.classList.contains('codewrap')) return;
    const wrap = document.createElement('div');
    wrap.className = 'codewrap';
    pre.replaceWith(wrap);
    wrap.append(pre);
    const tools = document.createElement('div');
    tools.className = 'codetools';
    tools.innerHTML = '<button type="button" data-code="copy" title="Copy this block">copy</button>' +
      '<span class="grip" draggable="true" data-code="drag" title="Drag into the prompt">⠿ drag</span>' +
      '<button type="button" data-code="insert" title="Insert this block into the prompt">→ prompt</button>';
    wrap.append(tools);
  });
  node.querySelectorAll('a[href]').forEach((a) => {
    if (a.dataset.art) return;
    const href = a.getAttribute('href') ?? '';
    if (/^(https?|mailto):/i.test(href)) { a.setAttribute('data-art', 'url|' + href); a.classList.add('art-link', 'art-url'); }
    else if (href.startsWith('#neo-file:')) {
      let p = href.slice(10);
      try { p = decodeURIComponent(p); } catch { /* keep */ }
      makeFileLink(a, p);
    } else if (relTarget(href) && workspace) makeFileLink(a, joinRel(workspace, relTarget(href)));
    else if (relTarget(href)) a.replaceWith(...a.childNodes);
  });
  node.querySelectorAll('code').forEach((c) => {
    if (c.closest('pre, a') || c.querySelector('a')) return;
    const t = (c.textContent ?? '').trim();
    if (CODE_PATH_RE.test(t)) {
      const a = document.createElement('a');
      a.textContent = c.textContent ?? '';
      makeFileLink(a, t);
      c.replaceChildren(a);
    }
  });
  const combined = new RegExp(`(${URL_RE.source})|(${PATH_RE.source})`, 'g');
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => n.nodeType === 3 ? NodeFilter.FILTER_ACCEPT
      : n.matches('a, pre') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP,
  });
  const texts = [];
  while (walker.nextNode()) texts.push(walker.currentNode);
  for (const t of texts) {
    const s = t.nodeValue ?? '';
    combined.lastIndex = 0;
    if (!combined.test(s)) continue;
    combined.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of s.matchAll(combined)) {
      const idx = m.index ?? 0;
      const kind = m[1] ? 'url' : 'file';
      const val = m[0].replace(TRAIL_RE, '');
      if (kind === 'file' && val.length <= 3) continue;
      frag.append(s.slice(last, idx));
      const a = document.createElement('a');
      a.textContent = val;
      a.setAttribute('href', artHref(kind, val));
      a.className = 'art-link art-' + kind;
      a.setAttribute('data-art', kind + '|' + val);
      frag.append(a);
      last = idx + val.length;
    }
    frag.append(s.slice(last));
    t.replaceWith(frag);
  }
  node.querySelectorAll('a[data-art]').forEach((a) => { a.draggable = true; });
}
