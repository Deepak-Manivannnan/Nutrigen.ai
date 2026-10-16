import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronDown, Check, Loader2, AlertTriangle, RotateCcw } from 'lucide-react';
import { connectStreaming } from '../utils/api';

/**
 * Live progress bar for a running agent swarm.
 *
 * Sits between the last message and the composer. Collapsed it is a single
 * line; tapping it reveals the stage list. It renders nothing at all once the
 * run finishes, so it never lingers as decoration after the answer arrives.
 *
 * Everything shown here is derived from the existing `agent_message` SSE
 * stream. No new backend endpoint, and no agent internals are exposed: the
 * model's own thoughts and tool arguments are deliberately mapped to plain
 * progress copy rather than displayed verbatim.
 */

// User-facing stages. `key` is stable; retries append a new pass rather than
// resetting, so the list reads top to bottom and simply grows.
const STAGES = [
  { key: 'reading', label: 'Reading your request', agent: 'supervisor' },
  { key: 'planning', label: 'Building your plan', agent: 'planner' },
  { key: 'nutrition', label: 'Checking calories', agent: 'nutrition' },
  { key: 'allergy', label: 'Checking allergens', agent: 'allergy' },
  { key: 'grocery', label: 'Building grocery list', agent: 'shopper' },
];

// Progress copy for the active stage, refined by what the agent is doing.
// Never shows a tool name or an argument.
const DETAIL = {
  planningSearching: 'Looking up real recipes…',
  planningWriting: 'Writing the meals…',
};

function freshPass() {
  return STAGES.map(s => ({ ...s, status: 'pending', detail: '' }));
}

export default function AgentTraceBar({ taskId, active }) {
  const [passes, setPasses] = useState([]);
  const [open, setOpen] = useState(false);
  const seenRef = useRef(new Set());

  const reset = useCallback(() => {
    seenRef.current = new Set();
    setPasses([]);
    setOpen(false);
  }, []);

  // Reset whenever a new run starts.
  useEffect(() => { if (taskId) reset(); }, [taskId, reset]);

  useEffect(() => {
    if (!taskId || !active) return undefined;
    const es = connectStreaming(taskId, handleEvent, () => {});
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, active]);

  function handleEvent(evt) {
    const { from, to, type, payload } = evt || {};
    if (!from || !type) return;

    // The hub broadcasts each supervisor step more than once. Without this the
    // same stage would flip done, running, done again as duplicates arrive.
    const sig = `${from}|${to}|${type}|${payload?.task || ''}|${payload?.tool || ''}`;
    if (seenRef.current.has(sig)) return;
    seenRef.current.add(sig);

    setPasses(prev => applyEvent(prev, from, to, type, payload || {}));
  }

  if (!active || passes.length === 0) return null;

  const flat = passes.flat();
  const currentIdx = flat.findIndex(s => s.status === 'running');
  const current = currentIdx >= 0 ? flat[currentIdx] : null;
  const doneCount = flat.filter(s => s.status === 'done').length;
  const failed = flat.some(s => s.status === 'failed');

  const headline = current
    ? current.detail || current.label
    : failed ? 'Adjusting your plan…' : 'Finishing up…';

  return (
    <div className="agent-trace" id="agent-trace-bar">
      <button
        type="button"
        className="agent-trace-toggle"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls="agent-trace-panel"
        id="agent-trace-toggle"
      >
        <Loader2 size={15} className="agent-trace-spin" aria-hidden="true" />
        <span className="agent-trace-headline">{headline}</span>
        <span className="agent-trace-meta">
          {passes.length > 1 && `pass ${passes.length}`}
          {doneCount}/{flat.length}
        </span>
        <ChevronDown
          size={16}
          className={`agent-trace-caret${open ? ' open' : ''}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="agent-trace-panel" id="agent-trace-panel">
          <ul className="agent-trace-list">
            {flat.map((s, i) => (
              <li key={`${s.key}-${i}`} className={`agent-trace-row ${s.status}`}>
                <span className="agent-trace-mark">
                  {s.status === 'done' && <Check size={12} strokeWidth={3} aria-hidden="true" />}
                  {s.status === 'running' && <Loader2 size={12} className="agent-trace-spin" aria-hidden="true" />}
                  {s.status === 'failed' && <AlertTriangle size={12} strokeWidth={2.5} aria-hidden="true" />}
                </span>
                <span className="agent-trace-label">{s.label}</span>
                {s.detail && <span className="agent-trace-detail">{s.detail}</span>}
              </li>
            ))}
          </ul>
          {passes.length > 1 && (
            <p className="agent-trace-note">
              <RotateCcw size={12} aria-hidden="true" />
              Pass {passes.length} of up to 3 — a review found something to adjust.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Fold one event into the stage list.
 *
 * Returns a new array; a retry appends a fresh pass so the history of what was
 * tried stays visible instead of the list silently rewinding.
 */
function applyEvent(prev, from, to, type, payload) {
  const pass = prev.length ? prev[prev.length - 1] : freshPass();
  const next = pass.map(s => ({ ...s }));
  const idx = key => next.findIndex(s => s.key === key);

  const setStatus = (key, status, detail) => {
    const i = idx(key);
    if (i < 0) return;
    next[i] = { ...next[i], status, detail: detail ?? next[i].detail };
  };
  const start = (key, detail) => {
    const i = idx(key);
    if (i < 0) return;
    next[i] = { ...next[i], status: 'running', detail: detail ?? '' };
  };

  // A fresh planner assignment means a new pass: mark what is still open as
  // failed (it was the reason we looped) and start a clean list.
  if (type === 'task' && payload.task === 'plan_meals' && to === 'planner_agent') {
    const carried = prev.length ? prev[prev.length - 1].map(s => ({ ...s })) : freshPass();
    for (const s of carried) if (s.status === 'running') s.status = 'failed';
    start('reading', '');
    start('planning', DETAIL.planningWriting);
    return [...prev, carried];
  }

  if (type === 'task' && to === 'planner_agent') {
    start('reading', '');
    start('planning', DETAIL.planningWriting);
    return [pass];
  }

  if (from === 'planner_agent') {
    if (type === 'tool_call') start('planning', DETAIL.planningSearching);
    if (type === 'status' || type === 'thought') start('planning', DETAIL.planningWriting);
    if (type === 'draft') setStatus('planning', 'done');
    return [pass];
  }

  if (type === 'task' && payload.task === 'parallel_reviews') {
    start('nutrition', '');
    start('allergy', '');
    return [pass];
  }

  if (from === 'nutrition_agent') {
    if (type === 'approve') {
      const passed = payload.passed !== false;
      setStatus('nutrition', passed ? 'done' : 'failed');
      if (passed) setStatus('allergy', 'done');
    } else {
      start('nutrition', '');
    }
    return [pass];
  }

  if (from === 'allergy_agent') {
    if (type === 'approve') setStatus('allergy', payload.passed !== false ? 'done' : 'failed');
    else start('allergy', '');
    return [pass];
  }

  if (type === 'task' && to === 'shopper_agent') {
    start('grocery', '');
    return [pass];
  }

  if (from === 'shopper_agent' && type === 'draft') setStatus('grocery', 'done');

  return [pass];
}
