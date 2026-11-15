// Agent Neo GUI: the page side. Plain ES modules, no build step (plan v0.4 §5). Python owns the agent; this file renders
// the event stream (lib/model.js), runs the composer and routes every link through the open_external command.
import { renderMd, linkify, codeOf } from './lib/md.js';
import { CTX_MIME, setDrag, getDrag, ctxForLink, plainRef, expandDraft } from './lib/dnd.js';
import { newModel, apply, rollup, hasSteps } from './lib/model.js';

const T = window.__TAURI__;
const invoke = (cmd, body = {}) => T.pytauri.pyInvoke(cmd, body);
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let st = { running: false, busy: '', show_thinking: false, workspace: '', backends: [], browser_modes: [] };
const model = newModel();
const openKeys = new Set();  // <details data-key> the user opened
const turnEls = new Map();   // turn id -> <section>
let chips = [];
let shownStartupWarnings = false;
const conv = $('conv'), ta = $('prompt');
window.__neo = { model, get state() { return st; } };  // read-only handle for e2e diagnostics

// ------------------------------------------------------------------ events
function onEvent(raw) {
  let e = raw;
  if (typeof e === 'string') { try { e = JSON.parse(e); } catch { return; } }
  if (!e || typeof e !== 'object') return;
  if (e.type === 'gui_state') return applyState(e);
  const stick = nearBottom();
  const changed = apply(model, e);
  if (changed === 'notice') appendNotice(model.items[model.items.length - 1]);
  else if (changed !== null && changed !== undefined) renderTurn(changed);
  if (stick) scrollDown();
}

function nearBottom() { return conv.scrollHeight - conv.scrollTop - conv.clientHeight < 80; }
function scrollDown() { conv.scrollTop = conv.scrollHeight; }

function applyState(s) {
  const thinkingChanged = s.show_thinking !== st.show_thinking;
  const wsChanged = s.workspace !== st.workspace;
  st = s;
  document.title = 'Agent Neo';
  $('title').textContent = 'Agent Neo';
  $('fake').hidden = !s.fake;
  fillSelect($('backend'), s.backends, s.backend);
  fillSelect($('browser-mode'), s.browser_modes, s.browser_mode);
  if ($('workspace') && (document.activeElement !== $('workspace') || wsChanged)) {
    $('workspace').value = s.workspace;
  }
  const wsPill = $('ws-pill');
  if (wsPill) {
    const wsName = s.workspace ? s.workspace.replace(/^.*[\\/]/, '') || s.workspace : 'Select folder...';
    wsPill.textContent = wsName;
    wsPill.title = s.workspace ? `Workspace: ${s.workspace}\n(Click to browse folder)` : 'Click to browse folder';
  }
  $('thinking').classList.toggle('on', !!s.show_thinking);
  $('thinking').setAttribute('aria-pressed', String(!!s.show_thinking));
  $('yolo').classList.toggle('on', !!s.yolo);
  $('yolo').setAttribute('aria-pressed', String(!!s.yolo));
  const err = $('settings-err');
  err.hidden = !s.settings_error;
  err.textContent = s.settings_error ? `Settings error (fix the file, then reload): ${s.settings_error}` : '';
  // startup settings warnings (later loads push them as notices from Python); shown once
  if (!shownStartupWarnings) { shownStartupWarnings = true; for (const w of s.settings_warnings || []) notice(w, 'warn'); }
  const busy = !!s.busy;
  $('send').disabled = busy;
  $('switch-account').disabled = busy || !!s.running;
  $('stop').hidden = !s.running;
  $('hint').textContent = s.busy ? s.busy + '…' : s.running ? 'Working… (Enter queues, Ctrl+Enter steers, Stop ends turn)' : '';
  if (thinkingChanged) for (const id of turnEls.keys()) renderTurn(id);
}

function fillSelect(sel, values, current) {
  if (sel.options.length !== values.length) {
    sel.replaceChildren(...values.map((v) => { const o = el('option', '', v); o.value = v; return o; }));
  }
  sel.value = current;
}

