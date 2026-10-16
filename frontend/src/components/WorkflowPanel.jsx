import React from 'react';
import { useApp } from '../context/AppContext';
import { X, BarChart2, ShieldCheck, UtensilsCrossed, ShoppingBasket, Bot } from 'lucide-react';

const AGENTS = [
  { key: 'supervisor', label: 'Supervisor', desc: { pending: 'Waiting to start', running: 'Analyzing your request', completed: 'Request analyzed', warning: 'Reviewing issues' }, Icon: Bot },
  { key: 'planner', label: 'Planner', desc: { pending: 'Will create meal plan', running: 'Creating meal plan...', completed: 'Meal plan created', warning: 'Revising plan...' }, Icon: UtensilsCrossed },
];

const PARALLEL = [
  { key: 'nutrition', label: 'Nutrition', desc: { pending: 'Will check calories', running: 'Checking nutrition...', completed: 'Nutrition verified', warning: 'Issues found' }, Icon: BarChart2 },
  { key: 'allergy', label: 'Allergy', desc: { pending: 'Will check allergens', running: 'Checking allergens...', completed: 'Allergens cleared', warning: 'Allergen found' }, Icon: ShieldCheck },
];

const SHOPPER = { key: 'shopper', label: 'Shopper', desc: { pending: 'Will build grocery list', running: 'Building grocery list...', completed: 'Grocery list ready', warning: 'Pricing issue' }, Icon: ShoppingBasket };

function AgentStepUI({ agentConfig, status, isCompact = false }) {
  const { Icon, label, desc } = agentConfig;
  const description = desc[status] || desc.pending;

  return (
    <div className={`agent-step ${status}`}>
      <div className={`agent-step-icon ${status === 'pending' ? 'gray' : 'green'}`}>
        <Icon size={14} />
      </div>
      <div className="agent-step-body">
        <div className="agent-status-row">
          <span className="agent-step-name">{label}</span>
          {status === 'running' && <div className="agent-mini-spinner" />}
          {status === 'completed' && <span className="agent-check">✓</span>}
          {status === 'warning' && <span className="agent-warn">!</span>}
        </div>
        {!isCompact && <div className="agent-step-desc">{description}</div>}
      </div>
    </div>
  );
}

export default function WorkflowPanel() {
  const { workflowState, showWorkflow, setShowWorkflow, isAgentRunning } = useApp();

  if (!showWorkflow || !workflowState) return null;

  const agents = workflowState.agents || {};
  const isRunning = isAgentRunning;
  const isDone = workflowState.done;
  const phase = workflowState.phase;
  const revisionRound = workflowState.revisionRound;

  const parallelActive = agents.nutrition !== 'pending' || agents.allergy !== 'pending';

  return (
    <aside className="workflow-panel" id="workflow-panel">
      <div className="workflow-header">
        <div className="workflow-header-left">
          <span className="workflow-header-title">Agent Workflow</span>
          {isRunning && <div className="workflow-running-dot" title="Running" />}
          {isRunning && <span className="workflow-status-label">Running</span>}
          {isDone && <span style={{ fontSize: 11, color: 'var(--green-600)', fontWeight: 500 }}>Complete</span>}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => setShowWorkflow(false)} id="workflow-hide-btn">
          <X size={13} /> Hide
        </button>
      </div>

      <div className="workflow-body">
        {workflowState.request && (
          <div className="workflow-request-card">
            <div className="workflow-request-label">Your Request</div>
            <div className="workflow-request-text">{workflowState.request.slice(0, 100)}{workflowState.request.length > 100 ? '…' : ''}</div>
          </div>
        )}

        <div className="agent-steps">
          {/* Supervisor */}
          <AgentStepUI agentConfig={AGENTS[0]} status={agents.supervisor || 'pending'} />
          <div className="agent-connector" />

          {/* Planner */}
          <AgentStepUI agentConfig={AGENTS[1]} status={agents.planner || 'pending'} />

          {/* Revision badge */}
          {revisionRound && (
            <div className="revision-badge">Revision {revisionRound} of 3</div>
          )}

          {/* Parallel nutrition + allergy */}
          {parallelActive && (
            <>
              <div className="agent-connector" />
              <div className="parallel-agents">
                {PARALLEL.map(a => (
                  <AgentStepUI key={a.key} agentConfig={a} status={agents[a.key] || 'pending'} isCompact />
                ))}
              </div>
            </>
          )}

          {/* Shopper (grocery phase) */}
          {(phase === 'grocery' || phase === 'grocery_complete' || agents.shopper !== 'pending') && (
            <>
              <div className="agent-connector" />
              <AgentStepUI agentConfig={SHOPPER} status={agents.shopper || 'pending'} />
            </>
          )}
        </div>

        {isDone && (
          <div className="workflow-complete-banner">
            <div className="wf-icon">✅</div>
            <p>
              {phase === 'grocery_complete' ? 'Grocery list ready' : 'Meal plan ready'}
            </p>
          </div>
        )}

        {!isDone && (
          <div style={{ marginTop: 16, padding: '10px 12px', background: '#fff', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', fontSize: 11, color: 'var(--gray-500)', lineHeight: 1.5 }}>
            <strong style={{ color: 'var(--gray-700)' }}>ℹ️ </strong>
            This may take a moment. You can continue browsing while agents work.
          </div>
        )}
      </div>
    </aside>
  );
}
