import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Copy,
  Download,
  Search,
} from 'lucide-react';
import { useApp } from '../context/AppContext';
import { generateDemoGrocery } from '../utils/workflowSimulator';

export default function GroceryPage() {
  const navigate = useNavigate();
  const { groceryList, setGrocery, addToast } = useApp();
  const [searchQuery, setSearchQuery] = useState('');

  function handleCopyList() {
    if (!groceryList) return;
    const lines = [`Nutrigen.ai Grocery List — ${groceryList.duration || 7} Days`];
    lines.push(`Total Estimated Cost: ${groceryList.currency || '₹'}${groceryList.total}\n`);

    Object.entries(groceryList.categories || {}).forEach(([cat, items]) => {
      lines.push(`[${cat.toUpperCase()}]`);
      items.forEach(i => {
        lines.push(`• ${i.name} — ${i.qty} (${groceryList.currency || '₹'}${i.price})`);
      });
      lines.push('');
    });

    navigator.clipboard?.writeText(lines.join('\n'));
    addToast('Grocery list copied to clipboard!', 'success');
  }

  function handleDownloadCSV() {
    if (!groceryList || !groceryList.categories) return;

    const headers = ['Item', 'Category', 'Quantity', 'Estimated Cost (₹)', 'Source'];
    const rows = [];

    Object.entries(groceryList.categories).forEach(([category, items]) => {
      items.forEach(item => {
        const sourceLabel =
          item.source === 'mandi_api'
            ? 'Mandi Benchmark'
            : item.source === 'baseline_cache'
              ? 'Retail Market'
              : 'AI Calibration';

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
    link.setAttribute('download', `nutrigen_grocery_list_${groceryList.duration || 7}days.csv`);
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

  if (!groceryList || !groceryList.categories) {
    return (
      <div className="page-content" id="grocery-page">
        <div className="page-header">
          <h1 className="page-title">Grocery List</h1>
          <p className="page-subtitle">Itemized ingredients and estimated costs based on your approved meal plan.</p>
        </div>

        <div className="card empty-state" style={{ padding: '60px var(--space-6)' }}>
          <div className="empty-icon">🛒</div>
          <h2 className="empty-title">No grocery list generated yet</h2>
          <p className="empty-desc">
            Your grocery list is automatically created once you approve a meal plan in the Plans tab or request one in Chat.
          </p>
          <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
            <button className="btn btn-primary" onClick={() => navigate('/app/plans')} id="empty-grocery-plans-btn">
              View Meal Plans
            </button>
            <button className="btn btn-outline" onClick={handleGenerateSample} id="empty-grocery-sample-btn">
              Generate Sample List
            </button>
          </div>
        </div>
      </div>
    );
  }

  const currency = groceryList.currency || '₹';
  const allCategories = Object.entries(groceryList.categories || {});

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
      </div>

      {/* Summary KPI Cards (3 Clean Cards) */}
      <div className="grocery-summary" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
        <div className="grocery-stat-card">
          <div className="grocery-stat-value" style={{ color: 'var(--green-700)' }}>
            {currency}{groceryList.total?.toLocaleString()}
          </div>
          <div className="grocery-stat-label">Estimated Total Cost</div>
        </div>

        <div className="grocery-stat-card">
          <div className="grocery-stat-value">{allCategories.length}</div>
          <div className="grocery-stat-label">Food Categories</div>
        </div>

        <div className="grocery-stat-card">
          <div className="grocery-stat-value">{groceryList.duration || 7} Days</div>
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
    </div>
  );
}

