import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';
import { CompanyContext } from './pages/company/company-context';

export const routes: Routes = [
  {
    path: '',
    canActivate: [authGuard],
    pathMatch: 'full',
    title: 'Dashboard · CivAlpha',
    loadComponent: () => import('./pages/dashboard').then((m) => m.DashboardPage),
  },
  {
    path: 'forecasts',
    canActivate: [authGuard],
    pathMatch: 'full',
    title: 'Current forecasts · CivAlpha',
    loadComponent: () => import('./pages/current-forecasts').then((m) => m.CurrentForecastsPage),
  },
  {
    path: 'compare',
    canActivate: [authGuard],
    title: 'Compare companies · CivAlpha',
    loadComponent: () => import('./pages/compare').then((m) => m.ComparePage),
  },
  {
    path: 'strategies/compare',
    canActivate: [authGuard],
    title: 'Compare strategies · CivAlpha',
    loadComponent: () => import('./pages/strategy-compare').then((m) => m.StrategyComparePage),
  },
  {
    path: 'companies',
    canActivate: [authGuard],
    title: 'Companies · CivAlpha',
    loadComponent: () => import('./pages/companies-list').then((m) => m.CompaniesPage),
  },
  {
    path: 'companies/:symbol',
    canActivate: [authGuard],
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
    canActivate: [authGuard],
    title: 'Filing · CivAlpha',
    loadComponent: () => import('./pages/filing-detail').then((m) => m.FilingDetailPage),
  },
  {
    path: 'universe',
    canActivate: [authGuard],
    data: { ownerOnly: true },
    title: 'Universe · CivAlpha',
    loadComponent: () => import('./pages/universe').then((m) => m.UniversePage),
  },
  {
    path: 'events',
    canActivate: [authGuard],
    title: 'Policy events · CivAlpha',
    loadComponent: () => import('./pages/events-list').then((m) => m.EventsPage),
  },
  {
    path: 'events/:id',
    canActivate: [authGuard],
    title: 'Event · CivAlpha',
    loadComponent: () => import('./pages/event-detail').then((m) => m.EventDetailPage),
  },
  {
    path: 'forecasts/history',
    canActivate: [authGuard],
    title: 'Forecast history · CivAlpha',
    loadComponent: () => import('./pages/forecast-history').then((m) => m.ForecastHistoryPage),
  },
  {
    path: 'forecasts/:id',
    canActivate: [authGuard],
    title: 'Forecast · CivAlpha',
    loadComponent: () => import('./pages/forecast-detail').then((m) => m.ForecastDetailPage),
  },
  {
    path: 'accuracy',
    canActivate: [authGuard],
    title: 'Model accuracy · CivAlpha',
    loadComponent: () => import('./pages/accuracy').then((m) => m.AccuracyPage),
  },
  {
    path: 'strategies',
    canActivate: [authGuard],
    title: 'Strategy lab · CivAlpha',
    loadComponent: () => import('./pages/strategies').then((m) => m.StrategiesPage),
  },
  {
    path: 'strategies/:key',
    canActivate: [authGuard],
    title: 'Strategy · CivAlpha',
    loadComponent: () => import('./pages/strategy-detail').then((m) => m.StrategyDetailPage),
  },
  {
    path: 'decisions',
    canActivate: [authGuard],
    title: 'AI decisions · CivAlpha',
    loadComponent: () => import('./pages/decisions').then((m) => m.DecisionsPage),
  },
  {
    path: 'portfolio',
    canActivate: [authGuard],
    title: 'My portfolio · CivAlpha',
    loadComponent: () => import('./pages/portfolio').then((m) => m.PortfolioPage),
  },
  {
    path: 'timemachine',
    canActivate: [authGuard],
    title: 'Time machine · CivAlpha',
    loadComponent: () => import('./pages/timemachine').then((m) => m.TimeMachinePage),
  },
  {
    path: 'signals',
    canActivate: [authGuard],
    title: 'Signal health · CivAlpha',
    loadComponent: () => import('./pages/signals').then((m) => m.SignalsPage),
  },
  {
    path: 'ablation',
    canActivate: [authGuard],
    title: 'Feature fragility · CivAlpha',
    loadComponent: () => import('./pages/ablation').then((m) => m.AblationPage),
  },
  {
    path: 'playbook',
    canActivate: [authGuard],
    title: 'Setup playbook · CivAlpha',
    loadComponent: () => import('./pages/playbook').then((m) => m.PlaybookPage),
  },
  {
    path: 'doublers',
    canActivate: [authGuard],
    title: 'Doubler study · CivAlpha',
    loadComponent: () => import('./pages/doublers').then((m) => m.DoublersPage),
  },
  {
    path: 'admin',
    canActivate: [authGuard],
    data: { ownerOnly: true },
    title: 'Data & pipeline · CivAlpha',
    loadComponent: () => import('./pages/admin').then((m) => m.AdminPage),
  },
  {
    path: 'account',
    canActivate: [authGuard],
    title: 'My account · CivAlpha',
    loadComponent: () => import('./pages/account').then((m) => m.AccountPage),
  },
  {
    path: 'access',
    canActivate: [authGuard],
    data: { ownerOnly: true },
    title: 'Access · CivAlpha',
    loadComponent: () => import('./pages/access').then((m) => m.AccessPage),
  },
  {
    path: 'login',
    title: 'Sign in · CivAlpha',
    loadComponent: () => import('./pages/login').then((m) => m.LoginPage),
  },
  {
    path: 'guide',
    canActivate: [authGuard],
    title: 'Guide · CivAlpha',
    loadComponent: () => import('./pages/guide').then((m) => m.GuidePage),
  },
  {
    path: '**',
    title: 'Not found · CivAlpha',
    loadComponent: () => import('./pages/not-found').then((m) => m.NotFoundPage),
  },
];
