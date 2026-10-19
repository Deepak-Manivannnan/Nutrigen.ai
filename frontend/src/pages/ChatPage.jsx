import React, { useState, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Send, Trash2, ChevronRight, MessageSquarePlus } from 'lucide-react';
import { useApp, loadInflight, saveInflight } from '../context/AppContext';
import AgentTraceBar from '../components/AgentTraceBar';
import {
  sendChatMessage,
  fetchChatResult,
  fetchPlanById,
  adaptBackendPlan,
  adaptBackendGrocery,
} from '../utils/api';
import nutrigenLogo from '../assets/nutrigen_logo.png';

// Delivery machinery: how the client behaves when the page reloads mid-run.
// INFLIGHT_TIMEOUT_MS â€” max wait for an orphaned run before an honest
// timeout note replaces "typingâ€¦". BOOT_GRACE_MS â€” extra wait granted on
// boot so a slow-but-alive run can still deliver after a late reload.
const INFLIGHT_TIMEOUT_MS = 10 * 60 * 1000;
const BOOT_GRACE_MS = 3 * 60 * 1000;
const RECONCILE_POLL_MS = 5000;

// Reply ids already shown, held at module scope rather than in the component.
// applyChatResponse is reached from two places - the POST that carried the
// message, and the delivery-inbox poll that collects it after a refresh - and
// both can report the same run. A ref would do for one build of the page, but
// a refresh (the very thing the inbox exists for) or a navigation away and
// back rebuilds the component and empties it with them, so the second copy of
// a reply already on screen would render again. This outlives the component.
// Bounded, because a long session on one device would otherwise grow it
// without end.
const APPLIED_TASK_IDS = new Set();
const APPLIED_TASK_CAP = 64;

/**
 * True when this run has already been shown; otherwise records it and says no.
 * The id is the backend's own task id, which is the same in both deliveries,
 * so the two copies of one reply always collide here.
 */
function takeApplied(taskId) {
  if (APPLIED_TASK_IDS.has(taskId)) return true;
  APPLIED_TASK_IDS.add(taskId);
  if (APPLIED_TASK_IDS.size > APPLIED_TASK_CAP) {
    // Set iteration is insertion order, so this drops the oldest first.
    APPLIED_TASK_IDS.delete(APPLIED_TASK_IDS.values().next().value);
  }
  return false;
}

const SUGGESTIONS = [
  'Tell me about yourself',
  'Create a meal plan',
];

function renderInlineMarkdown(str, onPlanLink) {
  if (!str) return '';
  const parts = [];
  let key = 0;

  const pushFormatted = (text) => {
    if (!text) return;
    const regex = /(\*\*.*?\*\*|\*.*?\*)/g;
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(<span key={key++}>{text.slice(lastIndex, match.index)}</span>);
      }
      const matchText = match[0];
      if (matchText.startsWith('**') && matchText.endsWith('**')) {
        parts.push(<strong key={key++}>{matchText.slice(2, -2)}</strong>);
      } else if (matchText.startsWith('*') && matchText.endsWith('*')) {
        parts.push(<em key={key++}>{matchText.slice(1, -1)}</em>);
      }
      lastIndex = regex.lastIndex;
    }

    if (lastIndex < text.length) {
      parts.push(<span key={key++}>{text.slice(lastIndex)}</span>);
    }
  };

  const pushPlanButton = (label, planId) => {
    parts.push(
      <button
        key={key++}
        type="button"
        className="chat-plan-link"
        title={`Open ${label} in Plans`}
        onClick={() => onPlanLink && onPlanLink(planId)}
      >
        {label}
      </button>
    );
  };

  // Split bare plan://<id> occurrences (not part of a [Label](...) link).
  const pushTextSegment = (text) => {
    if (!text) return;
    const bareRegex = /plan:\/\/(\d+)/g;
    let li = 0;
    let bm;
    let foundBare = false;
    while ((bm = bareRegex.exec(text)) !== null) {
      foundBare = true;
      if (bm.index > li) pushFormatted(text.slice(li, bm.index));
      pushPlanButton('Open plan', Number(bm[1]));
      li = bareRegex.lastIndex;
    }
    if (!foundBare) {
      pushFormatted(text);
    } else if (li < text.length) {
      pushFormatted(text.slice(li));
    }
  };

  // Inline plan links [Label](plan://<id>) render as tappable buttons that
  // jump straight to the plan in the Plans tab. The input is first normalized
  // so near-miss variants the model sometimes emits ("[Label] (plan://id)"
  // with a stray space) still resolve instead of showing raw markdown.
  const normalized = str.replace(/\]\s*\(plan:\/\//g, '](plan://');
  const linkRegex = /\[([^\]]+)\]\(plan:\/\/(\d+)\)/g;
  let lastIndex = 0;
  let match;
  let foundLink = false;

  while ((match = linkRegex.exec(normalized)) !== null) {
    foundLink = true;
    if (match.index > lastIndex) {
      pushTextSegment(normalized.slice(lastIndex, match.index));
    }
    pushPlanButton(match[1], Number(match[2]));
    lastIndex = linkRegex.lastIndex;
  }

  if (!foundLink) {
    pushTextSegment(normalized);
  } else if (lastIndex < normalized.length) {
    pushTextSegment(normalized.slice(lastIndex));
  }

  return parts.length > 0 ? parts : str;
}

