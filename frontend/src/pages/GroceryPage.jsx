import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Copy,
  Download,
  Search,
} from 'lucide-react';
import { useApp } from '../context/AppContext';
import { adaptBackendGrocery } from '../utils/api';
import { generateDemoGrocery } from '../utils/workflowSimulator';

export default function GroceryPage() {
  const navigate = useNavigate();
  const { plans, activePlanId, setActivePlanId, groceryList, setGrocery, addToast } = useApp();
  const [searchQuery, setSearchQuery] = useState('');

  const plansArray = Array.isArray(plans) ? plans : [];

  // Resolve active plan and its specific grocery checklist
  const activePlan = plansArray.find(p => p.id === activePlanId) || plansArray[0] || null;
  const currentGrocery = (activePlan && activePlan.grocery_list)
    ? adaptBackendGrocery(activePlan.grocery_list, activePlan.days)
    : (activePlan ? null : groceryList);

  function handleCopyList() {
    if (!currentGrocery) return;
    const lines = [`NutriGen.ai Grocery List — ${activePlan?.name || 'Meal Plan'} (${currentGrocery.duration || activePlan?.days || 7} Days)`];
    lines.push(`Total Estimated Cost: ${currentGrocery.currency || '₹'}${currentGrocery.total}\n`);

    Object.entries(currentGrocery.categories || {}).forEach(([cat, items]) => {
      lines.push(`[${cat.toUpperCase()}]`);
      items.forEach(i => {
        lines.push(`• ${i.name} — ${i.qty} (${currentGrocery.currency || '₹'}${i.price})`);
      });
      lines.push('');
    });

    navigator.clipboard?.writeText(lines.join('\n'));
    addToast('Grocery list copied to clipboard!', 'success');
  }

  function handleDownloadCSV() {
    if (!currentGrocery || !currentGrocery.categories) return;

    const headers = ['Item', 'Category', 'Quantity', 'Estimated Cost (₹)', 'Source'];
    const rows = [];

    Object.entries(currentGrocery.categories).forEach(([category, items]) => {
      items.forEach(item => {
        const sourceLabel =
          item.source === 'mandi_api'
            ? 'Mandi Wholesale'
            : item.source === 'baseline_cache'
              ? 'Retail Market'
              : 'AI Estimation';

        rows.push([
          `"${(item.name || '').replace(/"/g, '""')}"`,
          `"${category.replace(/"/g, '""')}"`,
          `"${(item.qty || '').replace(/"/g, '""')}"`,
          item.price || 0,
          `"${sourceLabel}"`,
        ]);
      });
    });

    const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows.map(e => e.join(','))].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `nutrigen_grocery_${(activePlan?.name || 'plan').toLowerCase().replace(/\s+/g, '_')}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    addToast('Grocery list exported as CSV!', 'success');
  }

  function handleGenerateSample() {
    const demo = generateDemoGrocery();
    setGrocery(demo);
    addToast('Sample grocery list generated!', 'success');
  }

  const currency = currentGrocery?.currency || '₹';
  const allCategories = Object.entries(currentGrocery?.categories || {});

  // Filter items by search query
  const filteredCategories = allCategories
    .map(([category, items]) => {
      const filtered = items.filter(i =>
        i.name.toLowerCase().includes(searchQuery.toLowerCase())
      );
      return [category, filtered];
    })
    .filter(([, items]) => items.length > 0);

  function getBadgeForSource(source) {
    switch (source) {
      case 'mandi_api':
        return <span className="badge badge-green" title="Direct from wholesale agricultural benchmark">Mandi</span>;
      case 'baseline_cache':
        return <span className="badge badge-gray" title="Retail market baseline median">Retail</span>;
      case 'ai_estimate':
      default:
        return <span className="badge badge-warning" title="AI calibrated unit estimation">Est.</span>;
    }
  }

  return (
    <div className="page-content" id="grocery-page">
      {/* Header */}
      <div
        className="page-header"
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: 'var(--space-4)',
        }}
      >
        <div>
          <h1 className="page-title">Grocery List</h1>
          <p className="page-subtitle">
            Consolidated shopping checklist with mandi wholesale benchmarks and retail estimates.
          </p>
        </div>

        {currentGrocery && (
          <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' }}>
            <button className="btn btn-outline btn-sm" onClick={handleCopyList} id="copy-grocery-btn">
              <Copy size={14} /> Copy List
            </button>
            <button
              className="btn btn-primary btn-sm"
              onClick={handleDownloadCSV}
              id="download-csv-btn"
            >
              <Download size={14} /> Download List (CSV)
            </button>
          </div>
        )}
      </div>

      {/* Plan Selector tabs if multiple plans */}
      {plansArray.length > 1 && (
        <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-6)', overflowX: 'auto', paddingBottom: 4 }}>
          {plansArray.map(p => {
            const isSelected = p.id === (activePlan?.id);
            return (
              <button
                key={p.id}
                onClick={() => setActivePlanId(p.id)}
                className={`btn btn-sm ${isSelected ? 'btn-primary' : 'btn-outline'}`}
                style={{ borderRadius: 'var(--radius-full)' }}
              >
                {p.name} · {p.status === 'approved' ? 'Approved' : 'Draft'}
              </button>
            );
          })}
        </div>
      )}

      {/* Empty State when no approved grocery list exists */}
      {(!currentGrocery || !currentGrocery.categories || allCategories.length === 0) ? (
        <div className="card empty-state" style={{ padding: '72px var(--space-8)' }}>
          <div className="empty-icon">🛒</div>
          <h2 className="empty-title">
            {activePlan ? `"${activePlan.name}" is currently in Draft` : 'No grocery list generated yet'}
          </h2>
          <p className="empty-desc">
            {activePlan
              ? 'This meal plan is currently drafted. Click "Approve & Generate Grocery List" in the Plans tab or reply "Approve this plan" in Chat to compile its Mandi market rates.'
              : 'Your grocery list is automatically created once you approve a meal plan in the Plans tab or request one in Chat.'}
          </p>
          <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
            <button className="btn btn-primary" onClick={() => navigate('/app/plans')} id="empty-grocery-plans-btn">
              View Meal Plans
            </button>
            <button className="btn btn-outline" onClick={() => navigate('/app/chat')} id="empty-grocery-chat-btn">
              Open Chat
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* Summary KPI Cards (3 Clean Cards) */}
          <div className="grocery-summary" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
            <div className="grocery-stat-card">
              <div className="grocery-stat-value" style={{ color: 'var(--green-700)' }}>
                {currency}{currentGrocery.total?.toLocaleString()}
              </div>
              <div className="grocery-stat-label">Estimated Total Cost</div>
            </div>

            <div className="grocery-stat-card">
              <div className="grocery-stat-value">{allCategories.length}</div>
              <div className="grocery-stat-label">Food Categories</div>
            </div>

            <div className="grocery-stat-card">
              <div className="grocery-stat-value">{currentGrocery.duration || activePlan?.days || 7} Days</div>
              <div className="grocery-stat-label">Plan Duration</div>
            </div>
          </div>

      {/* Search & Filter Toolbar */}
      <div style={{ marginBottom: 'var(--space-4)', maxWidth: 360 }}>
        <div className="form-input-icon">
          <Search className="icon" size={15} />
          <input
            type="text"
            className="form-input"
            placeholder="Search ingredients..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            id="grocery-search"
          />
        </div>
      </div>

      {/* Table Wrapper */}
      <div className="grocery-table-wrapper" id="grocery-checklist">
        <div className="grocery-table-head">
          <span>Item</span>
          <span>Quantity</span>
          <span>Est. Cost</span>
          <span>Source</span>
        </div>

        {filteredCategories.map(([category, items]) => {
          const catTotal = items.reduce((sum, item) => sum + (item.price || 0), 0);
          return (
            <div key={category}>
              <div
                className="grocery-category-header"
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <span>
                  {category} ({items.length} {items.length === 1 ? 'item' : 'items'})
                </span>
                <span style={{ fontWeight: 600, color: 'var(--gray-700)' }}>
                  Subtotal: {currency}{catTotal}
                </span>
              </div>

              {items.map(item => (
                <div
                  key={item.name}
                  className="grocery-item-row"
                  style={{ background: '#fff' }}
                  id={`grocery-item-${item.name.toLowerCase().replace(/\s+/g, '-')}`}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ color: 'var(--green-600)', fontSize: '13px' }}>•</span>
                    <span
                      className="grocery-item-name"
                      style={{ color: 'var(--gray-900)', fontWeight: 500 }}
                    >
                      {item.name}
                    </span>
                  </div>

                  <div className="grocery-item-qty">{item.qty}</div>

                  <div className="grocery-item-price">
                    {currency}{item.price}
                  </div>

                  <div>{getBadgeForSource(item.source)}</div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
      </>
      )}
    </div>
  );
}

