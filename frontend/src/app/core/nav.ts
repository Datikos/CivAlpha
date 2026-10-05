import { IconName } from '../shared/icon';

export interface NavLink {
  path: string;
  label: string;
  icon: IconName;
  exact: boolean;
  /** Extra words the command palette matches on. */
  keywords?: string;
}

export interface NavGroup {
  label: string;
  links: NavLink[];
}

/** Main navigation, grouped; drives the sidebar and the command palette. */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Forecasts',
    links: [
      {
        path: '/',
        label: 'Dashboard',
        icon: 'grid',
        exact: true,
        keywords: 'home overview today changes summary',
      },
      {
        path: '/forecasts',
        label: 'Current forecasts',
        icon: 'pulse',
        exact: true,
        keywords: 'latest probability table',
      },
      {
        path: '/forecasts/history',
        label: 'Forecast history',
        icon: 'history',
        exact: false,
        keywords: 'past versions',
      },
      {
        path: '/accuracy',
        label: 'Accuracy',
        icon: 'target',
        exact: false,
        keywords: 'calibration brier evaluation',
      },
    ],
  },
  {
    label: 'Research',
    links: [
      {
        path: '/companies',
        label: 'Companies',
        icon: 'building',
        exact: false,
        keywords: 'stocks tickers filings',
      },
      {
        path: '/compare',
        label: 'Compare',
        icon: 'columns',
        exact: false,
        keywords: 'side by side versus companies strategies',
      },
      {
        path: '/events',
        label: 'Policy events',
        icon: 'landmark',
        exact: false,
        keywords: 'tariff monetary politics',
      },
    ],
  },
  {
    label: 'Strategy',
    links: [
      {
        path: '/strategies',
        label: 'Strategy lab',
        icon: 'flask',
        exact: false,
        keywords: 'backtest',
      },
      {
        path: '/decisions',
        label: 'AI decisions',
        icon: 'sparkles',
        exact: false,
        keywords: 'llm trades',
      },
      {
        path: '/timemachine',
        label: 'Time machine',
        icon: 'hourglass',
        exact: false,
        keywords: 'replay point in time',
      },
      {
        path: '/doublers',
        label: 'Doubler study',
        icon: 'arrow',
        exact: false,
        keywords: 'double 100% high risk screen speculative',
      },
    ],
  },
  {
    label: 'Data',
    links: [
      {
        path: '/universe',
        label: 'Universe',
        icon: 'globe',
        exact: false,
        keywords: 'add company symbols',
      },
      {
        path: '/admin',
        label: 'Data & pipeline',
        icon: 'database',
        exact: false,
        keywords: 'admin jobs sync ingest',
      },
    ],
  },
  {
    label: 'Help',
    links: [
      {
        path: '/guide',
        label: 'Guide',
        icon: 'book',
        exact: false,
        keywords: 'help manual docs documentation how to tutorial glossary faq shortcuts',
      },
    ],
  },
];