// ------------------------------------------------------------------ rendering
function appendNotice(item) {
  $('welcome')?.remove();
  if (item.text && item.text.startsWith('Signed in;')) {
    conv.querySelectorAll('.note.warn').forEach((el) => {
      if (el.textContent.includes('Sign-in needed')) el.remove();
    });
  }
  const n = el('div', 'note ' + item.level, item.text);
  n.dataset.testid = 'notice';
  conv.append(n);
}

function details(cls, key, summaryNodes, bodyFn) {
  const d = el('details', cls);
  d.dataset.key = key;
  if (openKeys.has(key)) d.open = true;
  const s = el('summary');
  s.append(...summaryNodes);
  d.append(s);
  const body = bodyFn();
  if (body) d.append(body);
  return d;
}

function renderSteps(run, keyPrefix) {
  const box = el('div', 'stepsbody');
  for (const s of run.steps) {
    if (st.show_thinking) for (const t of s.thoughts) { const n = el('div', 'thought', t); n.dataset.testid = 'thought'; box.append(n); }
    const name = s.sub ? 'run_subagent' : s.tool ? s.tool.name : s.isFinal ? 'final' : 'reply';
    const summary = s.sub ? s.sub.task : s.tool ? s.tool.summary : s.isFinal ? 'final answer' : s.errors.length ? s.errors[s.errors.length - 1] : (s.replyLen ? `reply (${s.replyLen} chars)` : 'waiting…');
    const fail = (s.result && !s.result.ok) || s.errors.length || (s.sub && s.sub.outcome && s.sub.outcome !== 'final');
    const pending = s.tool && !s.result;
    const cls = 'tool' + (fail ? ' fail' : pending ? ' pending' : '') + (s.sub ? ' sub' : '');
    const key = `${keyPrefix}.s${s.n}`;
    const d = details(cls, key, [el('span', 'tname', `#${s.n} ${name}`), el('span', 'tsum', summary)], () => {
      const b = el('div');
      if (s.tool) b.append(el('pre', 'args', JSON.stringify(s.tool.args, null, 2)));
      for (const a of s.approvals) {
        const txt = a.auto ? 'auto-approved (yolo)' : a.kind === 'tool' ? (a.answer ? 'approved' : 'denied')
          : a.kind === 'steps' ? (a.answer > 0 ? `continued +${a.answer} steps` : 'stopped at the step threshold')
          : a.kind === 'question' ? (a.answer ? `answered: "${a.answer}"` : 'declined')
            : (a.answer ? 'continued past the ceiling' : 'stopped at the ceiling');
        b.append(el('div', 'note', '✔ ' + txt));
      }
      if (s.sub) {
        const sb = el('div', 'tool sub');
        sb.dataset.testid = 'subagent';
        sb.append(el('div', 'note', `sub-agent ${s.sub.sub_id}: ${s.sub.task}`));
        sb.append(renderSteps(s.sub.run, `${key}.${s.sub.sub_id}`));
        if (s.sub.outcome) sb.append(el('div', 'note' + (s.sub.outcome === 'final' ? '' : ' error'), `sub-agent ${s.sub.outcome}: ${s.sub.message}`));
        b.append(sb);
      }
      if (s.result) {
        b.append(el('div', 'note', `result (${s.result.length} chars${s.result.ok ? '' : ', failed'}):`));
        b.append(el('pre', '', s.result.preview));
      }
      for (const m of s.errors) b.append(el('div', 'note error', m));
      return b;
    });
    d.dataset.testid = 'step';
    box.append(d);
  }
  return box;
}