function FormattedMessage({ text, onPlanLink }) {
  if (!text) return null;
  const lines = text.split('\n');

  return (
    <div className="chat-formatted-text">
      {lines.map((line, idx) => {
        if (!line.trim()) {
          return <div key={idx} style={{ height: '0.35rem' }} />;
        }
        const bulletMatch = line.match(/^(\s*[-â€¢*]\s+)(.*)$/);
        if (bulletMatch) {
          return (
            <div key={idx} style={{ display: 'flex', gap: '0.4rem', margin: '0.2rem 0' }}>
              <span style={{ color: 'var(--green-600)', fontWeight: 600 }}>â€¢</span>
              <span>{renderInlineMarkdown(bulletMatch[2], onPlanLink)}</span>
            </div>
          );
        }
        return (
          <div key={idx} style={{ margin: '0.15rem 0' }}>
            {renderInlineMarkdown(line, onPlanLink)}
          </div>
        );
      })}
    </div>
  );
}

function MessageBubble({ msg, onAction, onPlanLink, userInitial = 'U' }) {
  const isUser = msg.role === 'user';

  function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  return (
    <div className={`message-row${isUser ? ' user' : ''}`}>
      <div className={`message-avatar ${isUser ? 'user' : 'ai'}`}>
        {isUser ? userInitial : <img src={nutrigenLogo} alt="" className="message-avatar-logo" />}
      </div>
      <div style={{ maxWidth: '85%' }}>
        <div
          className={`message-bubble ${isUser ? 'user' : 'ai'}`}
          style={!isUser && msg.tone === 'error'
            ? { background: '#fef2f2', borderColor: '#fca5a5', color: '#991b1b' }
            : undefined}
        >
          <FormattedMessage text={msg.content} onPlanLink={onPlanLink} />
          {msg.actionType && (
            <button className="chat-action-btn" onClick={() => onAction && onAction(msg)} id={`chat-action-${msg.id}`}>
              {msg.actionLabel} <ChevronRight size={13} />
            </button>
          )}
        </div>
        <div className="message-time">{formatTime(msg.timestamp)}</div>
      </div>
    </div>
  );
}

function StatusIndicator({ text }) {
  if (!text) return null;
  return (
    <div className="status-indicator">
      <div className="status-spinner" />
      Nutrigen is typing...
    </div>
  );
}

