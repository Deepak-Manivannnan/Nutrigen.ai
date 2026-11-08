import { useEffect, useRef, useState } from 'react';
import { connectStreaming } from '../utils/api';

/**
 * Follows one chat run's live events and turns them into a short, plain
 * timeline for the progress card.
 *
 * run = null until the server says a plan build / approval actually started
 * (ordinary replies never show a card). Otherwise:
 *   { kind: 'plan' | 'edit' | 'approve', intro, steps: [...], startedAt }
 * step = { id, agent, title, detail, status: 'running' | 'done' | 'issue' | 'failed' }
 */
export default function useAgentRun(taskId, active) {
  const [run, setRun] = useState(null);
  const seen = useRef(new Set());

  useEffect(() => {
    seen.current = new Set();
    setRun(null);
  }, [taskId]);

  useEffect(() => {
    if (!taskId || !active) return undefined;
    const es = connectStreaming(taskId, evt => {
      // The server replays a run's recent events to late listeners, so an
      // event can arrive twice: each message has its own id.
      const key = evt?.message_id;
      if (key) {
        if (seen.current.has(key)) return;
        seen.current.add(key);
      }
      setRun(prev => reduce(prev, evt));
    }, () => {});
    return () => es.close();
  }, [taskId, active]);

  return run;
}

const finishRunning = (steps, status = 'done') =>
  steps.map(s => (s.status === 'running' ? { ...s, status } : s));

function push(run, step) {
  return { ...run, steps: [...run.steps, { id: `${step.agent}-${run.steps.length}`, ...step }] };
}

function updateLast(run, agent, patch) {
  const steps = run.steps.slice();
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].agent === agent) {
      steps[i] = { ...steps[i], ...patch };
      return { ...run, steps };
    }
  }
  return run;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