function askCard(p) {
  const card = el('div', 'askcard');
  card.dataset.testid = 'askcard';
  card.dataset.kind = p.kind;
  const head = el('div', 'askhead', (p.sub ? 'Sub-agent: ' : '') +
    (p.kind === 'tool' ? `Approve ${p.tool || 'tool'}?`
     : p.kind === 'steps' ? 'Step threshold reached'
     : p.kind === 'question' ? 'Question from Agent Neo'
     : 'Hard ceiling reached'));
  card.append(head, el('div', 'asktext', p.summary));
  if (p.kind === 'tool' && p.args) card.append(el('pre', 'args', JSON.stringify(p.args, null, 2)));
  const row = el('div', 'askchoices');
  const answer = (v) => {
    row.querySelectorAll('button, input').forEach((b) => { b.disabled = true; });
    invoke('approve', { id: p.id, answer: v }).catch((err) => notice(`approve failed: ${err}`, 'error'));
  };
  const btn = (text, v, cls, testid) => {
    const b = el('button', 'choice ' + cls, text);
    b.dataset.testid = testid;
    b.onclick = () => answer(v);
    return b;
  };
  if (p.kind === 'tool') row.append(btn('Approve', true, 'rec', 'approve'), btn('Deny', false, 'deny', 'deny'));
  else if (p.kind === 'steps') {
    const chunk = p.chunk || st.step_chunk || 10;
    const n = el('input', 'n');
    n.type = 'number'; n.min = '1'; n.value = String(chunk); n.dataset.testid = 'steps-n';
    const more = el('button', 'choice', 'Continue');
    more.dataset.testid = 'continue-n';
    more.onclick = () => answer(Math.max(1, parseInt(n.value, 10) || chunk));
    row.append(btn(`Continue +${chunk}`, chunk, 'rec', 'continue'), n, more, btn('Stop', 0, 'deny', 'stop-steps'));
  } else if (p.kind === 'question') {
    if (Array.isArray(p.options) && p.options.length > 0) {
      for (let i = 0; i < p.options.length; i++) {
        const opt = p.options[i];
        row.append(btn(opt, opt, '', `choice-${i}`));
      }
    }
    const qInput = el('input', 'qinput');
    qInput.type = 'text';
    qInput.placeholder = (Array.isArray(p.options) && p.options.length > 0) ? 'Or type custom answer...' : 'Type your answer...';
    qInput.dataset.testid = 'question-input';
    qInput.onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const val = qInput.value.trim();
        if (val) answer(val);
      }
    };
    const submitBtn = el('button', 'choice rec', 'Submit');
    submitBtn.dataset.testid = 'submit-answer';
    submitBtn.onclick = () => {
      const val = qInput.value.trim();
      if (val) answer(val);
      else qInput.focus();
    };
    row.append(qInput, submitBtn);
    setTimeout(() => qInput.focus(), 50);
  } else row.append(btn('Continue', true, 'rec', 'continue'), btn('Stop', false, 'deny', 'stop-ceiling'));
  card.append(row);
  return card;
}

function renderTurn(id) {
  const t = model.turns.get(id);
  if (!t) return;
  $('welcome')?.remove();
  let sec = turnEls.get(id);
  if (!sec) {
    sec = el('section', 'turn');
    sec.dataset.turn = String(id);
    turnEls.set(id, sec);
    conv.append(sec);
  }
  const parts = [];
  const u = el('div', 'msg user', t.text);
  u.dataset.testid = 'user-msg';
  parts.push(u);
  if (hasSteps(t) || t.running) {
    const errored = t.outcome === 'error' || t.errors.length > 0;
    const d = details('steps' + (t.running ? ' live' : '') + (errored ? ' err' : ''), `t${id}`,
      [el('span', 'sname', t.running ? 'Working' : 'Steps'), (() => { const p = el('span', 'spreview', rollup(t)); p.dataset.testid = 'steps-line'; return p; })()],
      () => renderSteps(t, `t${id}`));
    d.dataset.testid = 'steps';
    parts.push(d);
  }
  if (t.pending) parts.push(askCard(t.pending));
  if (t.final !== null) {
    const m = el('div', 'msg assistant');
    m.dataset.testid = 'final';
    m.append(el('div', 'who', 'Agent Neo'));
    const md = el('div', 'md');
    if (t._mdSrc !== t.final) { t._mdSrc = t.final; t._mdHtml = renderMd(t.final); }
    md.innerHTML = t._mdHtml;
    linkify(md, st.workspace);
    m.append(md);
    parts.push(m);
  }
  for (const msg of t.errors) { const n = el('div', 'note error', msg); n.dataset.testid = 'turn-error'; parts.push(n); }
  if (!t.running && t.final === null && t.outcome && t.outcome !== 'error') {
    parts.push(el('div', 'note warn', t.outcome === 'cancelled' ? 'Stopped.' : 'Stopped at the step ceiling without a final answer.'));
  }
  sec.replaceChildren(...parts);
}

