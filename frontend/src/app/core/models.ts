// Types mirror docs/api.md (REST API v1). Fields are camelCase JSON.

export type ModelKind = 'BASELINE' | 'AUGMENTED';
export const MODEL_KINDS: readonly ModelKind[] = ['BASELINE', 'AUGMENTED'];

export interface Meta {
  demoDataPresent: boolean;
  llmEnabled: boolean;
  secMode: string;
  /** True when the backend requires X-Admin-Token for admin and write requests. */
  adminTokenRequired?: boolean;
  /** Configured sector benchmark ETFs with no prices yet; evaluation and forecasts need them. */
  missingBenchmarks?: string[];
  /** Configured automatic price provider: none | yahoo | tiingo. */
  priceProvider?: string;
  dataCutoff: string | null;
  target: string;
  disclaimers: string[];
}

// ---------- Companies ----------

export interface LatestForecastRef {
  id: number;
  probability: number;
  asOfDate: string;
}

export interface CompanySummary {
  id: number;
  symbol: string;
  name: string;
  sector: string;
  benchmarkSymbol: string;
  cik: string | null;
  isDemo: boolean;
  latestClose: number | null;
  latestCloseDate: string | null;
  latestForecasts?: Partial<Record<ModelKind, LatestForecastRef>> | null;
}

export interface TickerHistoryEntry {
  symbol: string;
  validFrom: string | null;
  validTo: string | null;
  source: string | null;
}

export interface CikHistoryEntry {
  cik: string;
  validFrom: string | null;
  validTo: string | null;
  source: string | null;
}

export interface KeyFact {
  concept: string;
  label: string;
  value: number;
  unit: string;
  periodStart: string | null;
  periodEnd: string | null;
  fiscalPeriod: string | null;
  formType: string | null;
  filedDate: string | null;
  accessionNo: string | null;
  sourceUrl: string | null;
}

export interface CompanyDetail {
  id: number;
  symbol: string;
  name: string;
  sector: string;
  benchmarkSymbol: string;
  exchange: string | null;
  cik?: string | null;
  isDemo: boolean;
  tickerHistory: TickerHistoryEntry[];
  cikHistory: CikHistoryEntry[];
  keyFacts: KeyFact[];
}

export interface PriceBar {
  date: string;
  symbol: string;
  close: number | null;
  benchmarkClose: number | null;
}

export interface CorporateAction {
  exDate: string;
  type: string;
  value: number | null;
}

export interface PriceSeries {
  symbol: string;
  benchmarkSymbol: string;
  isDemo: boolean;
  bars: PriceBar[];
  corporateActions: CorporateAction[];
}

export interface FinancialPoint {
  periodStart: string | null;
  periodEnd: string;
  fiscalPeriod: string | null;
  value: number;
  accessionNo: string | null;
  formType: string | null;
  filedDate: string | null;
  revised: boolean;
  originalValue: number | null;
  sourceUrl: string | null;
}

export interface FinancialSeries {
  concept: string;
  label: string;
  unit: string;
  points: FinancialPoint[];
}

export interface Financials {
  asOf: string;
  series: FinancialSeries[];
}

// ---------- Filings ----------

export interface Filing {
  id: number;
  accessionNo: string;
  formType: string;
  periodOfReport: string | null;
  filedDate: string | null;
  acceptedAt: string | null;
  url: string | null;
  items: string | null;
  amendsAccession: string | null;
  passageCount: number | null;
  factCount: number | null;
  isDemo: boolean;
}

export interface Passage {
  id: number;
  section: string | null;
  topic: string | null;
  text: string;
  extractionMethod: string | null;
  extractorVersion: string | null;
}

export interface FilingFact {
  id?: number;
  concept: string;
  value: number;
  unit: string | null;
  periodEnd: string | null;
  dimensions: Record<string, string> | null;
}

export interface FilingDetail extends Filing {
  companySymbol: string;
  passages: Passage[];
  facts: FilingFact[];
}

// ---------- Exposure ----------

export type ExposureBasis = 'DIRECTLY_REPORTED' | 'ESTIMATED';

export interface Exposure {
  id: number;
  targetType: string;
  targetCode: string;
  channel: string;
  share: number | null;
  basis: ExposureBasis | string;
  confidence: string | null;
  method: string | null;
  rationale: string | null;
  availableAt: string | null;
  periodEnd: string | null;
  filing: { id: number; accessionNo: string; formType: string; url: string | null } | null;
  passage: { id: number; section: string | null; text: string } | null;
  fact: {
    id: number;
    concept: string;
    value: number;
    dimensions: Record<string, string> | null;
  } | null;
  isDemo: boolean;
}

