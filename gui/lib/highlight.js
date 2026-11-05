// Syntax highlighting for chat code blocks: fence-tag map, evidence-based auto-detect ("plain if unsure"),
// size limits and a strict span-only DOMPurify pass. Uses the grammars vendored in gui/vendor/highlight;
// code over HL_SYNC_MAX stays plain text.
import hljs from '../vendor/highlight/core.min.js';
import powershell from '../vendor/highlight/languages/powershell.min.js';
import bash from '../vendor/highlight/languages/bash.min.js';
import dos from '../vendor/highlight/languages/dos.min.js';
import python from '../vendor/highlight/languages/python.min.js';
import json from '../vendor/highlight/languages/json.min.js';
import yaml from '../vendor/highlight/languages/yaml.min.js';
import xml from '../vendor/highlight/languages/xml.min.js';
import javascript from '../vendor/highlight/languages/javascript.min.js';
import typescript from '../vendor/highlight/languages/typescript.min.js';
import csharp from '../vendor/highlight/languages/csharp.min.js';
import css from '../vendor/highlight/languages/css.min.js';
import sql from '../vendor/highlight/languages/sql.min.js';
import ini from '../vendor/highlight/languages/ini.min.js';
import diff from '../vendor/highlight/languages/diff.min.js';
import markdown from '../vendor/highlight/languages/markdown.min.js';
import DOMPurify from '../vendor/dompurify/purify.es.mjs';

const GRAMMARS = { powershell, bash, dos, python, json, yaml, xml, javascript, typescript, csharp, css, sql, ini, diff, markdown };
for (const [name, fn] of Object.entries(GRAMMARS)) hljs.registerLanguage(name, fn);

export const LANGS = [
  { grammar: 'powershell', tags: ['powershell', 'pwsh', 'ps1', 'ps', 'posh'] },
  { grammar: 'bash', tags: ['bash', 'sh', 'shell', 'zsh'] },
  { grammar: 'dos', tags: ['bat', 'batch', 'cmd', 'dos'] },
  { grammar: 'csharp', tags: ['cs', 'csharp', 'c#'] },
  { grammar: 'css', tags: ['css'] },
  { grammar: 'xml', tags: ['html', 'xml', 'xaml', 'svelte', 'svg'] },
  { grammar: 'javascript', tags: ['js', 'javascript', 'jsx', 'mjs'] },
  { grammar: 'typescript', tags: ['ts', 'typescript', 'tsx'] },
  { grammar: 'python', tags: ['py', 'python', 'python3'] },
  { grammar: 'sql', tags: ['sql', 'tsql'] },
  { grammar: 'yaml', tags: ['yaml', 'yml', 'sls'] },
  { grammar: 'json', tags: ['json', 'jsonc', 'jsonl'] },
  { grammar: 'markdown', tags: ['markdown', 'md'] },
  { grammar: 'diff', tags: ['diff', 'patch'] },
  { grammar: 'ini', tags: ['ini', 'toml', 'cfg', 'conf'] },
];
const BY_TAG = new Map();
for (const l of LANGS) for (const t of l.tags) BY_TAG.set(t, l.grammar);
export const PLAIN_TAGS = new Set(['text', 'txt', 'plain', 'plaintext', 'log', 'output', 'console']);
export const AUTO_MIN_RELEVANCE = 1;
export const HL_SYNC_MAX = 6000;
export const HL_MAX_LINES = 5000;

/** What a fence tag means: a grammar, 'plain' (never coloured) or 'auto' (untagged / unknown: detect, else plain). */
export function grammarForTag(tag) {
  const t = (tag ?? '').trim().toLowerCase();
  if (!t) return 'auto';
  if (PLAIN_TAGS.has(t)) return 'plain';
  return BY_TAG.get(t) ?? 'auto';
}

// The highlighted HTML passes its own DOMPurify instance that keeps only <span class="hljs-…">.
const HL_CLASS = /^(?:hljs-[a-z][a-z0-9_-]*|[a-z]+_+|language-[a-z0-9]+)$/;
let strict = null;
function strictPurify() {
  if (strict) return strict;
  strict = DOMPurify(window);
  strict.addHook('uponSanitizeAttribute', (_node, data) => {
    if (data.attrName !== 'class') return;
    const keep = String(data.attrValue).split(/\s+/).filter((c) => HL_CLASS.test(c));
    data.attrValue = keep.join(' ');
    if (!keep.length) data.keepAttr = false;
  });
  return strict;
}
export function sanitizeHl(html) {
  return strictPurify().sanitize(html, { ALLOWED_TAGS: ['span'], ALLOWED_ATTR: ['class'], ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false });
}

const cache = new Map();
const PLAIN = { html: null, lang: '', auto: false };