function notice(text, level = 'info') { appendNotice({ text, level }); }

// open state of <details> survives re-renders
conv.addEventListener('toggle', (e) => {
  const d = e.target;
  if (!(d instanceof HTMLDetailsElement) || !d.dataset.key) return;
  if (d.open) openKeys.add(d.dataset.key); else openKeys.delete(d.dataset.key);
}, true);

// ------------------------------------------------------------------ links: every link opens outside the app
async function openLink(kind, target, reveal) {
  let r;
  try { r = await invoke('open_external', { target, kind, reveal }); } catch (err) { return notice(`open failed: ${err}`, 'error'); }
  if (r && r.confirm) {
    if (await confirmBox(r.message || `Open ${target}?`)) await invoke('open_external', { target: r.path || target, kind: 'file', reveal: !!r.reveal, confirmed: true });
  }
}

function confirmBox(text) {
  return new Promise((resolve) => {
    const m = el('div', 'modal');
    m.dataset.testid = 'confirm';
    const box = el('div', 'box');
    const row = el('div', 'row');
    const no = el('button', '', 'Cancel'), yes = el('button', 'primary', 'Open');
    no.dataset.testid = 'confirm-no'; yes.dataset.testid = 'confirm-yes';
    const done = (v) => { m.remove(); resolve(v); };
    no.onclick = () => done(false); yes.onclick = () => done(true);
    m.addEventListener('keydown', (e) => { if (e.key === 'Escape') done(false); });
    row.append(no, yes);
    box.append(el('p', 'text', text), row);
    m.append(box);
    document.body.append(m);
    no.focus();
  });
}

conv.addEventListener('click', (e) => {
  const tool = e.target.closest('[data-code]');
  if (tool) {
    e.preventDefault();
    const code = codeOf(tool);
    if (tool.dataset.code === 'copy') copyText(code.replace(/^```[^\n]*\n|\n```$/g, ''));
    else if (tool.dataset.code === 'insert') insertAtCaret(code);
    return;
  }
  const a = e.target.closest('a');
  if (!a) return;
  e.preventDefault();
  const art = a.getAttribute('data-art') || '';
  const i = art.indexOf('|');
  if (i > 0) openLink(art.slice(0, i), art.slice(i + 1), e.ctrlKey);
  else {
    const href = a.getAttribute('href') || '';
    if (href && !href.startsWith('#')) openLink('', href, e.ctrlKey);
  }
});
conv.addEventListener('auxclick', (e) => { if (e.target.closest('a')) e.preventDefault(); });

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return; } catch { /* fall back */ }
  const t = el('textarea');
  t.value = text;
  document.body.append(t);
  t.select();
  document.execCommand('copy');
  t.remove();
}

// ------------------------------------------------------------------ drag and drop
conv.addEventListener('dragstart', (e) => {
  const grip = e.target.closest?.('[data-code="drag"]');
  if (grip) return setDrag(e, { kind: 'code', value: codeOf(grip), label: 'code block' });
  const a = e.target.closest?.('a[data-art]');
  if (a) { const c = ctxForLink(a); if (c) setDrag(e, c); }
});