function ClearChatConfirm({ onConfirm, onCancel, isAgentRunning }) {
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onCancel()}>
      <div className="modal confirm-dialog">
        <div className="confirm-body">
          <div className="confirm-title">Clear this conversation?</div>
          <div className="confirm-desc">Your current chat messages will be removed from this view. This doesn't affect your saved plans or grocery lists.</div>
        </div>
        <div className="confirm-footer">
          <button className="btn btn-outline btn-sm" onClick={onCancel}>Cancel</button>
          <button 
            className="btn btn-danger btn-sm" 
            onClick={onConfirm} 
            id="confirm-clear"
            disabled={isAgentRunning}
            style={isAgentRunning ? { opacity: 0.5, cursor: 'not-allowed' } : {}}
          >
            Clear chat
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ChatPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    profile, messages, addMessage, clearMessages,
    isAgentRunning, setIsAgentRunning,
    chatStatus, setChatStatus,
    pendingWorkflow, setPendingWorkflow,
    addPlan, deletePlan, refreshPlans, setGrocery, addToast,
  } = useApp();

  const [input, setInput] = useState('');
  const [showClear, setShowClear] = useState(false);
  // Task id of the swarm currently running, or null. Drives the progress bar.
  // bootTaskRef covers the resumed-after-reload case without another setter.
  const [activeTaskId, setActiveTaskId] = useState(null);
  const bootTaskRef = useRef(null);
  // Plan id from the most recent reply, sent back with the next message so the
  // backend can approve a just-created plan without asking again. A ref, not
  // state: it is only read at send time and must not trigger a re-render.
  const lastPlanId = useRef(null);
  const traceTaskId = activeTaskId || (isAgentRunning ? bootTaskRef.current : null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  // Pending conflict questions (e.g. diet/calorie) and in-progress updates live
  // in AppContext so they survive navigation between tabs. The next message is
  // then understood as an answer to them instead of a brand new request.

  const userInitial = profile?.name ? profile.name.trim()[0].toUpperCase() : 'D';

  useEffect(() => {
    if (location.state?.prefill) {
      setInput(location.state.prefill);
      setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
    }
  }, [location.state]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, chatStatus]);

  async function handleSend(textOverride) {
    const text = (typeof textOverride === 'string' ? textOverride : input).trim();
    if (!text || isAgentRunning) return;

    // No default identity: with the fake user gone, a missing profile means we
    // genuinely do not know who this is. Sending as id 1 would file the
    // conversation against the wrong account.
    const uid = profile?.id;
    if (!uid) return;

    addMessage({ role: 'user', content: text });
    setInput('');
    setIsAgentRunning(true);
    setChatStatus('Nutrigen is typing...');

    const taskId = `t-${Math.random().toString(16).slice(2, 10)}`;
    // Hands the id to the progress bar so it can subscribe to this run's
    // event stream. Cleared in applyChatResponse once the run is terminal.
    setActiveTaskId(taskId);
    // In-flight marker: if the page reloads now, boot reconciliation can
    // still collect this run's reply from the delivery inbox.
    saveInflight(uid, { taskId, sentAt: Date.now() });

    try {
      const historyPayload = messages.slice(-6).map(m => ({
        role: m.role,
        content: m.content,
      }));
      const chatRes = await sendChatMessage(uid, text, taskId, historyPayload, pendingWorkflow, lastPlanId.current);
      await applyChatResponse(chatRes);
    } catch (err) {
      console.warn('Backend chat failed:', err);
      setIsAgentRunning(false);
      setChatStatus(null);
      setActiveTaskId(null);
      const rawMsg = err?.message || '';
      const displayMsg = rawMsg && !rawMsg.includes('Failed to fetch')
        ? rawMsg
        : "I'm having trouble connecting to the server. Please try again in a moment.";
      if (rawMsg.toLowerCase().includes('does not exist')) {
        loadUserData(null);
      }
      addMessage({
        role: 'assistant',
        content: displayMsg,
      });
    }
  }

  // Applies a finished ChatResponse envelope through the single shared path â€”
  // used by live replies AND inbox deliveries alike, so a delivered reply
  // behaves indistinguishably (pending sync, plan sync, badges, buttons).
  // Always terminal: clears the inflight marker and the running flag.
  async function applyChatResponse(chatRes) {
    // Same run delivered twice - once by the POST that carried the message,
    // once by the delivery-inbox poll: show it once. The record lives outside
    // the component (takeApplied), so it still holds when the page was rebuilt
    // between the two deliveries. Only tasked replies dedup - a missing id
    // always renders, so two identical texts from two separate runs are never
    // swallowed.
    const seenKey = chatRes?.task_id || null;
    if (seenKey && takeApplied(seenKey)) return;
    setChatStatus(null);
    setIsAgentRunning(false);
    // The bar is progress for an in-flight run only; once the reply lands it
    // has nothing left to report and must not linger.
    setActiveTaskId(null);
    // NOTE: the inflight marker is deliberately NOT cleared here. See the
    // block after addMessage below.

    // Keep the pending conflict context so a follow-up answer resolves it
    // (context-level, survives navigating to the Plans tab and back).
    // A null pending from a finished run overwrites any stale saved one â€”
    // this is what neutralizes resurrected confirmations after reload.
    setPendingWorkflow(chatRes.pending_workflow || null);

    // Handle deletion response sync
    if (chatRes.deleted_plan_id) {
      deletePlan(chatRes.deleted_plan_id);
      refreshPlans();
    } else if (chatRes.intent === 'delete_plan') {
      refreshPlans();
    } else if (chatRes.intent === 'edit_meal') {
      // Refresh plans to sync grocery list clearing and draft status after edit
      refreshPlans();
    }

    let createdPlan = null;
    // Only plan creation / updates / approvals touch the Plans badge.
    // Renames refresh silently (no badge); grocery badges only on approval.
    if (chatRes.plan_id && (chatRes.intent === 'new_plan' || chatRes.intent === 'edit_meal' || chatRes.intent === 'approve_plan' || chatRes.intent === 'grocery_only')) {
      try {
        const rawPlan = await fetchPlanById(chatRes.plan_id);
        if (rawPlan) {
          createdPlan = adaptBackendPlan(rawPlan, profile);
          addPlan(createdPlan);
          if (createdPlan.grocery_list && createdPlan.status === 'approved') {
            const grocery = adaptBackendGrocery(createdPlan.grocery_list, createdPlan.days);
            if (grocery) setGrocery(grocery);
          }
        }
      } catch (planErr) {
        console.warn('Could not fetch generated plan:', planErr);
      }
    } else if (chatRes.plan_id) {
      // E.g. renames: sync the new name without lighting any badge.
      refreshPlans();
    }

    // Successful approval produced a grocery list, so point at the Grocery
    // tab; successful creation/update points at the plan itself.
    const action = createdPlan
      ? (createdPlan.status === 'approved'
        ? { type: 'view_grocery', label: 'View Grocery List →', target: 'grocery' }
        : { type: 'view_plan', label: 'View Meal Plan →', target: 'plans' })
      : null;

    addMessage({
      role: 'assistant',
      content: chatRes.response || 'How can I assist you further?',
      // Only the two destination shortcuts live under a reply. They open an
      // existing result (the plan that was just made, the grocery list that
      // approval produced) — they are not quick replies, which stay removed.
      actionType: action ? action.type : null,
      actionLabel: action ? action.label : null,
      actionTarget: action ? action.target : null,
      planId: createdPlan?.id,
      });

    // NOW the run is over as far as the user is concerned, so the marker can
    // go. It has to be this late: while the marker is present, a reload means
    // "still collecting, go and poll the inbox". Clearing it earlier - as this
    // did - opened a window where the marker was gone but the reply had not
    // reached the message list yet, so a refresh in that window found nothing
    // to poll and the reply was lost even though the server was holding it.
    // The ordering that is now guaranteed: marker present => the reply is not
    // in the list yet; reply in the list => the marker is gone. A reload can
    // therefore never show a reply twice, and can never miss one.
    if (profile?.id) saveInflight(profile.id, null);

    // Remembered for the next turn so "approve it" right after a creation
    // resolves to that plan instead of prompting for a target.
    lastPlanId.current = createdPlan?.id || null;
  }
  const applyRef = useRef(null);
  applyRef.current = applyChatResponse;

  // Boot reconciliation: a leftover inflight marker means the page reloaded
  // mid-run. Show typing, poll the delivery inbox, and either render the
  // delivered reply (indistinguishable from live) or time out honestly.
  // Send stays disabled throughout (isAgentRunning), mirroring generation.
  // NOTE: deliberately no "run once" ref guard here. React StrictMode
  // double-invokes mount effects in dev (run â†’ cleanup â†’ run); the cleanup
  // below already cancels the first poll, so the second run safely takes over.
  // A ref guard would kill the surviving run too (refs persist across the
  // double-invoke) and silently disable all boot reconciliation.
  useEffect(() => {
    const uid = profile?.id;
    if (!uid) return;
    const inf = loadInflight(uid);
    if (!inf?.taskId) return;
    let cancelled = false;
    setIsAgentRunning(true);
    setChatStatus('Nutrigen is typing...');
    // Resumed run: hand its id to the progress bar so a reload mid-swarm still
    // shows live stages rather than a bare "typingâ€¦" dot. Held in a ref rather
    // than state because this effect already sets state for the same resume,
    // and a third setter here would add a warning without adding a render â€”
    // the isAgentRunning update above already re-renders past this line.
    bootTaskRef.current = inf.taskId;
    const deadline = Math.max((inf.sentAt || Date.now()) + INFLIGHT_TIMEOUT_MS, Date.now() + BOOT_GRACE_MS);
    const poll = async () => {
      if (cancelled) return;
      if (Date.now() > deadline) {
        setIsAgentRunning(false);
        setChatStatus(null);
        saveInflight(uid, null);
        addMessage({
          role: 'assistant',
          content: "That run didn't finish before you reloaded, so I don't have its reply. Your request is still above â€” feel free to send it again.",
          tone: 'error',
        });
        return;
      }
      try {
        const delivered = await fetchChatResult(inf.taskId);
        if (cancelled) return;
        if (delivered) {
          await applyRef.current(delivered);
          return;
        }
      } catch (pollErr) {
        console.warn('Inbox poll failed, will retry:', pollErr);
      }
      if (!cancelled) setTimeout(poll, RECONCILE_POLL_MS);
    };
    poll();
    return () => { cancelled = true; };
  }, [profile?.id]);

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleSuggestionSubmit(e) {
    e.preventDefault();
    handleSend(e.nativeEvent.submitter ? e.nativeEvent.submitter.value : '');
  }

  // Destination shortcuts only: open the plan / grocery tab. Quick replies are
  // intentionally absent — the user always types their own answer.
  function handleAction(msg) {
    if (msg.actionTarget === 'plans') navigate('/app/plans');
    else if (msg.actionTarget === 'grocery') navigate('/app/grocery');
  }

  // Inline plan links inside message text ([Name](plan://id)) jump straight to
  // that plan in the Plans tab.
  function handlePlanLink(planId) {
    if (!planId) return;
    navigate('/app/plans', { state: { focusPlanId: Number(planId) } });
  }

  const hasContent = messages.length > 0 || chatStatus;

  return (
    <div className="chat-layout">
      <div className="chat-main">
        {/* Chat Header */}
        <div className="chat-header">
          <span className="chat-title">Chat</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            {hasContent && (
              <button 
                className="btn btn-ghost btn-sm" 
                onClick={() => setShowClear(true)} 
                id="clear-chat-btn"
                disabled={isAgentRunning}
                style={isAgentRunning ? { opacity: 0.5, cursor: 'not-allowed' } : {}}
              >
                <Trash2 size={13} /> Clear chat
              </button>
            )}
          </div>
        </div>

        {/* Messages */}
        <div className="chat-messages" id="chat-messages">
          {!hasContent ? (
            <div className="chat-welcome">
              <div className="chat-welcome-icon">
                <img src={nutrigenLogo} alt="" className="chat-welcome-logo" />
              </div>
              <h2>How can I help with<br />your meal plan today?</h2>
              <p>Tell me what you'd like to plan.</p>
              <form className="chat-suggestions" onSubmit={handleSuggestionSubmit}>
                {SUGGESTIONS.map((s, i) => (
                  <button key={i} type="submit" value={s} className="chat-suggestion-btn" id={`suggestion-${i}`}>
                    {s}
                  </button>
                ))}
              </form>
            </div>
          ) : (
            <>
              {messages.map(msg => (
                <MessageBubble
                  key={msg.id}
                  msg={msg}
                  onAction={handleAction}
                  onPlanLink={handlePlanLink}
                  userInitial={userInitial}
                />
              ))}
              {chatStatus && <StatusIndicator text={chatStatus} />}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* Live progress for a running swarm. Sits between the last message and
            the composer, and renders nothing once the run is terminal. */}
        <AgentTraceBar taskId={traceTaskId} active={isAgentRunning} />

        {/* Input */}
        <div className="chat-input-area">
          <div className="chat-input-wrapper">
            {/* Mobile-only. On a phone the header's Clear chat text button is
                a wide, easy-to-mis-tap target sitting in a dead strip, so the
                action moves down here beside the composer. Hidden on desktop,
                where the labelled button in the header is clearer. */}
            {hasContent && (
              <button
                className="chat-clear-btn"
                onClick={() => setShowClear(true)}
                disabled={isAgentRunning}
                aria-label="Clear chat"
                title="Clear chat"
                id="chat-clear-btn"
              >
                <MessageSquarePlus size={18} />
              </button>
            )}
            <div className="chat-input-pill">
              <textarea
                ref={inputRef}
                className="chat-input"
                placeholder="Type your message..."
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                disabled={isAgentRunning}
                id="chat-input"
              />
            </div>
            <button
              className="chat-send-btn"
              onClick={handleSend}
              disabled={!input.trim() || isAgentRunning}
              id="chat-send"
              title="Send message"
            >
              <Send size={18} />
            </button>
          </div>
        </div>
      </div>

      {showClear && (
        <ClearChatConfirm
          onConfirm={() => {
            clearMessages();
            setPendingWorkflow(null);
            setShowClear(false);
            addToast('Chat cleared', 'default');
          }}
          onCancel={() => setShowClear(false)}
          isAgentRunning={isAgentRunning}
        />
      )}
    </div>
  );
}
