/**
 * Loading, empty and error states.
 *
 * These are separate components because the three are genuinely different
 * messages and must never be collapsed into one another. Rendering an empty
 * table when a request failed is the specific bug this app had: five call
 * sites did not check res.ok, so an error body produced `undefined` data and
 * the mentor was shown "0 students flagged" during an outage.
 */
import { Button } from './Primitives';
import { ErrorKind } from '../../services/apiClient';

/* ------------------------------------------------------------ Loading ---- */
export function LoadingState({ label = 'Loading…', rows = 0 }) {
  // A skeleton when we know the shape, a spinner when we do not.
  if (rows > 0) {
    return (
      <div className="skeleton-list" role="status" aria-live="polite" aria-label={label}>
        {Array.from({ length: rows }).map((_, i) => (
          <div className="skeleton-row" key={i} aria-hidden="true">
            <span className="skeleton skeleton--dot" />
            <span className="skeleton skeleton--line" />
            <span className="skeleton skeleton--pill" />
          </div>
        ))}
        <span className="sr-only">{label}</span>
      </div>
    );
  }
  return (
    <div className="state state--loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p className="state__text">{label}</p>
    </div>
  );
}

/** Full-screen variant, for the initial session check. */
export function LoadingScreen({ label = 'Loading Sahay…' }) {
  return (
    <div className="loading-screen" role="status" aria-live="polite">
      <span className="spinner spinner--lg" aria-hidden="true" />
      <p className="state__text">{label}</p>
    </div>
  );
}

/* -------------------------------------------------------------- Empty ---- */
/**
 * Genuinely no data. `title` should say what is absent and, where it matters,
 * why -- "no students are flagged this week" is good news and should not look
 * like a failure.
 */
export function EmptyState({ title, description, action, tone = 'neutral', icon }) {
  return (
    <div className={`state state--empty state--${tone}`}>
      {icon && <div className="state__icon" aria-hidden="true">{icon}</div>}
      <p className="state__title">{title}</p>
      {description && <p className="state__text">{description}</p>}
      {action && <div className="state__action">{action}</div>}
    </div>
  );
}

/* -------------------------------------------------------------- Error ---- */
/**
 * A failure, with the backend's own message. The error envelope carries a
 * code, a message and sometimes a hint and per-field detail; all of it is
 * shown rather than replaced with a generic string, because "the database
 * rejected this request" and "your session ended" need different responses
 * from the user.
 */
export function ErrorState({ error, onRetry, compact = false }) {
  if (!error) return null;
  const isNetwork = error.kind === ErrorKind.NETWORK;
  const isAuth = error.kind === ErrorKind.UNAUTHENTICATED;

  return (
    <div className={`state state--error ${compact ? 'state--compact' : ''}`}
      role="alert" aria-live="assertive">
      <div className="state__error-head">
        <svg viewBox="0 0 20 20" className="state__error-icon" aria-hidden="true">
          <circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M10 5.5v5.5M10 14h.01" stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" />
        </svg>
        <p className="state__title">
          {isNetwork ? 'Cannot reach the server'
            : isAuth ? 'Session ended'
              : 'Something went wrong'}
        </p>
      </div>
      <p className="state__text">{error.message}</p>
      {error.hint && <p className="state__hint">{error.hint}</p>}
      {Array.isArray(error.fields) && error.fields.length > 0 && (
        <ul className="state__fields">
          {error.fields.map((f, i) => (
            <li key={i}><code>{f.field}</code> {f.message}</li>
          ))}
        </ul>
      )}
      {onRetry && !isAuth && (
        <div className="state__action">
          <Button variant="secondary" size="sm" onClick={onRetry}>Try again</Button>
        </div>
      )}
    </div>
  );
}

/**
 * The three-way switch every data view needs.
 *
 * Order matters: error before empty, so a failed request is never rendered as
 * "nothing here".
 */
export function AsyncBoundary({
  loading, error, isEmpty, onRetry, loadingLabel, skeletonRows = 0,
  empty, children,
}) {
  if (loading) return <LoadingState label={loadingLabel} rows={skeletonRows} />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isEmpty) return empty || <EmptyState title="Nothing to show yet" />;
  return children;
}