const composer = $('composer');
let dropHint = null;
function setDropping(mode) {
  composer.classList.toggle('dropping', !!mode);
  if (mode && !dropHint) { dropHint = el('div', 'drophint'); composer.append(dropHint); }
  if (dropHint) dropHint.textContent = mode === 'chip' ? 'Drop to attach' : 'Drop to insert';
  if (!mode && dropHint) { dropHint.remove(); dropHint = null; }
}
composer.addEventListener('dragover', (e) => {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  const types = [...(e.dataTransfer?.types || [])];
  setDropping(types.includes(CTX_MIME) || types.includes('Files') || types.includes('text/uri-list') ? (e.shiftKey ? 'text' : 'chip') : 'text');
});
composer.addEventListener('dragleave', (e) => { if (!composer.contains(e.relatedTarget)) setDropping(''); });
composer.addEventListener('drop', async (e) => {
  e.preventDefault();
  setDropping('');
  const dt = e.dataTransfer;
  if (!dt) return;
  const files = [...dt.files];
  if (files.length) { for (const f of files) await attachFile(f); return focusEnd(); }
  const c = getDrag(dt);
  if (c) { if (e.shiftKey) insertAtCaret(plainRef(c)); else { addChip(c); focusEnd(); } return; }
  const uri = (dt.getData('text/uri-list') || '').split(/\r?\n/).find((l) => l && !l.startsWith('#')) || '';
  if (uri && /^https?:/i.test(uri)) {
    if (e.shiftKey) insertAtCaret(uri); else { addChip({ kind: 'url', value: uri, label: uri.replace(/^https?:\/\//, '').slice(0, 60) }); focusEnd(); }
    return;
  }
  const t = dt.getData('text/plain');
  if (t) insertAtCaret(t);
});

const TEXT_EXT = /\.(txt|md|log|json|ya?ml|xml|csv|ps1|psm1|psd1|py|js|ts|cs|sql|ini|toml|cfg|conf|bat|cmd|sh|html?|css|sls)$/i;
const MAX_INLINE = 64 * 1024;
async function attachFile(f) {
  // WebView2 does not expose the dropped file's path: small text files are attached with their content, others by name.
  if (f.size <= MAX_INLINE && (f.type.startsWith('text/') || TEXT_EXT.test(f.name) || f.type === 'application/json')) {
    const text = await f.text();
    if (!text.includes('\u0000')) return addChip({ kind: 'filetext', value: text, label: f.name });
  }
  addChip({ kind: 'file', value: f.name, label: f.name + ' (name only)' });
}

function addChip(c) {
  if (chips.some((x) => x.kind === c.kind && x.value === c.value)) return;
  chips.push(c);
  renderChips();
}
function renderChips() {
  const box = $('chips');
  box.replaceChildren(...chips.map((c, i) => {
    const ch = el('span', 'chip');
    ch.dataset.testid = 'chip';
    ch.dataset.kind = c.kind;
    ch.title = c.kind === 'filetext' ? `${c.label} (${c.value.length} chars attached)` : c.value;
    const x = el('button', '', '×');
    x.title = 'Remove';
    x.onclick = () => { chips.splice(i, 1); renderChips(); ta.focus(); };
    ch.append(el('span', '', c.label || c.value), x);
    return ch;
  }));
}

function focusEnd() {
  ta.focus();
  const p = ta.value.length;
  ta.setSelectionRange(p, p);
}
function insertAtCaret(text) {
  const start = ta.selectionStart ?? ta.value.length, end = ta.selectionEnd ?? start;
  const pre = ta.value.slice(0, start), post = ta.value.slice(end);
  const sep = pre && !/\s$/.test(pre) ? ' ' : '';
  ta.value = pre + sep + text + post;
  const p = pre.length + sep.length + text.length;
  ta.focus();
  ta.setSelectionRange(p, p);
}

// ------------------------------------------------------------------ prompt history
const HISTORY_KEY = 'neo.promptHistory';
const HISTORY_MAX = 100;
function loadPromptHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}
const promptHistory = loadPromptHistory();
let historyIndex = -1;
let currentDraft = '';

function pushPromptHistory(prompt) {
  const clean = (prompt || '').trim();
  if (!clean) return;
  if (promptHistory.length > 0 && promptHistory[promptHistory.length - 1] === clean) return;
  promptHistory.push(clean);
  if (promptHistory.length > HISTORY_MAX) promptHistory.shift();
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(promptHistory));
  } catch { /* ignore storage errors */ }
  historyIndex = -1;
  currentDraft = '';
}

// ------------------------------------------------------------------ composer
async function send() {
  if (st.busy) return;
  const raw = ta.value;
  const isSlash = raw.trim().startsWith('/');
  const text = isSlash ? raw.trim() : expandDraft(raw, chips);
  if (!text) return;
  pushPromptHistory(raw);
  const sentChips = isSlash ? [] : chips.slice();
  let r;
  try { r = await invoke('send', { text }); } catch (err) { return notice(`send failed: ${err}`, 'error'); }
  const clearSent = () => {
    if (ta.value === raw) ta.value = '';
    if (sentChips.length) { chips = chips.filter((c) => !sentChips.includes(c)); renderChips(); }
  };
  if (r && r.ok) {
    clearSent();
    if (r.queued) {
      notice(`Queued prompt for next turn: ${text.slice(0, 80)}`, 'info');
    }
  }
  else if (r && r.error === 'settings') notice(`Not sent: fix the settings file first. ${r.message || ''}`, 'error');
  else if (r && r.error === 'busy') notice('Not sent: the agent is busy.', 'warn');
  else if (isSlash && ta.value === raw) ta.value = '';
  ta.focus();
}

async function steer() {
  if (st.busy) return;
  const raw = ta.value;
  const isSlash = raw.trim().startsWith('/');
  const text = isSlash ? raw.trim() : expandDraft(raw, chips);
  if (!text) return;
  pushPromptHistory(raw);
  const sentChips = isSlash ? [] : chips.slice();
  let r;
  try { r = await invoke('steer', { text }); } catch (err) { return notice(`steer failed: ${err}`, 'error'); }
  const clearSent = () => {
    if (ta.value === raw) ta.value = '';
    if (sentChips.length) { chips = chips.filter((c) => !sentChips.includes(c)); renderChips(); }
  };
  if (r && r.ok) clearSent();
  else if (r && r.error === 'settings') notice(`Not sent: fix the settings file first. ${r.message || ''}`, 'error');
  ta.focus();
}

ta.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    steer();
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
    return;
  }
  if (e.key === 'ArrowUp' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
    const isFirstLine = ta.selectionStart === 0 || !ta.value.slice(0, ta.selectionStart).includes('\n');
    if (isFirstLine && promptHistory.length > 0) {
      if (historyIndex === -1) {
        currentDraft = ta.value;
        historyIndex = promptHistory.length - 1;
      } else if (historyIndex > 0) {
        historyIndex--;
      }
      ta.value = promptHistory[historyIndex];
      focusEnd();
      e.preventDefault();
    }
    return;
  }
  if (e.key === 'ArrowDown' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
    const isLastLine = ta.selectionEnd === ta.value.length || !ta.value.slice(ta.selectionEnd).includes('\n');
    if (isLastLine && historyIndex !== -1) {
      if (historyIndex < promptHistory.length - 1) {
        historyIndex++;
        ta.value = promptHistory[historyIndex];
      } else {
        historyIndex = -1;
        ta.value = currentDraft;
      }
      focusEnd();
      e.preventDefault();
    }
    return;
  }
});
$('send').onclick = send;
$('stop').onclick = () => invoke('cancel');

