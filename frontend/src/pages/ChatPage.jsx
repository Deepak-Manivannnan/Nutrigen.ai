import React, { useState, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Send, Trash2, ChevronRight } from 'lucide-react';
import { useApp } from '../context/AppContext';
import {
  sendChatMessage,
  fetchPlanById,
  adaptBackendPlan,
  adaptBackendGrocery,
} from '../utils/api';

const SUGGESTIONS = [
  'Plan 7 days — 1,800 calories',
  'Plan tomorrow (vegetarian)',
  'Create a grocery list',
  'Plan 5 days for weight loss',
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

  // Inline plan links [Label](plan://<id>) render as tappable buttons that
  // jump straight to the plan in the Plans tab.
  const linkRegex = /\[([^\]]+)\]\(plan:\/\/(\d+)\)/g;
  let lastIndex = 0;
  let match;
  let foundLink = false;

  while ((match = linkRegex.exec(str)) !== null) {
    foundLink = true;
    if (match.index > lastIndex) {
      pushFormatted(str.slice(lastIndex, match.index));
    }
    const label = match[1];
    const planId = Number(match[2]);
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
    lastIndex = linkRegex.lastIndex;
  }

  if (!foundLink) {
    pushFormatted(str);
  } else if (lastIndex < str.length) {
    pushFormatted(str.slice(lastIndex));
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
        const bulletMatch = line.match(/^(\s*[-•*]\s+)(.*)$/);
        if (bulletMatch) {
          return (
            <div key={idx} style={{ display: 'flex', gap: '0.4rem', margin: '0.2rem 0' }}>
              <span style={{ color: 'var(--green-600)', fontWeight: 600 }}>•</span>
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

function MessageBubble({ msg, onAction, onQuickOption, onPlanLink, userInitial = 'U', showQuickOptions = true }) {
  const isUser = msg.role === 'user';
  const initials = isUser ? userInitial : '🌿';

  function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  return (
    <div className={`message-row${isUser ? ' user' : ''}`}>
      <div className={`message-avatar ${isUser ? 'user' : 'ai'}`}>{initials}</div>
      <div style={{ maxWidth: '85%' }}>
        <div className={`message-bubble ${isUser ? 'user' : 'ai'}`}>
          <FormattedMessage text={msg.content} onPlanLink={onPlanLink} />
          {msg.actionType && (
            <button className="chat-action-btn" onClick={() => onAction && onAction(msg)} id={`chat-action-${msg.id}`}>
              {msg.actionLabel} <ChevronRight size={13} />
            </button>
          )}
        </div>
        {showQuickOptions && msg.quickOptions && msg.quickOptions.length > 0 && (
          <div className="chat-quick-options" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '8px' }}>
            {msg.quickOptions.map((opt, i) => (
              <button
                key={i}
                type="button"
                className="btn btn-outline btn-xs"
                style={{
                  borderRadius: '16px',
                  fontSize: '11.5px',
                  padding: '4px 12px',
                  fontWeight: 500,
                  background: opt.value.toLowerCase().includes('delete') ? '#fef2f2' : '#ffffff',
                  borderColor: opt.value.toLowerCase().includes('delete') ? '#fca5a5' : 'var(--green-400)',
                  color: opt.value.toLowerCase().includes('delete') ? '#dc2626' : 'var(--green-700)',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
                onClick={() => onQuickOption && onQuickOption(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
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

    addMessage({ role: 'user', content: text });
    setInput('');
    setIsAgentRunning(true);
    setChatStatus('Nutrigen is typing...');

    const taskId = `t-${Math.random().toString(16).slice(2, 10)}`;

    try {
      const historyPayload = messages.slice(-6).map(m => ({
        role: m.role,
        content: m.content,
      }));
      const chatRes = await sendChatMessage(profile?.id || 1, text, taskId, historyPayload, pendingWorkflow);

      setChatStatus(null);
      setIsAgentRunning(false);

      // Keep the pending conflict context so a follow-up answer resolves it
      // (context-level, survives navigating to the Plans tab and back).
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
      if (chatRes.plan_id && (chatRes.intent === 'new_plan' || chatRes.intent === 'edit_meal' || chatRes.intent === 'approve_plan')) {
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

      addMessage({
        role: 'assistant',
        content: chatRes.response || 'How can I assist you further?',
        actionType: createdPlan ? 'view_plan' : null,
        actionLabel: createdPlan ? 'View Meal Plan →' : null,
        actionTarget: createdPlan ? 'plans' : null,
        planId: createdPlan?.id,
        quickOptions: chatRes.quick_options || null,
      });
    } catch (err) {
      console.warn('Backend chat failed:', err);
      setIsAgentRunning(false);
      setChatStatus(null);
      addMessage({
        role: 'assistant',
        content: "I'm having trouble connecting to the server. Please try again in a moment.",
      });
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleSuggestion(text) {
    setInput(text);
    inputRef.current?.focus();
  }

  function handleAction(msg) {
    if (msg.actionTarget === 'plans') navigate('/app/plans');
    else if (msg.actionTarget === 'grocery') navigate('/app/grocery');
  }

  // Quick options either send a chat message, or — for plan shortcuts emitted by
  // the "list my plans" response — navigate straight to that plan in the Plans tab.
  function handleQuickOption(value) {
    const m = typeof value === 'string' && value.match(/^__open_plan__:(\d+)$/);
    if (m) {
      navigate('/app/plans', { state: { focusPlanId: Number(m[1]) } });
      return;
    }
    handleSend(value);
  }

  // Inline plan links inside message text ([Name](plan://id)) jump straight to
  // that plan in the Plans tab.
  function handlePlanLink(planId) {
    if (!planId) return;
    navigate('/app/plans', { state: { focusPlanId: Number(planId) } });
  }

  // Quick options belong to the latest assistant message only: once the user
  // replies or picks an option, older options disappear.
  const lastAssistantId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') return messages[i].id;
    }
    return null;
  })();

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
                <span style={{ fontSize: 26 }}>🌿</span>
              </div>
              <h2>How can I help with<br />your meal plan today?</h2>
              <p>Tell me what you'd like to plan.</p>
              <div className="chat-suggestions">
                {SUGGESTIONS.map((s, i) => (
                  <button key={i} className="chat-suggestion-btn" onClick={() => handleSuggestion(s)} id={`suggestion-${i}`}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map(msg => (
                <MessageBubble
                  key={msg.id}
                  msg={msg}
                  onAction={handleAction}
                  onQuickOption={handleQuickOption}
                  onPlanLink={handlePlanLink}
                  showQuickOptions={msg.role === 'assistant' && msg.id === lastAssistantId}
                  userInitial={userInitial}
                />
              ))}
              {chatStatus && <StatusIndicator text={chatStatus} />}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* Input */}
        <div className="chat-input-area">
          <div className="chat-input-wrapper">
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
