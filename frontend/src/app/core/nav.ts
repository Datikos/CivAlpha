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
        label: 'Current forecasts',
        icon: 'pulse',
        exact: true,
        keywords: 'home latest probability',
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
];
