import React from 'react';
import nutrigenLogo from '../assets/nutrigen_logo.png';

export function Logo({ variant = 'full', size = 'md' }) {
  const iconHeight = size === 'sm' ? 22 : size === 'lg' ? 34 : size === 'nav' ? 26 : 26;
  const fontSize = size === 'sm' ? 15 : size === 'lg' ? 22 : size === 'nav' ? 19 : 18;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
      <img
        src={nutrigenLogo}
        alt="Nutrigen.ai Logo"
        style={{ height: iconHeight, width: 'auto', display: 'block', objectFit: 'contain' }}
      />
      {variant !== 'icon' && (
        <span style={{ fontSize, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1 }}>
          <span style={{ color: '#16a34a' }}>Nutri</span>
          <span style={{ color: '#0f172a' }}>gen.ai</span>
        </span>
      )}
    </div>
  );
}
