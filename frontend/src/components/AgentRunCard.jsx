import React, { useState } from 'react';
import { Check, AlertTriangle, ChevronDown, X } from 'lucide-react';

/**
 * Timeline of what the agents did, in plain words.
 *
 * live: shown while the run is going (expanded, with a heading).
 * done: collapsed to one line above the reply; tapping expands it.
 */
export function AgentTimeline({ run, live = false }) {
  const [open, setOpen] = useState(live);
  if (!run || !run.steps) return null;
  const steps = run.steps;
  // Judge by how the run ended: a problem a review found and the Planner
  // fixed is still a success.
  const last = steps[steps.length - 1];
  const anyIssue = !last || last.status !== 'done';
  const seconds = run.finishedAt && run.startedAt
    ? Math.max(1, Math.round((run.finishedAt - run.startedAt) / 1000)) : null;
  const took = seconds == null ? '' : seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)} min ${seconds % 60}s`;

  const isApprove = run.kind === 'approve';
  const liveTitle = isApprove ? 'Approving your plan' : 'Our agents are working on your plan';
  const doneTitle = isApprove
    ? (anyIssue ? 'Approval needs your attention' : 'Plan approved')
    : (anyIssue ? 'Plan needs your attention' : run.kind === 'edit' ? 'Plan updated' : 'Plan created');

  if (live) {
    return (
      <div className="run-card" aria-live="polite">
        <div className="run-card-head">
          <span className="run-pulse" aria-hidden="true" />
          <span>{liveTitle}</span>
        </div>
        <Steps steps={steps} />
      </div>
    );
  }

  return (
    <div className={`run-card run-card-done${open ? ' open' : ''}`}>
      <button type="button" className="run-card-summary" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className={`run-dot ${anyIssue ? 'issue' : 'done'}`} aria-hidden="true">
          {anyIssue ? <AlertTriangle size={11} strokeWidth={2.5} /> : <Check size={11} strokeWidth={3} />}
        </span>
        <span className="run-card-summary-text">
          {doneTitle}{took && <span className="run-card-took"> · {took}</span>}
        </span>
        <span className="run-card-toggle">{open ? 'Hide steps' : 'View steps'}</span>
        <ChevronDown size={15} className={`run-caret${open ? ' open' : ''}`} aria-hidden="true" />
      </button>
      {open && <Steps steps={steps} />}
    </div>
  );
}

function Steps({ steps }) {
  return (
    <ol className="run-steps">
      {steps.map((s, i) => (
        <li key={s.id || i} className={`run-step ${s.status}`}>
          <span className="run-mark" aria-hidden="true">
            {s.status === 'done' && <Check size={11} strokeWidth={3} />}
            {s.status === 'issue' && <AlertTriangle size={10} strokeWidth={2.5} />}
            {s.status === 'failed' && <X size={11} strokeWidth={3} />}
            {s.status === 'running' && <span className="run-spinner" />}
          </span>
          <div className="run-step-body">
            <div className="run-step-title">
              {s.title}
              <span className="run-agent">{s.agent}</span>
            </div>
            {s.detail && <div className="run-step-detail">{s.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** The opening bubble + live timeline shown while a run is in progress. */
export default function LiveRun({ run, logo }) {
  if (!run) return null;
  return (
    <div className="message-row run-live">
      <div className="message-avatar ai">
        {logo ? <img src={logo} alt="" className="message-avatar-logo" /> : 'N'}
      </div>
      <div className="run-live-col">
        <div className="message-bubble ai run-intro-bubble">
          {run.intro
            ? run.intro
            : <span className="run-typing" aria-label="Writing"><i /><i /><i /></span>}
        </div>
        <AgentTimeline run={run} live />
      </div>
    </div>
  );
}
