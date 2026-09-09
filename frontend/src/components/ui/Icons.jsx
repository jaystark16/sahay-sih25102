/**
 * Inline icons.
 *
 * Kept as small local SVGs rather than an icon package: the app needs about a
 * dozen glyphs and a dependency for that would be weight without benefit.
 * All are aria-hidden -- every icon here sits next to a text label or inside
 * an IconButton that supplies its own accessible name.
 */
const base = {
  width: 18, height: 18, viewBox: '0 0 20 20', fill: 'none',
  stroke: 'currentColor', strokeWidth: 1.6,
  strokeLinecap: 'round', strokeLinejoin: 'round',
  'aria-hidden': 'true', focusable: 'false',
};

export const IconClipboard = (p) => (
  <svg {...base} {...p}>
    <path d="M7 4H5.5A1.5 1.5 0 004 5.5v11A1.5 1.5 0 005.5 18h9a1.5 1.5 0 001.5-1.5v-11A1.5 1.5 0 0014.5 4H13" />
    <rect x="7" y="2.5" width="6" height="3" rx="1" />
    <path d="M7.5 9.5h5M7.5 12.5h3" />
  </svg>
);

export const IconUsers = (p) => (
  <svg {...base} {...p}>
    <circle cx="7.5" cy="7" r="2.5" />
    <path d="M2.5 16c0-2.5 2.2-4 5-4s5 1.5 5 4" />
    <path d="M13.5 5.2a2.5 2.5 0 010 4.6M14.5 12.4c1.8.5 3 1.8 3 3.6" />
  </svg>
);

export const IconChart = (p) => (
  <svg {...base} {...p}>
    <path d="M3 17h14" />
    <rect x="4.5" y="10" width="2.6" height="5" rx="0.6" />
    <rect x="8.7" y="6.5" width="2.6" height="8.5" rx="0.6" />
    <rect x="12.9" y="8.5" width="2.6" height="6.5" rx="0.6" />
  </svg>
);

export const IconBolt = (p) => (
  <svg {...base} {...p}>
    <path d="M11 2.5L4.5 11h4l-.5 6.5L15 9h-4z" />
  </svg>
);

export const IconSettings = (p) => (
  <svg {...base} {...p}>
    <circle cx="10" cy="10" r="2.6" />
    <path d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4M15.3 15.3l-1.4-1.4M6.1 6.1L4.7 4.7" />
  </svg>
);

export const IconArrowLeft = (p) => (
  <svg {...base} {...p}>
    <path d="M16 10H4M9 5l-5 5 5 5" />
  </svg>
);

export const IconAlert = (p) => (
  <svg {...base} {...p}>
    <path d="M10 3.5L2.5 16.5h15L10 3.5z" />
    <path d="M10 8.5v3.5M10 14.5h.01" />
  </svg>
);

export const IconCheck = (p) => (
  <svg {...base} {...p}>
    <path d="M4 10.5l4 4 8-9" />
  </svg>
);

export const IconUpload = (p) => (
  <svg {...base} {...p}>
    <path d="M10 13V3.5M6.5 7L10 3.5 13.5 7" />
    <path d="M3.5 13v2.5A1.5 1.5 0 005 17h10a1.5 1.5 0 001.5-1.5V13" />
  </svg>
);

export const IconRefresh = (p) => (
  <svg {...base} {...p}>
    <path d="M16.5 10a6.5 6.5 0 11-2-4.7" />
    <path d="M17 3v3.5h-3.5" />
  </svg>
);

export const IconPlus = (p) => (
  <svg {...base} {...p}>
    <path d="M10 4.5v11M4.5 10h11" />
  </svg>
);

export const IconMenu = (p) => (
  <svg {...base} {...p}>
    <path d="M3.5 6h13M3.5 10h13M3.5 14h13" />
  </svg>
);

export const IconLogout = (p) => (
  <svg {...base} {...p}>
    <path d="M12.5 6V4.5A1.5 1.5 0 0011 3H5.5A1.5 1.5 0 004 4.5v11A1.5 1.5 0 005.5 17H11a1.5 1.5 0 001.5-1.5V14" />
    <path d="M8 10h9M14 7l3 3-3 3" />
  </svg>
);

export const IconTrash = (p) => (
  <svg {...base} {...p}>
    <path d="M3.5 6h13M8 3.5h4M5.5 6l.7 10A1.5 1.5 0 007.7 17.5h4.6a1.5 1.5 0 001.5-1.4l.7-10" />
    <path d="M8.5 9v5M11.5 9v5" />
  </svg>
);

export const IconBuilding = (p) => (
  <svg {...base} {...p}>
    <path d="M3.5 17.5h13M5 17.5V4.5a1 1 0 011-1h5a1 1 0 011 1v13" />
    <path d="M12 8.5h2.5a1 1 0 011 1v8" />
    <path d="M7.5 6.5h2M7.5 9.5h2M7.5 12.5h2" />
  </svg>
);

export const NAV_ICONS = {
  building: IconBuilding,
  clipboard: IconClipboard,
  users: IconUsers,
  chart: IconChart,
  bolt: IconBolt,
  settings: IconSettings,
};