// ------------------------------------------------------------------ header
$('backend').onchange = (e) => invoke('set_backend', { backend: e.target.value });
$('browser-mode').onchange = (e) => invoke('set_option', { browser_mode: e.target.value });
if ($('ws-browse')) $('ws-browse').onclick = () => invoke('browse_workspace');
if ($('ws-pill')) $('ws-pill').onclick = () => invoke('browse_workspace');
if ($('workspace')) {
  $('workspace').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); invoke('set_workspace', { path: e.target.value }); }
    if (e.key === 'Escape') { e.target.value = st.workspace; ta.focus(); }
  });
}
$('thinking').onclick = () => invoke('set_option', { show_thinking: !st.show_thinking });
$('yolo').onclick = () => invoke('set_option', { yolo: !st.yolo });
$('settings').onclick = () => invoke('open_settings');
$('reload').onclick = () => invoke('reload_settings');
$('new-chat').onclick = () => invoke('new_chat');
$('switch-account').onclick = () => invoke('switch_account');

// ------------------------------------------------------------------ colour theme (plan v0.5 D4)
// theme-boot.js applied the saved theme before first paint; this keeps the header dropdown in step and saves changes.
const themeSel = $('theme-toggle');
const themeApi = window.neoTheme;
if (themeSel && themeApi) {
  themeSel.value = themeApi.load();
  themeSel.onchange = () => { themeSel.value = themeApi.save(themeSel.value); };
}