export function reduce(prev, evt) {
  const { from, to, type } = evt || {};
  const p = evt?.payload || {};
  if (!from || !type) return prev;

  // --- start of a run -------------------------------------------------------
  if (p.event === 'run_started') {
    if (prev) return prev;
    return {
      kind: p.kind || 'plan',
      intro: '',
      startedAt: Date.now(),
      steps: [{
        id: 'supervisor-0', agent: 'Supervisor', status: 'running',
        title: p.kind === 'edit' ? 'Preparing your changes' : 'Getting your plan started',
        detail: 'Sharing your request with the Planner',
      }],
    };
  }
  if (p.event === 'approval' && p.step === 'run_started') {
    if (prev) return prev;
    return {
      kind: 'approve',
      intro: p.intro || '',
      startedAt: Date.now(),
      steps: [{ id: 'Shopper-0', agent: 'Shopper', title: 'Building your grocery list', detail: 'Adding up ingredients and checking prices', status: 'running' }],
    };
  }
  if (!prev) return prev; // nothing to draw until a run has started

  if (p.event === 'intro') return { ...prev, intro: p.text || prev.intro };

  // --- approval steps ---------------------------------------------------------
  if (p.event === 'approval') {
    const STEP = {
      grocery: { agent: 'Shopper', title: 'Building your grocery list', running: 'Adding up ingredients and checking prices' },
      safety: { agent: 'Supervisor', title: 'Final safety check', running: 'Re-checking the plan and the list for your allergies and diet' },
      approved: { agent: 'Supervisor', title: 'Plan approved', running: '' },
    }[p.step];
    if (!STEP) return prev;
    if (p.status === 'running') {
      // Already showing (the grocery step starts with the card): don't repeat it.
      if (prev.steps.some(s => s.title === STEP.title && s.status === 'running')) return prev;
      return push(prev, { agent: STEP.agent, title: STEP.title, detail: STEP.running, status: 'running' });
    }
    if (p.step === 'approved') {
      return push(prev, { agent: STEP.agent, title: STEP.title, detail: 'Your grocery list is ready', status: 'done' });
    }
    const detail = p.status === 'failed'
      ? (p.step === 'safety' ? 'Something in the plan needs a change' : "Couldn't build the list")
      : p.step === 'grocery'
        ? `${plural(p.items || 0, 'item')}${p.cost ? ` · about ₹${Number(p.cost).toLocaleString('en-IN')}` : ''}`
        : 'Everything checks out';
    return updateLast(prev, STEP.agent, { status: p.status === 'failed' ? 'failed' : 'done', detail });
  }

  // --- plan build: Supervisor -> Planner -------------------------------------
  if (from === 'supervisor' && type === 'task' && to === 'planner_agent') {
    // The same assignment is announced twice (when the Supervisor checks the
    // request and when the Planner starts). A fix round is a new step.
    const lastStep = prev.steps[prev.steps.length - 1];
    if (!p.repair && lastStep && lastStep.agent === 'Planner' && lastStep.status === 'running') return prev;
    let run = { ...prev, steps: finishRunning(prev.steps) };
    if (p.repair) {
      const what = [...(p.meals || []), ...(p.days || []).map(d => `day ${d}`)].join(', ');
      return push(run, { agent: 'Planner', title: 'Fixing the flagged meals', detail: what ? `Redoing ${what}` : 'Redoing only what needs a change', status: 'running' });
    }
    if (p.task === 'modify_meal') {
      return push(run, { agent: 'Planner', title: 'Updating your meal', detail: 'Making the change you asked for', status: 'running' });
    }
    const days = p.days || prev.days;
    run = push(run, { agent: 'Planner', title: 'Planning your meals', detail: days ? `Writing a ${days}-day plan` : 'Writing your meals', status: 'running' });
    return run;
  }
  if (from === 'planner_agent') {
    if (type === 'tool_call') return updateLast(prev, 'Planner', { detail: 'Looking up real recipes' });
    if (type === 'draft') {
      const last = [...prev.steps].reverse().find(s => s.agent === 'Planner');
      if (last && last.title !== 'Planning your meals') {
        return updateLast(prev, 'Planner', { status: 'done', detail: 'Done, everything else stays the same' });
      }
      const n = p.days_count;
      const kcal = p.avg_calories ? ` · about ${Math.round(p.avg_calories).toLocaleString('en-IN')} kcal/day` : '';
      return updateLast(prev, 'Planner', { status: 'done', detail: n ? `${plural(n, 'day')} drafted${kcal}` : 'Draft ready' });
    }
    return prev;
  }

  // --- both checks at once ----------------------------------------------------
  if (from === 'supervisor' && type === 'task' && p.task === 'parallel_reviews') {
    let run = { ...prev, steps: finishRunning(prev.steps) };
    run = push(run, { agent: 'Nutrition', title: 'Checking calories', detail: 'Checking each day against your target', status: 'running' });
    run = push(run, { agent: 'Allergy', title: 'Checking allergies and diet', detail: 'Making sure every ingredient is safe for you', status: 'running' });
    return run;
  }
  if (from === 'nutrition_agent' && (type === 'approve' || type === 'feedback')) {
    const ok = type === 'approve' && p.passed !== false;
    return updateLast(prev, 'Nutrition', ok
      ? { status: 'done', detail: 'Calories are on target' }
      : { status: 'issue', detail: p.issues_count ? `${plural(p.issues_count, 'day')} off target` : 'Some days need adjusting' });
  }
  if (from === 'allergy_agent' && (type === 'approve' || type === 'veto')) {
    const ok = type === 'approve' && p.passed !== false;
    return updateLast(prev, 'Allergy', ok
      ? { status: 'done', detail: 'Safe for your allergies and diet' }
      : { status: 'issue', detail: p.violations_count ? `Flagged ${plural(p.violations_count, 'ingredient')}` : 'Found something unsafe' });
  }

  // --- Supervisor decides -----------------------------------------------------
  if (from === 'supervisor' && type === 'feedback') {
    return push({ ...prev, steps: finishRunning(prev.steps) },
      { agent: 'Supervisor', title: 'Sending fixes to the Planner', detail: p.repair ? `Redo ${String(p.repair).replace(/^meal\(s\)\s*/i, '').replace(/^day\(s\)\s*/i, 'day ')}` : 'Only the meals that need a change', status: 'done' });
  }
  if (from === 'supervisor' && type === 'approve' && to === 'user') {
    return push({ ...prev, steps: finishRunning(prev.steps) },
      { agent: 'Supervisor', title: prev.kind === 'edit' ? 'Plan updated' : 'Draft plan saved', detail: 'Ready for you to review', status: 'done' });
  }
  if (from === 'supervisor' && type === 'escalate') {
    return push({ ...prev, steps: finishRunning(prev.steps, 'issue') },
      { agent: 'Supervisor', title: 'Needs your input', detail: "Some issues couldn't be fixed automatically", status: 'issue' });
  }
  if (from === 'supervisor' && type === 'error') {
    return { ...prev, steps: finishRunning(prev.steps, 'failed') };
  }
  return prev;
}
