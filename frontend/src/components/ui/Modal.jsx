import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Button, IconButton } from './Primitives';

/**
 * An accessible dialog.
 *
 * Handles the things a hand-rolled overlay usually forgets: Escape to close,
 * a focus trap so Tab cannot wander behind the dialog, focus restored to the
 * element that opened it, `aria-modal` semantics, and a scroll lock on the
 * page underneath.
 */
export function Modal({ title, description, onClose, children, footer, size = 'md' }) {
  const panelRef = useRef(null);
  const previouslyFocused = useRef(null);
  const titleId = useId();

  useEffect(() => {
    previouslyFocused.current = document.activeElement;
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';
    // Focus the first control inside, or the panel itself.
    const first = panelRef.current?.querySelector(
      'input, select, textarea, button, [href], [tabindex]:not([tabindex="-1"])',
    );
    (first || panelRef.current)?.focus();
    return () => {
      document.body.style.overflow = overflow;
      previouslyFocused.current?.focus?.();
    };
  }, []);

  const onKeyDown = useCallback((e) => {
    if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
    if (e.key !== 'Tab') return;
    const focusable = panelRef.current?.querySelectorAll(
      'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), '
      + 'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}>
      <div
        ref={panelRef}
        className={`modal modal--${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <header className="modal__head">
          <div>
            <h2 className="modal__title" id={titleId}>{title}</h2>
            {description && <p className="modal__desc">{description}</p>}
          </div>
          <IconButton label="Close" onClick={onClose} icon={
            <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor"
                strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          } />
        </header>
        <div className="modal__body">{children}</div>
        {footer && <footer className="modal__foot">{footer}</footer>}
      </div>
    </div>
  );
}

/**
 * Confirmation for destructive operations.
 *
 * `requireTyping` forces the user to type an exact phrase before the action
 * unlocks. Used for things like the demo reset, which drops every table --
 * a single mis-click should not be able to do that.
 */
export function ConfirmDialog({
  title, description, confirmLabel = 'Confirm', tone = 'danger',
  requireTyping, onConfirm, onClose, busy, error, children,
}) {
  const [typed, setTyped] = useState('');
  const unlocked = !requireTyping || typed.trim() === requireTyping;

  return (
    <Modal
      title={title}
      description={description}
      onClose={onClose}
      size="sm"
      footer={(
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={onConfirm}
            busy={busy}
            disabled={!unlocked}
          >
            {confirmLabel}
          </Button>
        </>
      )}
    >
      {children}
      {requireTyping && (
        <label className="field">
          <span className="field__label">
            Type <code>{requireTyping}</code> to confirm
          </span>
          <input
            className="field__input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            spellCheck="false"
          />
        </label>
      )}
      {error && <p className="field__error" role="alert">{error.fullMessage || error.message}</p>}
    </Modal>
  );
}