// ------------------------------------------------------------------ chat content zoom (docs/PLAN-zoom.md)
// Only conversation + composer CONTENT scale (CSS var --content-zoom); header, status and chrome stay at 100%.
const ZOOM_MIN = 80, ZOOM_MAX = 150, ZOOM_STEP = 10, ZOOM_DEFAULT = 100, ZOOM_KEY = 'neo.contentZoom';
function loadZoom() {
 let raw = null;
 try { raw = localStorage.getItem(ZOOM_KEY); } catch { return ZOOM_DEFAULT; }
 // missing / malformed / non-step / out-of-range -> 100 (normalised, never clamped)
 if (raw === null || !/^[0-9]+$/.test(raw)) return ZOOM_DEFAULT;
 const n = Number(raw);
 return n >= ZOOM_MIN && n <= ZOOM_MAX && n % ZOOM_STEP === 0 ? n : ZOOM_DEFAULT;
}
let zoom = loadZoom();
function renderZoom() {
 document.documentElement.style.setProperty('--content-zoom', String(zoom / 100));
 $('zoom-level').textContent = zoom + '%';
 $('zoom-out').disabled = zoom <= ZOOM_MIN;
 $('zoom-in').disabled = zoom >= ZOOM_MAX;
}
function setZoom(next, persistSame = false) {
 if (next < ZOOM_MIN || next > ZOOM_MAX) return;
 if (next === zoom && !persistSame) return;
 // keep the reading position: bottom stays bottom, otherwise keep the same relative offset
 const max = conv.scrollHeight - conv.clientHeight;
 const atBottom = max <= 0 || nearBottom();
 const ratio = max > 0 ? conv.scrollTop / max : 0;
 zoom = next;
 try { localStorage.setItem(ZOOM_KEY, String(zoom)); } catch { /* best effort */ }
 renderZoom();
 if (atBottom) scrollDown();
 else conv.scrollTop = ratio * Math.max(0, conv.scrollHeight - conv.clientHeight);
}
$('zoom-out').onclick = () => setZoom(zoom - ZOOM_STEP);
$('zoom-in').onclick = () => setZoom(zoom + ZOOM_STEP);
// window-wide shortcuts (also inside the composer); native WebView zoom suppressed
window.addEventListener('keydown', (e) => {
 if (!e.ctrlKey || e.altKey || e.metaKey) return;
 const k = e.key, c = e.code;
 let next = null, reset = false;
 if (k === '+' || k === '=' || c === 'NumpadAdd') next = zoom + ZOOM_STEP;
 else if (k === '-' || k === '_' || c === 'NumpadSubtract') next = zoom - ZOOM_STEP;
 else if (k === '0' || c === 'Numpad0') { next = ZOOM_DEFAULT; reset = true; }
 if (next === null) return;
 e.preventDefault();
 e.stopPropagation();
 setZoom(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next)), reset);
}, true);
// no Ctrl+wheel zoom: block the native page zoom so chrome never scales
window.addEventListener('wheel', (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false, capture: true });
Object.defineProperty(window.__neo, 'zoom', { get: () => zoom });
renderZoom();

// ------------------------------------------------------------------ start
(async () => {
  await T.event.listen('neo:event', (ev) => onEvent(ev.payload));
  applyState(await invoke('get_state'));
  ta.focus();
})().catch((err) => notice(`startup failed: ${err}`, 'error'));
