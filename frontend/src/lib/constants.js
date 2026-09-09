/**
 * Shared constants.
 *
 * Anything that was previously repeated inside components lives here, so that
 * a band colour or a nav label is defined once. Nothing in this file is a
 * metric: every number the UI displays comes from the API.
 */

/**
 * Risk bands, exactly as the backend spells them (risk_engine._band returns
 * "Low" | "Medium" | "High"). `tone` maps to the semantic colour tokens in
 * index.css; `text` is what a human reads.
 *
 * Colour is never the only carrier of this distinction -- every RiskBadge also
 * prints the band name, which is what makes the palette safe for a red-green
 * colourblind mentor.
 */
export const BANDS = {
  High: { text: 'High risk', tone: 'danger', order: 0 },
  Medium: { text: 'Medium risk', tone: 'warning', order: 1 },
  Low: { text: 'Low risk', tone: 'success', order: 2 },
};

export const BAND_ORDER = ['High', 'Medium', 'Low'];

export function bandMeta(band) {
  return BANDS[band] || { text: 'Not scored', tone: 'neutral', order: 3 };
}

/** Primary navigation. `staffOnly` mirrors the API's require_mentor routes. */
export const NAV = [
  { id: 'worklist', label: 'Worklist', icon: 'clipboard',
    hint: 'Students needing attention this week' },
  { id: 'students', label: 'Students', icon: 'users',
    hint: 'The full directory' },
  { id: 'analytics', label: 'Analytics', icon: 'chart',
    hint: 'Outcomes, fairness and coverage' },
  { id: 'model', label: 'Model', icon: 'bolt',
    hint: 'What the model does and does not do' },
  { id: 'admin', label: 'Data & admin', icon: 'settings', staffOnly: true,
    hint: 'Import, refresh and configuration' },
];

/** Risk filter options for the directory. Values match the API's `risk` param. */
export const RISK_FILTERS = [
  { value: 'all', label: 'All students' },
  { value: 'at_risk', label: 'At risk (Medium + High)' },
];

/**
 * Intervention outcomes accepted by POST /api/interventions/{id}/outcome.
 * Kept in sync with service.close_intervention's validation.
 */
export const OUTCOMES = [
  { value: 'improved', label: 'Improved', tone: 'success' },
  { value: 'unchanged', label: 'No change', tone: 'warning' },
  { value: 'worsened', label: 'Worsened', tone: 'danger' },
  { value: 'unreachable', label: 'Could not reach', tone: 'neutral' },
];

/** Page size for the directory. The API caps this at 200. */
export const PAGE_SIZE = 50;
