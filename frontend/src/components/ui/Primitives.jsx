/**
 * Shared UI primitives.
 *
 * These exist to remove the 175 inline `style={{...}}` blocks the previous
 * single-file app carried, and to make spacing, colour and focus behaviour
 * consistent. Styling lives in styles/components.css against the tokens
 * already defined in index.css.
 */
import { forwardRef } from 'react';

function cx(...parts) {
  return parts.filter(Boolean).join(' ');
}

/* ------------------------------------------------------------- Button ---- */
export const Button = forwardRef(function Button(
  { variant = 'secondary', size = 'md', busy = false, disabled,
    icon, children, className, ...rest }, ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={cx('btn', `btn--${variant}`, `btn--${size}`, busy && 'is-busy', className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy && <span className="btn__spinner" aria-hidden="true" />}
      {!busy && icon}
      {children}
    </button>
  );
});

/**
 * A button whose only content is an icon. `label` is required and becomes the
 * accessible name -- an icon-only control with no label is invisible to a
 * screen reader.
 */
export function IconButton({ label, icon, className, ...rest }) {
  return (
    <button type="button" className={cx('icon-btn', className)}
      aria-label={label} title={label} {...rest}>
      {icon}
    </button>
  );
}

/* -------------------------------------------------------------- Badge ---- */
export function Badge({ tone = 'neutral', children, className, ...rest }) {
  return (
    <span className={cx('badge', `badge--${tone}`, className)} {...rest}>
      {children}
    </span>
  );
}

/* --------------------------------------------------------------- Card ---- */
export function Card({ as: Tag = 'section', pad = true, className, children, ...rest }) {
  return (
    <Tag className={cx('card', pad && 'card--pad', className)} {...rest}>
      {children}
    </Tag>
  );
}

export function SectionHeader({ title, subtitle, actions, help, id }) {
  return (
    <header className="section-header">
      <div className="section-header__text">
        <h2 className="section-header__title" id={id}>
          {title}
          {help && <HelpTip text={help} />}
        </h2>
        {subtitle && <p className="section-header__sub">{subtitle}</p>}
      </div>
      {actions && <div className="section-header__actions">{actions}</div>}
    </header>
  );
}

/* ------------------------------------------------------------ HelpTip ---- */
/**
 * A small "?" that explains a term. Uses the native title attribute plus an
 * accessible name, so it works without JS and is reachable by keyboard.
 */
export function HelpTip({ text }) {
  return (
    <button type="button" className="help-tip" title={text}
      aria-label={text} onClick={(e) => e.preventDefault()}>
      ?
    </button>
  );
}

/* ------------------------------------------------------------- Metric ---- */
/**
 * One number with a label. `hint` explains what it means; `state` lets a
 * caller say "not available" rather than rendering a misleading 0.
 */
export function Metric({ label, value, sub, tone = 'default', hint, onClick }) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      className={cx('metric', `metric--${tone}`, onClick && 'metric--clickable')}
      onClick={onClick}
      type={onClick ? 'button' : undefined}
    >
      <span className="metric__label">
        {label}
        {hint && <HelpTip text={hint} />}
      </span>
      <span className="metric__value">{value}</span>
      {sub && <span className="metric__sub">{sub}</span>}
    </Tag>
  );
}

/* -------------------------------------------------------- ProgressBar ---- */
/**
 * A proportion bar. `max` is explicit because a component's points are only
 * meaningful against its own cap.
 */
export function ProgressBar({ value, max, tone = 'accent', label }) {
  const safeMax = typeof max === 'number' && max > 0 ? max : null;
  const safeVal = typeof value === 'number' && Number.isFinite(value) ? value : null;
  const pctFilled = safeMax && safeVal !== null
    ? Math.max(0, Math.min(100, (safeVal / safeMax) * 100))
    : 0;
  return (
    <div
      className={cx('progress', `progress--${tone}`)}
      role="img"
      aria-label={label || (safeMax ? `${safeVal} of ${safeMax}` : 'no data')}
    >
      <div className="progress__fill" style={{ width: `${pctFilled}%` }} />
    </div>
  );
}

/* --------------------------------------------------------- SearchInput --- */
export function SearchInput({ value, onChange, placeholder = 'Search…', label }) {
  return (
    <div className="search">
      <svg className="search__icon" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <path d="M11 11l4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        className="search__input"
        value={value}
        aria-label={label || placeholder}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- Tabs --- */
export function Tabs({ tabs, active, onChange, ariaLabel = 'Sections' }) {
  return (
    <div className="tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          type="button"
          id={`tab-${t.id}`}
          aria-selected={active === t.id}
          aria-controls={`panel-${t.id}`}
          className={cx('tabs__tab', active === t.id && 'is-active')}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {typeof t.count === 'number' && <span className="tabs__count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ id, active, children }) {
  if (id !== active) return null;
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} tabIndex={-1}>
      {children}
    </div>
  );
}

/* ----------------------------------------------------------- FilterBar --- */
export function FilterBar({ children, className }) {
  return <div className={cx('filter-bar', className)}>{children}</div>;
}

export function Select({ value, onChange, options, label, id }) {
  return (
    <div className="select">
      {label && <label className="select__label" htmlFor={id}>{label}</label>}
      <select
        id={id}
        className="select__input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

export { cx };
