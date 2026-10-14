import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Leaf, ShieldCheck, ShoppingCart, ChevronRight, BarChart2, CalendarDays, UtensilsCrossed, Star } from 'lucide-react';
import heroFoodImg from '../assets/hero_food.jpg';
import nutrigenLogo from '../assets/nutrigen_logo.png';

export default function LandingPage() {
  const navigate = useNavigate();

  function scrollTo(id) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
  }

  const features = [
    { icon: <UtensilsCrossed size={20} />, title: 'Personalized Meal Planning', desc: 'Plans created around your profile, goals, dietary preferences, and specific requirements — not generic templates.' },
    { icon: <BarChart2 size={20} />, title: 'Nutrition Checks', desc: 'Every meal plan is automatically verified against your daily calorie and macronutrient targets.' },
    { icon: <ShieldCheck size={20} />, title: 'Allergy Awareness', desc: 'All ingredients are checked against your allergy profile before the plan is finalized. Your safety first.' },
    { icon: <Leaf size={20} />, title: 'Personalized Preferences', desc: 'Tell us what you dislike or what you want for specific meals. We plan around your preferences.' },
    { icon: <ShoppingCart size={20} />, title: 'Grocery Planning', desc: 'Once your plan is approved, a complete grocery list is prepared with ingredients and quantities.' },
    { icon: <Star size={20} />, title: 'Estimated Cost', desc: 'See estimated grocery costs based on current market pricing data, so you can plan your budget.' },
  ];

  const steps = [
    { icon: '👤', title: '1. Create your profile', desc: 'Tell us about your goals, preferences and allergies.' },
    { icon: '💬', title: '2. Chat and plan', desc: 'Ask for a meal plan or make specific requests.' },
    { icon: '📋', title: '3. Get your meal plan', desc: 'View detailed meals with nutrition information.' },
    { icon: '🛒', title: '4. Get your grocery list', desc: 'See ingredients and estimated cost to follow the plan.' },
  ];

  return (
    <div className="landing-page">
      {/* Nav */}
      <nav className="landing-nav">
        <div className="landing-nav-inner">
          <div className="landing-nav-brand" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <img
                src={nutrigenLogo}
                alt="Nutrigen.ai Logo"
                style={{ height: 32, width: 'auto', display: 'block', objectFit: 'contain' }}
              />
              <span style={{ fontSize: 21, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1 }}>
                <span style={{ color: 'var(--green-500)' }}>Nutri</span>
                <span style={{ color: 'var(--gray-900)' }}>gen.ai</span>
              </span>
            </div>
          </div>
          <div className="landing-nav-links">
            <button className="landing-nav-link" onClick={() => scrollTo('features')}>Features</button>
            <button className="landing-nav-link" onClick={() => scrollTo('how-it-works')}>How it works</button>
            <button className="landing-nav-link" onClick={() => scrollTo('pricing')}>Pricing</button>
          </div>
          </div>
      </nav>

      {/* Hero */}
      <section className="landing-hero">
        <div className="landing-hero-grid">
          <div>
            <h1 className="hero-title">
              Personalized<br />meal plans for a<br />
              <span className="highlight">healthier you</span>
            </h1>
            <p className="hero-subtitle">
              Nutrigen.ai uses AI agents to create safe, personalized meal plans with nutrition insights,
              allergy checks and a complete grocery list — tailored to your goals and lifestyle.
            </p>
            <div className="hero-actions">
              <button className="btn btn-primary btn-lg" onClick={() => navigate('/signup')} id="hero-get-started">
                Get started<span className="btn-arrow">→</span>
              </button>
              <button className="btn btn-outline btn-lg" onClick={() => navigate('/signin')} id="hero-sign-in">
                Sign in
              </button>
            </div>
            <p className="hero-subtext">
              Create your profile in a few minutes and start planning.
            </p>
          </div>

          <div className="hero-image-area">
            <div className="hero-image-blend-wrap">
              <img
                src={heroFoodImg}
                alt="Personalized healthy meal bowl with balanced nutrition"
                className="hero-food-blend-img"
              />
            </div>
          </div>
        </div>

        {/* 3 feature cards spanning across the bottom of the hero */}
        <div className="hero-trust-row">
          <div className="hero-trust-card">
            <div className="hero-trust-circle-icon">
              <Leaf size={20} />
            </div>
            <div>
              <h3 className="hero-trust-title">Personalized for you</h3>
              <p className="hero-trust-desc">Plans based on your goals, lifestyle and preferences.</p>
            </div>
          </div>

          <div className="hero-trust-card">
            <div className="hero-trust-circle-icon">
              <ShieldCheck size={20} />
            </div>
            <div>
              <h3 className="hero-trust-title">Allergy-aware</h3>
              <p className="hero-trust-desc">We check for allergens in every plan.</p>
            </div>
          </div>

          <div className="hero-trust-card">
            <div className="hero-trust-circle-icon">
              <ShoppingCart size={20} />
            </div>
            <div>
              <h3 className="hero-trust-title">Grocery list included</h3>
              <p className="hero-trust-desc">Get ingredients and estimated cost.</p>
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <div id="features" style={{ background: 'var(--gray-50)' }}>
        <div className="landing-section">
          <div className="section-header">
            <h2 className="section-title">Everything you need</h2>
            <p className="section-subtitle">A complete AI-powered meal planning experience built around your personal health goals.</p>
          </div>
          <div className="features-grid">
            {features.map((f, i) => (
              <div key={i} className="feature-card">
                <div className="feature-icon">{f.icon}</div>
                <h3 className="feature-title">{f.title}</h3>
                <p className="feature-desc">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* How It Works */}
      <div id="how-it-works">
        <div className="landing-section">
          <div className="section-header">
            <h2 className="section-title">How it works</h2>
            <p className="section-subtitle">Create your profile, chat with our AI agents, and get meal plans with recipes and a grocery list.</p>
          </div>
          <div className="how-steps">
            {steps.map((step, i) => (
              <React.Fragment key={i}>
                <div className="how-step">
                  <div className="how-step-number">{step.icon}</div>
                  <h3 className="how-step-title">{step.title}</h3>
                  <p className="how-step-desc">{step.desc}</p>
                </div>
                {i < steps.length - 1 && (
                  <div className="how-step-arrow"><ChevronRight size={20} /></div>
                )}
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>

      {/* Pricing */}
      <div id="pricing" style={{ background: 'var(--gray-50)' }}>
        <div className="landing-section">
          <div className="section-header">
            <h2 className="section-title">About grocery estimates</h2>
            <p className="section-subtitle">Transparent pricing information for your grocery planning.</p>
          </div>
          <div className="pricing-card">
            <h3 style={{ fontSize: 'var(--text-xl)', fontWeight: 700, color: 'var(--gray-900)', marginBottom: 'var(--space-3)' }}>
              How we calculate estimated costs
            </h3>
            <p style={{ fontSize: 'var(--text-sm)', color: 'var(--gray-600)', lineHeight: 1.7 }}>
              Nutrigen.ai uses real-time market pricing data where available to estimate your grocery costs.
              For items without live pricing data, we use curated baseline price references or AI-estimated prices
              based on typical market rates.
            </p>
            <div className="pricing-info-grid">
              {[
                { label: 'Live Market Data', value: 'Used where available (Mandi prices, retail APIs)' },
                { label: 'Baseline Prices', value: 'Curated price references for common items' },
                { label: 'AI Estimates', value: 'Generated for items without pricing data — clearly labeled' },
                { label: 'Currency', value: 'Displayed in Indian Rupees (₹)' },
              ].map((item, i) => (
                <div key={i} className="pricing-info-item">
                  <div className="pricing-info-label">{item.label}</div>
                  <div className="pricing-info-value">{item.value}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* CTA Banner */}
      <div style={{ background: 'var(--green-50)', borderTop: '1px solid var(--green-200)' }}>
        <div className="landing-section" style={{ textAlign: 'center', padding: '60px var(--space-8)' }}>
          <h2 style={{ fontSize: 'var(--text-4xl)', fontWeight: 800, color: 'var(--gray-900)', marginBottom: 'var(--space-4)', letterSpacing: '-0.04em' }}>
            Start planning smarter
          </h2>
          <p style={{ fontSize: 'var(--text-md)', color: 'var(--gray-600)', marginBottom: 'var(--space-8)', maxWidth: 400, margin: '0 auto var(--space-8)' }}>
            Create your free profile in minutes and get your first personalized meal plan.
          </p>
          <button className="btn btn-primary btn-lg" onClick={() => navigate('/signup')} id="cta-get-started">
            Get started — it's free <ChevronRight size={16} />
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="landing-footer-bg">
        <div className="landing-footer">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <img
              src={nutrigenLogo}
              alt="Nutrigen.ai Logo"
              style={{ height: 24, width: 'auto', display: 'block', objectFit: 'contain' }}
            />
            <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1 }}>
              <span style={{ color: 'var(--green-500)' }}>Nutri</span>
              <span style={{ color: 'var(--gray-900)' }}>gen.ai</span>
            </span>
          </div>
          <span className="landing-footer-copy">© 2026 Nutrigen.ai · AI-powered meal planning</span>
          <span style={{ fontSize: 12, color: 'var(--gray-400)' }}>Personalized for you</span>
        </div>
      </div>
    </div>
  );
}