export function highlightCode(code, grammar) {
  if (grammar === 'plain' || !code) return PLAIN;
  if (code.length > HL_SYNC_MAX || code.split('\n').length > HL_MAX_LINES) return PLAIN;
  const key = grammar + '\u0000' + code;
  const hit = cache.get(key);
  if (hit) return hit;
  let out = PLAIN;
  if (grammar === 'auto') {
    const r = detect(code);
    if (r) out = { html: sanitizeHl(r.value), lang: r.language, auto: true };
  } else if (hljs.getLanguage(grammar)) {
    out = { html: sanitizeHl(hljs.highlight(code, { language: grammar, ignoreIllegals: true }).value), lang: grammar, auto: false };
  }
  if (cache.size > 500) cache.delete(cache.keys().next().value);
  cache.set(key, out);
  return out;
}

// ---- auto-detect with evidence (most untagged blocks are not code)
export const AUTO_SUBSET = ['powershell', 'bash', 'python', 'json', 'yaml', 'xml'];
const NOT_CODE = /[\u2500-\u257F\u2190-\u21FF\u25B6\u25BA\u25C0\u2713\u2714\u2717\u2718\u2705\u274C]/;
const DIFFSTAT = /\|\s+\d+\s+[+-]+\s*$|^[ \t]*\d+ files? changed\b/m;
const PS_VERBS = 'Add|Approve|Assert|Backup|Block|Build|Checkpoint|Clear|Close|Compare|Complete|Compress|Confirm|Connect|Convert|ConvertFrom|ConvertTo|Copy|Debug|Deny|Deploy|Disable|Disconnect|Dismount|Edit|Enable|Enter|Exit|Expand|Export|Find|ForEach|Format|Get|Grant|Group|Hide|Import|Initialize|Install|Invoke|Join|Limit|Lock|Measure|Merge|Mount|Move|New|Open|Optimize|Out|Ping|Pop|Protect|Publish|Push|Read|Receive|Register|Remove|Rename|Repair|Request|Reset|Resize|Resolve|Restart|Restore|Resume|Revoke|Save|Search|Select|Send|Set|Show|Skip|Sort|Split|Start|Step|Stop|Submit|Suspend|Switch|Sync|Tee|Test|Trace|Unblock|Uninstall|Unlock|Unprotect|Unpublish|Unregister|Update|Use|Wait|Watch|Where|Write';
const PS_STRONG = [
  /\$[A-Za-z_][\w:]*\s*[+\-*/]?=[^=]/, /\$env:\w+/i, /\$_\b|\$PSScriptRoot\b|\$PSVersionTable\b|\$ErrorActionPreference\b/,
  /(\$[\w.:]+|\))\s+-(eq|ne|gt|ge|lt|le|like|notlike|match|notmatch|contains|notcontains|in|notin|and|or|replace)\s/i,
  /\[[\w.]+(\[\])?\]\s*\$\w/, /\[[\w.]+\]::\w+/, /@\{|@\(/, /^[ \t]*param\s*\(/im,
  new RegExp(`(^|[|;(=&]\\s*)(${PS_VERBS})-[A-Z][A-Za-z]+\\s+-[A-Za-z]`, 'm'),
  new RegExp(`\\|\\s*(${PS_VERBS})-[A-Z][A-Za-z]+\\b`),
  /^[ \t]*(&\s*)?\.\\[\w.\\-]+\.ps1\b/m, /^[ \t]*(&\s*)?[\w.:\\/-]+\.ps1 [^\s#:\d|]/m,
  /(^|\s)(pwsh|powershell)(\.exe)?\s+-[A-Za-z]/im, /(`\s*\n[^\n]*){2}/,
];
const PS_WEAK = [new RegExp(`(^|[|;(=&]\\s*)(${PS_VERBS})-[A-Z][A-Za-z]+([ \\t]+[^\\s#:>-]|[ \\t]*$)`, 'm'), /`\s*$/m, /\$[A-Za-z_]\w*/];
const BASH_STRONG = [/^#!.*\b(ba|z|da)?sh\b/, /^[ \t]*(then|fi|done|esac|elif\b.*)\s*$/m, /\$\([^()\n]+\)/, /\$\{\w+/, / \\\s*$/m, /\|\s*(grep|sed|awk|xargs|sort|uniq|head|tail|wc|tee)\b/];
const BASH_CMD = /^[ \t]*(export|source|sudo|apt(-get)?|yum|dnf|chmod|chown|ln|mkdir|rm|echo|grep|sed|awk|curl|wget|tar|ssh|scp|ls|cat|cd|docker|kubectl|git|npm|npx|pip3?|python3?|make|bash|sh|systemctl|journalctl)\s+[^\s:=]/;
const PY_STRONG = [/^[ \t]*(async[ \t]+)?def[ \t]+\w+[ \t]*\([^\n]*:[ \t]*$/m, /^[ \t]*class\s+\w+(\(.*\))?:\s*$/m, /^[ \t]*import\s+[\w.]+(\s+as\s+\w+)?\s*$/m, /^[ \t]*from\s+[\w.]+\s+import\s+/m, /__name__\s*==\s*['"]__main__['"]/];
const PY_WEAK = [/\bprint\(/, /^[ \t]*(if|elif|for|while|with|try|except|else)\b[^\n]*:\s*$/m, /\bself\.\w+/, /^[ \t]*return\b/m, /\bNone\b|\bTrue\b|\bFalse\b/];
const YAML_LINE = /^\s*(#.*|-\s.*|-|[\w.\-/"']+\s*:(\s.*)?|---|\.\.\.)$/;
const count = (res, s) => res.reduce((n, r) => n + (r.test(s) ? 1 : 0), 0);

export function candidates(code) {
  const s = code.trim();
  if (!s) return [];
  if (isJson(s)) return ['json'];
  if (isMarkup(s)) return ['xml'];
  if (NOT_CODE.test(s) || DIFFSTAT.test(s) || isDiff(s)) return [];
  const out = [];
  if (2 * count(PS_STRONG, s) + count(PS_WEAK, s) >= 2) out.push('powershell');
  if (count(BASH_STRONG, s) >= 1 || allCommandLines(s)) out.push('bash');
  if (count(PY_STRONG, s) >= 1 || count(PY_WEAK, s) >= 3) out.push('python');
  if (isYaml(s)) out.push('yaml');
  return out;
}
export function detect(code, min = AUTO_MIN_RELEVANCE) {
  const c = candidates(code);
  if (!c.length) return null;
  if (c.length === 1 && (c[0] === 'json' || c[0] === 'xml')) {
    const v = hljs.highlight(code, { language: c[0], ignoreIllegals: true });
    return { language: c[0], relevance: v.relevance, value: v.value };
  }
  const r = hljs.highlightAuto(code, c);
  if (!r.language || r.relevance < min) return null;
  return { language: r.language, relevance: r.relevance, value: r.value };
}
function isDiff(s) {
  const lines = s.split('\n').filter((l) => l.trim());
  const plus = lines.filter((l) => /^\+(?!\+)/.test(l) || /^@@ /.test(l)).length;
  return plus >= 2 && plus >= 0.3 * lines.length;
}
function allCommandLines(s) {
  const lines = s.split('\n');
  let any = false;
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (!l.trim() || /^\s*#/.test(l)) continue;
    if (BASH_CMD.test(l)) { any = true; continue; }
    if (i > 0 && (/\\\s*$/.test(lines[i - 1]) || /^\s+--?\w/.test(l))) continue;
    return false;
  }
  return any;
}
export function isJson(s) {
  if (!/^[[{]/.test(s) || !/[\]}]$/.test(s)) return false;
  const ok = (t) => { try { const v = JSON.parse(t); return v !== null && typeof v === 'object'; } catch { return false; } };
  if (ok(s)) return true;
  const lines = s.split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length > 1 && lines.every((l) => /^[[{]/.test(l) && ok(l));
}
export function isMarkup(s) {
  return /^<([A-Za-z][\w:.-]*[\s>/]|\?xml|!--|!DOCTYPE)/i.test(s) && s.endsWith('>') && /<\/[A-Za-z][\w:.-]*\s*>|\/>|-->|\?>/.test(s);
}
export function isYaml(s) {
  const lines = s.split('\n').filter((l) => l.trim());
  if (lines.length < 3) return false;
  let shaped = 0, nested = false;
  for (let i = 0; i < lines.length; i++) {
    if (YAML_LINE.test(lines[i])) shaped++;
    const m = /^(\s*)(-\s+)?[\w.\-/"']+\s*:\s*$/.exec(lines[i]);
    if (m && i + 1 < lines.length && /^\s*/.exec(lines[i + 1])[0].length > m[1].length) nested = true;
  }
  return nested && shaped === lines.length;
}

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** The HTML for one fenced code block (marked's `code` renderer), same `<pre><code class="language-x">` shape as marked's. */
export function codeBlockHtml(text, infoLang, escaped = false, park) {
  const tag = (infoLang || '').match(/^\S*/)?.[0] ?? '';
  const body = text ? text.replace(/\n$/, '') + '\n' : '';
  const cls = tag ? 'language-' + esc(tag) : '';
  const hl = escaped ? PLAIN : highlightCode(body.replace(/\n$/, ''), grammarForTag(tag));
  if (!hl.html) return `<pre><code${cls ? ` class="${cls}"` : ''}>${escaped ? body : esc(body)}</code></pre>\n`;
  const attrs = ` class="hljs${cls ? ' ' + cls : ''}" data-hl="${esc(hl.lang)}"${hl.auto ? ' data-hl-auto="1"' : ''}`;
  return `<pre><code${attrs}>${park ? park(hl.html) : hl.html}\n</code></pre>\n`;
}
