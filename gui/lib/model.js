// Conversation model: folds the core's event stream (neo_events.py schema v1) into turns, and computes the rolled-up
// one-line status per turn: the user's request and the final answer stay outside; everything in between is one line that
// expands to the step list.

export function newTurn(text, id) {
  return {
    id, text, steps: [], final: null, outcome: '', running: true, status: '', errors: [],
    pending: null, // the pending approval (also one raised by a sub-agent): never collapsed
  };
}
function newStep(n) {
  return { n, limit: 0, ceiling: 0, thoughts: [], tool: null, result: null, approvals: [], sub: null, errors: [], replyLen: 0 };
}

export function newModel() {
  return { items: [], turns: new Map() };
}

function stepOf(turn, e) {
  const n = typeof e.step === 'number' ? e.step : (turn.steps.length ? turn.steps[turn.steps.length - 1].n : 0);
  let s = turn.steps.find((x) => x.n === n);
  if (!s) { s = newStep(n); turn.steps.push(s); }
  return s;
}

/** Apply one core event to a turn (also used recursively for a sub-agent's own run). */
export function applyToTurn(turn, e, top = turn) {
  switch (e.type) {
    case 'turn_start': turn.text = turn.text || e.text; turn.running = true; break;
    case 'step_start': {
      const s = stepOf(turn, e);
      s.limit = e.limit; s.ceiling = e.ceiling;
      turn.status = '';
      break;
    }
    case 'status': turn.status = e.text; break;
    case 'reply': stepOf(turn, e).replyLen = e.length; break;
    case 'thought': stepOf(turn, e).thoughts.push(e.text); break;
    case 'tool_call': stepOf(turn, e).tool = { name: e.name, args: e.args, summary: e.summary }; break;
    case 'tool_result': stepOf(turn, e).result = { ok: e.ok, preview: e.preview, length: e.length }; break;
    case 'approval_request': top.pending = { ...e, sub: turn !== top }; break;
    case 'approval_result':
      if (top.pending && top.pending.id === e.id) top.pending = null;
      if (e.kind === 'tool' || typeof e.step === 'number' || turn.steps.length) stepOf(turn, e).approvals.push(e);
      break;
    case 'subagent_start': {
      const s = stepOf(turn, e);
      s.sub = { sub_id: e.sub_id, task: e.task, run: newTurn(e.task, e.sub_id), outcome: '', message: '' };
      break;
    }
    case 'subagent_event': {
      const s = turn.steps.find((x) => x.sub && x.sub.sub_id === e.sub_id);
      if (s) applyToTurn(s.sub.run, e.event, top);
      break;
    }
    case 'subagent_end': {
      const s = turn.steps.find((x) => x.sub && x.sub.sub_id === e.sub_id);
      if (s) { s.sub.outcome = e.outcome; s.sub.message = e.message; s.sub.run.running = false; }
      break;
    }
    case 'final': turn.final = e.message; stepOf(turn, e).isFinal = true; break;
    case 'error':
      if (e.fatal) turn.errors.push(e.message);
      else stepOf(turn, e).errors.push(e.message);
      break;
    case 'turn_end':
      turn.running = false; turn.outcome = e.outcome; turn.status = '';
      if (turn === top) top.pending = null;
      break;
    default: break;
  }
}

/** Apply a page event to the model; returns the id of the turn that changed (or 'notice' / null). */
export function apply(model, e) {
  if (e.type === 'gui_notice') {
    model.items.push({ kind: 'notice', text: e.text, level: e.level || 'info' });
    return 'notice';
  }
  if (e.type === 'turn_start') {
    const t = newTurn(e.text, e.turn);
    model.turns.set(e.turn, t);
    model.items.push({ kind: 'turn', id: e.turn });
    return e.turn;
  }
  const t = model.turns.get(e.turn);
  if (!t) {
    // a status / error outside a turn (e.g. a driver notice before the first turn): show it as a notice
    if (e.type === 'status' || e.type === 'error') {
      model.items.push({ kind: 'notice', text: e.text || e.message, level: e.type === 'error' ? 'error' : 'info' });
      return 'notice';
    }
    return null;
  }
  applyToTurn(t, e);
  return e.turn;
}

const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

/** Every tool step of a run, sub-agent steps included (for counts). */
function toolNames(turn) {
  const out = [];
  for (const s of turn.steps) {
    if (s.tool) out.push(s.tool.name);
    if (s.sub) out.push(...toolNames(s.sub.run));
  }
  return out;
}

/** The one-line rollup for a turn: live while it runs, a summary when it is done. */
export function rollup(turn) {
  const last = turn.steps[turn.steps.length - 1];
  if (turn.running) {
    if (!last) return turn.status || 'Starting…';
    let where = `Step ${last.n}/${last.limit || '?'}`;
    let tool = last.tool ? last.tool.name : '';
    let status = turn.status;
    if (last.sub && last.sub.run.running) {
      const sl = last.sub.run.steps[last.sub.run.steps.length - 1];
      where += ` · sub-agent${sl ? ` step ${sl.n}/${sl.limit || '?'}` : ''}`;
      tool = sl && sl.tool ? sl.tool.name : '';
      status = last.sub.run.status || status;
    }
    const doing = turn.pending ? 'waiting for your approval'
      : status || (last.result ? 'thinking…' : last.tool ? 'running…' : 'waiting for reply…');
    return [where, tool, doing].filter(Boolean).join(' · ');
  }
  const names = toolNames(turn);
  const counts = new Map();
  for (const n of names) counts.set(n, (counts.get(n) || 0) + 1);
  const tools = [...counts].map(([n, c]) => (c > 1 ? `${n}×${c}` : n)).join(', ');
  const outcome = { final: 'done', cancelled: 'stopped', ceiling: 'stopped at the step ceiling', error: 'error' }[turn.outcome] || turn.outcome;
  return [plural(turn.steps.length, 'step'), tools ? plural(names.length, 'tool call') + ': ' + tools : 'no tools', outcome]
    .filter(Boolean).join(' · ');
}

/** Has this turn anything to put in the rolled-up section? */
export function hasSteps(turn) {
  return turn.steps.length > 0 || turn.errors.length > 0;
}
