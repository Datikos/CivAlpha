import { Routes } from '@angular/router';
import { CompanyContext } from './pages/company/company-context';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    title: 'Current forecasts · CivAlpha',
    loadComponent: () => import('./pages/current-forecasts').then((m) => m.CurrentForecastsPage),
  },
  {
    path: 'companies',
    title: 'Companies · CivAlpha',
    loadComponent: () => import('./pages/companies-list').then((m) => m.CompaniesPage),
  },
  {
    path: 'companies/:symbol',
    providers: [CompanyContext],
    loadComponent: () => import('./pages/company/company-shell').then((m) => m.CompanyShell),
    children: [
      {
        path: '',
        title: 'Company · CivAlpha',
        loadComponent: () => import('./pages/company/company-overview').then((m) => m.CompanyOverview),
      },
      {
        path: 'filings',
        title: 'Filings · CivAlpha',
        loadComponent: () => import('./pages/company/company-filings').then((m) => m.CompanyFilings),
      },
      {
        path: 'exposure',
        title: 'Exposure · CivAlpha',
        loadComponent: () => import('./pages/company/company-exposure').then((m) => m.CompanyExposure),
      },
      {
        path: 'forecasts',
        title: 'Company forecasts · CivAlpha',
        loadComponent: () => import('./pages/company/company-forecasts').then((m) => m.CompanyForecasts),
      },
    ],
  },
  {
    path: 'filings/:id',
    title: 'Filing · CivAlpha',
    loadComponent: () => import('./pages/filing-detail').then((m) => m.FilingDetailPage),
  },
  {
    path: 'universe',
    title: 'Universe · CivAlpha',
    loadComponent: () => import('./pages/universe').then((m) => m.UniversePage),
  },
  {
    path: 'events',
    title: 'Policy events · CivAlpha',
    loadComponent: () => import('./pages/events-list').then((m) => m.EventsPage),
  },
  {
    path: 'events/:id',
    title: 'Event · CivAlpha',
    loadComponent: () => import('./pages/event-detail').then((m) => m.EventDetailPage),
  },
  {
    path: 'forecasts/history',
    title: 'Forecast history · CivAlpha',
    loadComponent: () => import('./pages/forecast-history').then((m) => m.ForecastHistoryPage),
  },
  {
    path: 'forecasts/:id',
    title: 'Forecast · CivAlpha',
    loadComponent: () => import('./pages/forecast-detail').then((m) => m.ForecastDetailPage),
  },
  {
    path: 'accuracy',
    title: 'Model accuracy · CivAlpha',
    loadComponent: () => import('./pages/accuracy').then((m) => m.AccuracyPage),
  },
  {
    path: 'strategies',
    title: 'Strategy lab · CivAlpha',
    loadComponent: () => import('./pages/strategies').then((m) => m.StrategiesPage),
  },
  {
    path: 'strategies/:key',
    title: 'Strategy · CivAlpha',
    loadComponent: () => import('./pages/strategy-detail').then((m) => m.StrategyDetailPage),
  },
  {
    path: 'decisions',
    title: 'AI decisions · CivAlpha',
    loadComponent: () => import('./pages/decisions').then((m) => m.DecisionsPage),
  },
  {
    path: 'timemachine',
    title: 'Time machine · CivAlpha',
    loadComponent: () => import('./pages/timemachine').then((m) => m.TimeMachinePage),
  },
  {
    path: 'doublers',
    title: 'Doubler study · CivAlpha',
    loadComponent: () => import('./pages/doublers').then((m) => m.DoublersPage),
  },
  {
    path: 'admin',
    title: 'Data & pipeline · CivAlpha',
    loadComponent: () => import('./pages/admin').then((m) => m.AdminPage),
  },
  {
    path: 'guide',
    title: 'Guide · CivAlpha',
    loadComponent: () => import('./pages/guide').then((m) => m.GuidePage),
  },
  {
    path: '**',
    title: 'Not found · CivAlpha',
    loadComponent: () => import('./pages/not-found').then((m) => m.NotFoundPage),
  },
];
