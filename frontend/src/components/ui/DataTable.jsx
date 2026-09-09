/**
 * A table for operational data.
 *
 * Rows can be activated with the mouse or the keyboard (Enter / Space), which
 * matters because the student directory is the main way into a record and a
 * click-only row is unreachable without a pointer.
 *
 * Below the mobile breakpoint the same data renders as a stacked card list --
 * each cell keeps its column name as a label, which a horizontally scrolling
 * table cannot do legibly on a phone.
 */
import { cx } from './Primitives';

export function DataTable({
  columns, rows, getRowKey, onRowActivate, caption,
  emptyState, footer, dense = false,
}) {
  if (!rows || rows.length === 0) return emptyState || null;

  const activate = (row) => onRowActivate && onRowActivate(row);

  return (
    <div className="table-wrap">
      <table className={cx('table', dense && 'table--dense')}>
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cx(c.align && `is-${c.align}`, c.hideOnMobile && 'hide-sm')}
                style={c.width ? { width: c.width } : undefined}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={getRowKey(row)}
              className={cx(onRowActivate && 'is-activatable')}
              tabIndex={onRowActivate ? 0 : undefined}
              role={onRowActivate ? 'button' : undefined}
              aria-label={onRowActivate ? (row.name || getRowKey(row)) : undefined}
              onClick={onRowActivate ? () => activate(row) : undefined}
              onKeyDown={onRowActivate ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  activate(row);
                }
              } : undefined}
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  data-label={typeof c.header === 'string' ? c.header : undefined}
                  className={cx(c.align && `is-${c.align}`, c.hideOnMobile && 'hide-sm')}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {footer}
    </div>
  );
}

/**
 * Pagination that reflects the server's own paging.
 *
 * The API returns {total, page, page_size, pages}; this renders those rather
 * than counting rows client-side, so the directory never has to load the whole
 * institution to know how big it is.
 */
export function Pagination({ page, pages, total, pageSize, onPage, label = 'records' }) {
  if (!pages || pages <= 1) {
    return total ? (
      <p className="pagination__summary">{total.toLocaleString()} {label}</p>
    ) : null;
  }
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  return (
    <nav className="pagination" aria-label="Pagination">
      <p className="pagination__summary">
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()} {label}
      </p>
      <div className="pagination__controls">
        <button
          type="button"
          className="pagination__btn"
          onClick={() => onPage(page - 1)}
          disabled={page <= 1}
        >
          Previous
        </button>
        <span className="pagination__page" aria-live="polite">
          Page {page} of {pages}
        </span>
        <button
          type="button"
          className="pagination__btn"
          onClick={() => onPage(page + 1)}
          disabled={page >= pages}
        >
          Next
        </button>
      </div>
    </nav>
  );
}