export interface ExposurePath {
  eventId: number;
  eventTitle: string;
  eventCategory: string;
  eventPublishedAt: string | null;
  targetType: string;
  targetCode: string;
  exposureId: number;
  basis: ExposureBasis | string;
  confidence: string | null;
  share: number | null;
  passageId: number | null;
  filingAccessionNo: string | null;
}

export interface ExposureResponse {
  asOf: string;
  exposures: Exposure[];
  paths: ExposurePath[];
}

// ---------- Events ----------

export type EventCategory = 'MONETARY_POLICY' | 'TRADE_TARIFF';
export const EVENT_CATEGORIES: readonly EventCategory[] = ['MONETARY_POLICY', 'TRADE_TARIFF'];

export interface PolicyEventTarget {
  targetType: string;
  targetCode: string;
  magnitude: number | null;
}

export interface PolicyEvent {
  id: number;
  category: EventCategory | string;
  eventType: string;
  title: string;
  eventDate: string | null;
  publishedAt: string | null;
  firstSeenAt: string | null;
  evidenceStatus: 'OFFICIAL' | 'NEWS_ONLY' | string;
  actorName: string | null;
  targets: PolicyEventTarget[];
  sourceCount: number;
  affectedCompanyCount: number;
  version: number;
  isDemo: boolean;
}

export interface ActorRecord {
  recordType: string;
  occurredAt: string | null;
  summary: string;
  source: { id: number; url: string | null; title: string | null } | null;
}

export interface Actor {
  id: number;
  name: string;
  actorType: string | null;
  authority: string | null;
  affiliation: string | null;
  profileNote: string | null;
  records: ActorRecord[];
}

export interface EventSource {
  id: number;
  role: string | null;
  sourceType: string | null;
  publisher: string | null;
  title: string | null;
  url: string | null;
  accessionNo: string | null;
  publishedAt: string | null;
  ingestedAt: string | null;
  version: number | null;
  contentSha256: string | null;
  documentUrl: string | null;
  isDemo: boolean;
}

export interface AffectedPath {
  targetType: string;
  targetCode: string;
  exposureId: number;
  basis: ExposureBasis | string;
  confidence: string | null;
  share: number | null;
  passageId: number | null;
}

export interface AffectedCompany {
  symbol: string;
  name: string;
  paths: AffectedPath[];
}

export interface PolicyEventDetail extends PolicyEvent {
  summary: string | null;
  attributes: Record<string, unknown> | null;
  actor: Actor | null;
  sources: EventSource[];
  affectedCompanies: AffectedCompany[];
}

export interface NewEventRequest {
  category: string;
  eventType: string;
  title: string;
  summary: string;
  eventDate: string;
  publishedAt: string;
  actorName: string;
  attributes: Record<string, unknown>;
  targets: PolicyEventTarget[];
  source: { url: string; title: string; publisher: string; role: string };
}

export interface NewEventResponse {
  event: PolicyEventDetail;
  deduplicated: boolean;
  reissuedForecastIds: number[];
}

// ---------- Forecasts ----------

export interface ForecastOutcome {
  windowEndDate: string;
  stockReturn: number;
  benchmarkReturn: number;
  excessReturn: number;
  outcome: boolean;
  brier: number;
}

export interface ForecastSummary {
  id: number;
  companyId: number;
  symbol: string;
  companyName: string;
  benchmarkSymbol: string;
  modelKind: ModelKind;
  probability: number;
  probLow: number | null;
  probHigh: number | null;
  horizonTradingDays: number;
  asOfDate: string;
  asOf: string;
  issuedAt: string;
  issueMode: 'LIVE' | 'REPLAY' | string;
  version: number;
  supersedesId: number | null;
  reason: string | null;
  isDemo: boolean;
  outcome: ForecastOutcome | null;
}

export interface Provenance {
  type: 'EVENT' | 'PASSAGE' | 'FILING' | string;
  id: number | null;
  label: string;
  url: string | null;
}

export interface Factor {
  feature: string;
  label: string;
  value: number | null;
  z: number | null;
  coefficient: number | null;
  contribution: number;
  direction: 'UP' | 'DOWN' | string;
  kind: string;
  provenance: Provenance[];
}

export interface ForecastSource {
  label: string;
  kind: 'EVENT' | 'FILING' | 'PRICES' | 'MACRO' | string;
  url: string | null;
  accessionNo: string | null;
  publishedAt: string | null;
}

export interface ModelVersion {
  id: number;
  modelKind: ModelKind;
  algorithm: string;
  trainedThrough: string | null;
  nSamples: number | null;
  featureNames: string[];
}

export interface ForecastVersionRef {
  id: number;
  version: number;
  issuedAt: string;
  probability: number;
  reason: string | null;
}

export interface ForecastDetail extends ForecastSummary {
  target: string;
  uncertaintyNote: string | null;
  features: Record<string, number> | null;
  explanation: {
    intercept: number | null;
    baseRate: number | null;
    factors: Factor[];
  } | null;
  sources: ForecastSource[];
  modelVersion: ModelVersion | null;
  versions: ForecastVersionRef[];
  contentSha256: string | null;
}

