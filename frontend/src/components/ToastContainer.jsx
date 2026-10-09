import React from 'react';
import { useApp } from '../context/AppContext';
import { CheckCircle, AlertTriangle, Info } from 'lucide-react';

export default function ToastContainer() {
  const { toasts } = useApp();

  const icons = {
    success: <CheckCircle size={15} />,
    error: <AlertTriangle size={15} />,
    warning: <AlertTriangle size={15} />,
    default: <Info size={15} />,
  };

  return (
    <div className="toast-container" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.type}`} role="alert">
          {icons[t.type] || icons.default}
          {t.message}
        </div>
      ))}
    </div>
  );
}
