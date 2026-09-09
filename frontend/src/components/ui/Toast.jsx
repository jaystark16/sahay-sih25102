import { createContext, useCallback, useContext, useMemo, useState } from 'react';

/**
 * Transient confirmations and failures.
 *
 * The region is aria-live so a screen reader announces the message without
 * moving focus. Errors use assertive because a failed write is something the
 * user must hear about; successes are polite.
 */
const ToastCtx = createContext(null);
export const useToast = () => useContext(ToastCtx);

let nextId = 1;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const push = useCallback((message, { tone = 'success', timeout = 5000 } = {}) => {
    const id = nextId++;
    setToasts((t) => [...t, { id, message, tone }]);
    if (timeout) setTimeout(() => dismiss(id), timeout);
    return id;
  }, [dismiss]);

  const value = useMemo(() => ({
    push,
    success: (m, o) => push(m, { ...o, tone: 'success' }),
    error: (m, o) => push(m, { ...o, tone: 'danger', timeout: 8000 }),
    info: (m, o) => push(m, { ...o, tone: 'info' }),
    dismiss,
  }), [push, dismiss]);

  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="toast-region">
        <div aria-live="polite" aria-atomic="false">
          {toasts.filter((t) => t.tone !== 'danger').map((t) => (
            <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
          ))}
        </div>
        <div aria-live="assertive" aria-atomic="false">
          {toasts.filter((t) => t.tone === 'danger').map((t) => (
            <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
          ))}
        </div>
      </div>
    </ToastCtx.Provider>
  );
}

function ToastItem({ toast, onDismiss }) {
  return (
    <div className={`toast toast--${toast.tone}`}>
      <span className="toast__msg">{toast.message}</span>
      <button
        type="button"
        className="toast__close"
        aria-label="Dismiss notification"
        onClick={() => onDismiss(toast.id)}
      >
        ×
      </button>
    </div>
  );
}