// ---------- Accuracy ----------

export interface ModelMetrics {
  n: number;
  brier: number;
  logLoss: number;
  auc: number;
  accuracy: number;
  baseRate: number;
  brierSkill: number;
}

export interface CalibrationBin {
  binLow: number;
  binHigh: number;
  meanPredicted: number | null;
  observedRate: number | null;
  count: number;
}

export interface TradingStats {
  periods: number;
  meanGross: number;
  meanNet: number;
  tStatNet: number;
  hitRate: number;
  annualizedNet: number;
  sharpeNet: number;
  avgPositions: number;
  turnoverCostPerPeriod: number;
}

export interface EvalFold {
  fold: number;
  testStart: string;
  testEnd: string;
  nTrain: number;
  nTest: number;
  brier: Partial<Record<ModelKind, number>>;
}

export interface Evaluation {
  id: number;
  runAt: string;
  dataCutoff: string | null;
  isDemo: boolean;
  config: {
    horizon: number;
    sampleEvery: number;
    embargo: number;
    foldLength: number;
    minTrainDays: number;
    costBpsPerSide: number;
  };
  metrics: Partial<Record<ModelKind, ModelMetrics>>;
  comparison: {
    brierDiff: number;
    ciLow: number;
    ciHigh: number;
    aucDiff: number | null;
    /** Folds where the augmented model had the lower Brier score, and a two-sided sign-test p-value. */
    foldsAugmentedBetter?: number;
    foldsCompared?: number;
    signTestP?: number | null;
    note: string | null;
  } | null;
  calibration: Partial<Record<ModelKind, CalibrationBin[]>>;
  trading: Partial<Record<ModelKind, Partial<TradingStats>>>;
  folds: EvalFold[];
  verdict: string | null;
}

export interface IssuedAccuracy {
  issued: number;
  resolved: number;
  brier: number | null;
  hitRate: number | null;
}

export interface AccuracyResponse {
  evaluation: Evaluation | null;
  /** LIVE-issued forecasts only (published before their outcome window opened). */
  issued: Partial<Record<ModelKind, IssuedAccuracy>> | null;
  /** Same statistics split by issue mode; REPLAY = published after its data cutoff. */
  issuedByMode?: Partial<Record<'LIVE' | 'REPLAY', Partial<Record<ModelKind, IssuedAccuracy>>>> | null;
}

// ---------- Strategy lab ----------

export type StrategyFamily = 'BENCHMARK' | 'TREND' | 'MEAN_REVERSION' | 'FUNDAMENTAL' | 'EVENT' | 'AI';

export interface StrategyMetrics {
  start: string;
  end: string;
  years: number;
  totalReturn: number | null;
  cagr: number | null;
  volatility: number | null;
  /** annualized, on returns in excess of cash */
  sharpe: number | null;
  sortino: number | null;
  maxDrawdown: number | null;
  calmar: number | null;
  /** average invested fraction of capital */
  exposure: number | null;
  beta: number | null;
  alpha: number | null;
  trades: number;
  closedTrades: number;
  winRate: number | null;
  avgHoldingDays: number | null;
  turnoverPerYear: number | null;
  costDragPerYear: number | null;
  /** annualized mean daily return minus equal-weight buy & hold, with a block-bootstrap 95% CI */
  excessReturn?: number | null;
  excessCiLow?: number | null;
  excessCiHigh?: number | null;
  informationRatio?: number | null;
  /** probability the excess Sharpe is real after testing every strategy (>= 0.95 needed) */
  deflatedSharpe?: number | null;
}

export interface EquityPoint {
  date: string;
  equity: number;
  drawdown: number;
}

export interface StrategyResult {
  strategyKey: string;
  family: StrategyFamily;
  name: string;
  description: { entry: string; exit: string; origin: string; sizing: string; trailingStop?: number };
  params: Record<string, unknown>;
  metrics: StrategyMetrics;
  equity: EquityPoint[];
  yearly: { year: number; return: number }[];
  costSensitivity: Record<string, { cagr: number | null; sharpe: number | null }>;
  verdict: string;
}

export interface StrategyRun {
  id: number;
  runAt: string;
  dataCutoff: string;
  oosStart: string;
  config: {
    costBpsPerSide: number;
    costSensitivityBps: number[];
    reference: string;
    nCandidates: number;
    execution: string;
    verdictRule: string;
    ai: Record<string, unknown>;
  };
  summary: string;
  isDemo: boolean;
}

export interface StrategiesResponse {
  run: StrategyRun | null;
  results: StrategyResult[];
}

