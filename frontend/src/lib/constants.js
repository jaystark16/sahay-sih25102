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

/**
 * Primary navigation, with a second level.
 *
 * Sub-items are not decoration: each one is a real destination. Directory
 * children carry a `filter` that maps onto service.list_students' RISK_FILTERS,
 * and page children carry a `section` id that exists as an anchor on that
 * page. Nothing here navigates nowhere.
 *
 * `staffOnly` mirrors the API's require_mentor routes -- hiding it is a
 * convenience, not the authorisation, which stays server-side.
 */
export const NAV = [
  {
    id: 'worklist', label: 'Worklist', icon: 'clipboard',
    hint: 'Students needing attention this week',
  },
  {
    id: 'students', label: 'Students', icon: 'users',
    hint: 'The full directory',
    children: [
      { id: 'students:all', label: 'Everyone', filter: 'all' },
      { id: 'students:at_risk', label: 'At risk', filter: 'at_risk' },
      { id: 'students:high', label: 'High risk', filter: 'high' },
      { id: 'students:medium', label: 'Medium risk', filter: 'medium' },
      { id: 'students:rising', label: 'Risk rising', filter: 'rising' },
      { id: 'students:unscored', label: 'Not yet scoreable', filter: 'unscored' },
    ],
  },
  {
    id: 'analytics', label: 'Analytics', icon: 'chart',
    hint: 'Outcomes, fairness and coverage',
    children: [
      { id: 'analytics:overview', label: 'Risk distribution', section: 'sec-distribution' },
      { id: 'analytics:effect', label: 'Did actions help?', section: 'sec-effectiveness' },
      { id: 'analytics:fairness', label: 'Fairness', section: 'sec-fairness' },
    ],
  },
  {
    id: 'model', label: 'Model', icon: 'bolt',
    hint: 'What the model does and does not do',
  },
  {
    id: 'admin', label: 'Data & admin', icon: 'settings', staffOnly: true,
    hint: 'Import, refresh and configuration',
    children: [
      { id: 'admin:upload', label: 'Import a spreadsheet', section: 'sec-upload' },
      { id: 'admin:maintenance', label: 'Maintenance', section: 'sec-maintenance' },
      { id: 'admin:activity', label: 'Recent activity', section: 'sec-activity' },
    ],
  },
];

/**
 * Directory filters. Values match service.list_students' RISK_FILTERS map
 * exactly, and each one reconciles with the metric that links to it -- the
 * "rising" filter uses the same delta >= 10 threshold as the summary count,
 * so clicking 544 shows 544 students.
 */
export const RISK_FILTERS = [
  { value: 'all', label: 'All students' },
  { value: 'at_risk', label: 'At risk (Medium + High)' },
  { value: 'high', label: 'High risk only' },
  { value: 'medium', label: 'Medium risk only' },
  { value: 'low', label: 'Low risk only' },
  { value: 'rising', label: 'Risk rising' },
  { value: 'unscored', label: 'Not yet scoreable' },
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