export interface StrategyTrade {
  companyId: number | null;
  symbol: string;
  entryDate: string;
  exitDate: string | null;
  tradeReturn: number | null;
  holdingDays: number;
  entryReason: string;
  exitReason: string;
}

export interface StrategyDetailResponse {
  run: StrategyRun;
  result: StrategyResult;
  reference: { strategyKey: string; name: string; equity: EquityPoint[] } | null;
  trades: StrategyTrade[];
  tradeCount: number;
}

export type DecisionAction = 'ENTER' | 'EXIT' | 'HOLD' | 'STAY_OUT';

export interface DecisionFactor {
  feature: string;
  label: string;
  kind: string;
  value: number | null;
  median: number | null;
  /** change in probability versus the feature at its training median */
  contribution: number;
  direction: 'UP' | 'DOWN';
}

export interface AiDecision {
  id: number;
  companyId: number;
  name: string;
  symbol: string;
  asOfDate: string;
  strategyKey: string;
  action: DecisionAction;
  probability: number;
  entryP: number;
  exitP: number;
  weight: number;
  rank: number;
  factors: DecisionFactor[];
  ruleVotes: Record<string, boolean>;
  model: { algorithm: string; trainedThrough: string; nTrain: number; horizon: number; codeVersion: string };
  issuedAt: string;
  isDemo: boolean;
  explanation: string | null;
  explanationModel: string | null;
}

export interface DecisionsResponse {
  asOfDate: string | null;
  dates: string[];
  decisions: AiDecision[];
}

// ---------- Time machine ----------

export interface TmActual {
  stockReturn: number;
  benchmarkReturn: number;
  excess: number;
  beat: boolean;
  /** entered at the next close, as a trade would be */
  execReturn: number | null;
  execBenchmarkReturn: number | null;
  endDate: string;
}

export interface TmRange {
  q10: number;
  q50: number;
  q90: number;
  naiveQ10: number;
  naiveQ50: number;
  naiveQ90: number;
}

export interface TmStock {
  companyId: number;
  symbol: string;
  name: string;
  benchmarkSymbol: string;
  closeAsOf: number | null;
  /** horizon -> model kind -> P(beat the sector ETF) */
  odds?: Record<string, Partial<Record<ModelKind, number>>>;
  range?: Record<string, TmRange>;
  ai?: { action: 'ENTER' | 'STAY_OUT'; probability: number; rank: number; weight: number; factors: DecisionFactor[] };
  /** horizon -> outcome, null while it is not known yet */
  actual: Record<string, TmActual | null>;
  path: { date: string; stock: number; benchmark: number | null }[];
}

export interface TmHorizonSummary {
  resolved: number;
  endDate: string | null;
  odds?: Partial<Record<ModelKind, { n: number; hitRate: number; brier: number; brierBaseRate: number; auc: number | null; topMinusBottomExcess: number | null }>>;
  range?: { n: number; coverage: number; naiveCoverage: number; target: number; medianAbsError: number; naiveMedianAbsError: number; avgWidth: number; naiveWidth: number };
  ai?: { universeReturn: number; picks: string[]; picksReturn?: number; excessVsUniverse?: number; picksBeatSector?: number };
}

export interface TimeMachineRunSummary {
  id: number;
  asOfDate: string;
  runAt: string;
  dataCutoff: string;
  headline: string;
  isDemo: boolean;
}

export interface TimeMachineRun extends TimeMachineRunSummary {
  result: {
    asOfDate: string;
    horizons: number[];
    stocks: TmStock[];
    summary: Record<string, TmHorizonSummary>;
    models: Record<string, Record<string, unknown>>;
    headline: string;
    dataCutoff: string;
  };
}

// ---------- Admin ----------

export interface Job {
  id: number;
  jobType: string;
  status: 'PENDING' | 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | string;
  log: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

// ---------- Universe management ----------

export interface UniverseCompany {
  id: number;
  symbol: string;
  formerSymbols: string | null;
  cik: string | null;
  name: string;
  sector: string;
  industry: string | null;
  benchmarkSymbol: string;
  isDemo: boolean;
  active: boolean;
  memberSince: string | null;
  removedOn: string | null;
  priceCount: number;
  lastPriceDate: string | null;
  filingCount: number;
  forecastCount: number;
  deletable: boolean;
}

export interface UniverseResponse {
  universe: string;
  companies: UniverseCompany[];
  benchmarks: string[];
  sectors: string[];
  industries: string[];
  productIndustries: string[];
}

export interface SecMatch {
  symbol: string;
  cik: string;
  name: string;
  source: string;
}

export interface AddCompanyRequest {
  symbol: string;
  name: string;
  cik: string;
  sector: string;
  industry: string | null;
  benchmarkSymbol: string;
  memberSince: string | null;
  ingestSec: boolean;
}

export interface AddCompanyResponse {
  id: number;
  symbol: string;
  nextSteps: string[];
  job?: Job;
}
