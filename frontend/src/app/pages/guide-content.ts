import { IconName } from '../shared/icon';
import { Area } from '../shared/viz';

export interface GuideSection {
  id: string;
  title: string;
  icon: IconName;
}

/** Table of contents; each id is also an anchor that help tips link to. */
export const SECTIONS: GuideSection[] = [
  { id: 'start', title: 'What CivAlpha does', icon: 'compass' },
  { id: 'checklist', title: 'Setup checklist', icon: 'checklist' },
  { id: 'reading', title: 'Reading a forecast', icon: 'pulse' },
  { id: 'colors', title: 'Colours, badges and glyphs', icon: 'palette' },
  { id: 'tour', title: 'Page by page', icon: 'book' },
  { id: 'metrics', title: 'The metrics, explained', icon: 'calculator' },
  { id: 'workflows', title: 'Common tasks', icon: 'zap' },
  { id: 'glossary', title: 'Glossary', icon: 'search' },
  { id: 'faq', title: 'Questions', icon: 'help' },
  { id: 'shortcuts', title: 'Keyboard shortcuts', icon: 'keyboard' },
];

export interface PageCard {
  id: string;
  path: string;
  title: string;
  icon: IconName;
  area: Area;
  question: string;
  read: string[];
  caution?: string;
}

export const PAGES: PageCard[] = [
  {
    id: 'page-dashboard',
    path: '/',
    title: 'Dashboard',
    icon: 'grid',
    area: 'forecast',
    question: 'What changed since the last run?',
    read: [
      'Four tiles: pipeline health and data cutoff, the forecast lean split, the AI\'s entries and exits for today, and the two evidence verdicts in one word.',
      '"Leans that changed" lists forecasts whose lean flipped between the newest as-of date and the previous one; "Windows that closed" scores the forecasts whose 21-day window ended in the last 10 days.',
      '"Movers vs benchmark" ranks stocks by their 21-trading-day return minus their sector ETF\'s, with a sparkline of raw closes.',
    ],
    caution: 'A flip from coin flip to leans above is a change of the model\'s confidence, not of the world. Open the forecast and read what moved it.',
  },
  {
    id: 'page-forecasts',
    path: '/forecasts',
    title: 'Current forecasts',
    icon: 'pulse',
    area: 'forecast',
    question: 'What does the model expect for each stock right now?',
    read: [
      'One row per company and model. The purple pill is the probability that the stock beats its sector ETF over the next 21 trading days; the bracket and the tiny bar are its uncertainty interval.',
      'The lean chip says whether that interval sits wholly above 50% (leans above), wholly below (leans below), or straddles it (coin flip). Most forecasts are coin flips; that is honest, not broken.',
      'Compare the BASELINE and AUGMENTED rows: when they differ, the policy-event data moved the estimate.',
    ],
    caution: 'A probability of 56% is not a buy signal. Check the Accuracy page to see whether such numbers have been worth anything out of sample.',
  },
  {
    id: 'page-history',
    path: '/forecasts/history',
    title: 'Forecast history',
    icon: 'history',
    area: 'forecast',
    question: 'What was forecast before, and how did it turn out?',
    read: [
      'Every version of every forecast, newest first. Superseded versions are dimmed and kept, because forecasts are never deleted.',
      'Once a 21-day window closes, the Outcome column fills in: outperformed or not, the excess return, and the Brier score of that single forecast.',
    ],
  },
  {
    id: 'page-accuracy',
    path: '/accuracy',
    title: 'Accuracy',
    icon: 'target',
    area: 'forecast',
    question: 'Are the probabilities any good, and does the event data help?',
    read: [
      'The verdict at the top is the backend\'s own conclusion from a walk-forward evaluation: models trained only on the past, scored on blocks they never saw.',
      'The four tiles are the headline: best Brier score against the 0.25 of a coin flip, best AUC against 0.5, whether AUGMENTED beats BASELINE (its confidence interval must exclude zero), and the net return of a simulated trading rule with its t-statistic.',
      'The reliability diagram shows whether "60%" happened about 60% of the time. Dots on the diagonal are honest probabilities.',
      '"Act only when confident" is the abstention test: the forecasts are ranked by how far they sit from 50%, and each row shows what trading only the top 5%, 10%, 20% and so on would have earned per position after costs. A trader never acts on every stock every day; this table asks whether the model\'s surest calls are worth more than the rest.',
      'The folds table shows the same comparison block by block; green cells are folds where the event data helped.',
    ],
    caution: 'Negative Brier skill and an AUC near 0.5 mean the model has not found a usable edge yet. The page says so when that is the case.',
  },
  {
    id: 'page-companies',
    path: '/companies',
    title: 'Companies',
    icon: 'building',
    area: 'research',
    question: 'What do we know about this stock, from its own filings?',
    read: [
      'The list shows every tracked stock with its benchmark ETF, last close, dividend status and both latest forecasts.',
      'Filter by sector, industry, your own tags, lean or dividend status, or type into the search box. Click a tag chip under a company name to keep only that tag.',
      'A company page has four tabs: Overview (price against the benchmark, dividends, key filed facts), Filings & financials (every 10-K, 10-Q and 8-K with XBRL values), Policy exposure (where the filings say the company is exposed to tariffs or rates), and Forecasts (its own history).',
      'Grey FILED and REPORTED badges mark facts taken straight from a filing; amber ESTIMATED marks a value the platform inferred.',
    ],
  },
  {
    id: 'page-compare',
    path: '/compare',
    title: 'Compare',
    icon: 'columns',
    area: 'research',
    question: 'How do two or three stocks (or strategies) stack up side by side?',
    read: [
      'Tick up to three companies on the Companies page, or up to three strategies on the Strategy lab, and press Compare. The same rows appear for each column; the best value in a row is bold with a ✓.',
      'For companies: price against the benchmark over a year, the 21-day move, both forecasts with their leans, the AI\'s current action, resolved-forecast accuracy, dividends and the latest filed facts.',
      'For strategies: every metric of the backtest, the equity curves on one chart, and calendar-year returns.',
    ],
  },
  {
    id: 'page-events',
    path: '/events',
    title: 'Policy events',
    icon: 'landmark',
    area: 'research',
    question: 'Which tariff and rate decisions are in the data, and whom do they touch?',
    read: [
      'Each event has a category (monetary policy or trade/tariff), a severity, and an evidence status: OFFICIAL when a primary document is stored, NEWS ONLY when only press reports exist.',
      'Only OFFICIAL events feed the AUGMENTED model. An event page lists the affected companies and the filing passage that documents each exposure.',
      'You can add an event with a source; affected forecasts are re-issued as a new version.',
    ],
  },
  {
    id: 'page-strategies',
    path: '/strategies',
    title: 'Strategy lab',
    icon: 'flask',
    area: 'strategy',
    question: 'Does any classic trading rule, or the AI, beat simply holding everything?',
    read: [
      'Every strategy is backtested on the same stocks, the same out-of-sample window and the same costs, then judged against equal-weight buy & hold.',
      'The bars in the table are scaled to the column\'s largest value, so the eye can rank without reading. The ✓ marks the best cell in a column.',
      'The Excess column shows the annual return above buy & hold with its confidence interval drawn against the zero line: green when the whole interval is above zero, red when wholly below, grey when it crosses.',
      'A strategy earns "Beats buy & hold" only with three years of data, an interval above zero and a Deflated Sharpe Ratio of at least 0.95. Anything else is treated as luck.',
      'The AI family tests the decision layer on the same probabilities: "confident entries only" waits for p ≥ 0.60 (abstention), "sized by volatility" keeps the same entries but gives calm stocks more capital than volatile ones (position sizing). The "Does confidence pay?" table ranks the AI\'s own forecasts by probability and shows the net return of acting only on the top slice.',
    ],
    caution: 'Twenty strategies tested on five years and thirty stocks will always produce one that looks great. The DSR exists to deflate exactly that.',
  },
  {
    id: 'page-decisions',
    path: '/decisions',
    title: 'AI decisions',
    icon: 'sparkles',
    area: 'strategy',
    question: 'What would the AI strategy do today, and why?',
    read: [
      'One card per stock with its probability of beating the sector ETF over the next 10 trading days and the action: Enter (green), Exit (red), Hold or Stay out.',
      'The zoned meter puts the probability against the two thresholds: red zone below the exit level, green zone at or above the entry level.',
      'Open "Why" for the factors that moved the probability (blue raises it, red lowers it) and which classic rules would also hold the stock.',
      'Each held stock also shows its volatility-sized weight (the AI_SIZED rule: 0.04 divided by its annualized 21-day volatility, at most 20%) and a "confident" chip when the probability clears the 0.60 bar of the AI_CONF rule. Both are what the sized and confident variants in the Strategy lab would do today.',
    ],
    caution: 'Decisions are recorded for research and never sent anywhere. The backtest of this exact strategy is on the Strategy lab page.',
  },
  {
    id: 'page-timemachine',
    path: '/timemachine',
    title: 'Time machine',
    icon: 'hourglass',
    area: 'strategy',
    question: 'If I had used this on a past date, what would it have said, and was it right?',
    read: [
      'Pick a past date. The models are retrained with only the data known then, so there is no peeking.',
      'Three tiles score the result at each horizon: hit rate of the direction calls against a coin flip, the AI\'s picks against the whole universe, and how often the actual return fell inside the forecast band (target 80%).',
      'Click a row to see that stock\'s real path drawn inside the band that was forecast.',
    ],
    caution: 'One date is one draw. It is an illustration; the Accuracy and Strategy lab pages hold the multi-year evidence.',
  },
  {
    id: 'page-doublers',
    path: '/doublers',
    title: 'Doubler study',
    icon: 'zap',
    area: 'strategy',
    question: 'How often does a stock double in a few months, and can a screen find those days?',
    read: [
      'The base rate is the share of all stock-days that went on to double within the horizon. The screen is a fixed profile (volatile, small and cheap, on a breakout or volume spike).',
      'The lift tile divides the screen\'s hit rate by the base rate. It counts only when the confidence interval sits above the base rate.',
      'The red tile is the other side: how often the same stock-days lost half. The quintile table (greener = more doublers) shows where the screen\'s thresholds come from.',
    ],
    caution: 'A universe without delisted stocks overstates every rate here. The page lists the caveats.',
  },
  {
    id: 'page-universe',
    path: '/universe',
    title: 'Universe',
    icon: 'globe',
    area: 'data',
    question: 'Which stocks are tracked?',
    read: [
      'Add a ticker; "Look up on SEC" fills in the name and CIK. Pick a sector and the sector ETF it will be measured against (XLK for technology, XLV for health care, and so on).',
      'Tags are your own categories, comma-separated: a theme (AI, China exposed), a watchlist (core, watch only) or anything worth filtering by. Edit them in the table; they group the Companies page and never reach the models.',
      'Removing a stock ends its membership on a date; history is kept so past backtests do not change. Deleting is only possible before any data is attached.',
      '"Expand the universe" finds every company on Nasdaq or NYSE above a public-float threshold from SEC data, previews the largest ones not yet tracked, and adds them in one job with a tag. Breadth is what makes a small edge measurable; the notes under the preview say what the batch costs in price-provider quota and pipeline time.',
    ],
    caution: 'Backdating "member since" puts a stock into backtests for periods when you had not chosen it yet, which flatters the results.',
  },
  {
    id: 'page-admin',
    path: '/admin',
    title: 'Data & pipeline',
    icon: 'database',
    area: 'data',
    question: 'How do I refresh everything?',
    read: [
      'The status tiles show the data cutoff and whether the price provider and SEC access are configured; green means ready.',
      '"Run pipeline" does everything in order: prices, filings, macro and events, evaluation, forecasts, outcome resolution, the strategy lab, AI decisions and the doubler study. It only fetches what is new.',
      'Advanced lists each step separately. The jobs table shows progress and logs; it refreshes itself while something runs.',
    ],
  },
];

export interface MetricDoc {
  id: string;
  name: string;
  short: string;
  what: string;
  good: string;
  where: string;
}

export const METRICS: MetricDoc[] = [
  {
    id: 'brier',
    name: 'Brier score',
    short: 'how far the probabilities were from what happened',
    what: 'For each forecast, the squared gap between the probability and the outcome (1 if the stock outperformed, 0 if not), averaged over all forecasts.',
    good: 'Lower is better. 0 is perfect. Always saying 50% scores 0.25, so anything above 0.25 is worse than a coin flip.',
    where: 'Accuracy page, forecast outcomes, time machine.',
  },
  {
    id: 'brier-skill',
    name: 'Brier skill',
    short: 'improvement over always forecasting the base rate',
    what: '1 minus the model\'s Brier score divided by the Brier score of a forecast that always says the historical base rate.',
    good: 'Above 0 beats the base rate; below 0 is worse than knowing nothing about the stock.',
    where: 'Accuracy page.',
  },
  {
    id: 'log-loss',
    name: 'Log loss',
    short: 'like Brier, but punishes confident misses harder',
    what: 'The average negative log of the probability assigned to what actually happened.',
    good: 'Lower is better. A coin flip scores 0.693.',
    where: 'Accuracy page.',
  },
  {
    id: 'auc',
    name: 'AUC',
    short: 'can the model rank winners above losers?',
    what: 'The probability that a randomly chosen outperformer got a higher forecast than a randomly chosen underperformer.',
    good: '0.5 is no skill; 1.0 is perfect ranking. Anything under about 0.55 on this problem is noise.',
    where: 'Accuracy page, time machine.',
  },
  {
    id: 'hit-rate',
    name: 'Hit rate / accuracy',
    short: 'share of direction calls that were right',
    what: 'A call is right when the probability was above 50% and the stock outperformed, or below 50% and it did not.',
    good: 'A coin scores about 50%. With a few dozen calls, anything between 40% and 60% is indistinguishable from luck.',
    where: 'Accuracy page (issued forecasts), time machine.',
  },
  {
    id: 'calibration',
    name: 'Calibration',
    short: 'does "60%" happen 60% of the time?',
    what: 'Forecasts are grouped into bins by probability; for each bin the observed rate of outperformance is compared with the mean forecast.',
    good: 'Dots on the diagonal of the reliability diagram. Above the line the model is under-confident, below it over-confident.',
    where: 'Accuracy page.',
  },
  {
    id: 'confidence-interval',
    name: 'Confidence interval (CI)',
    short: 'the range a number could plausibly be, given the noise',
    what: 'A 95% interval from a block bootstrap: the history is re-sampled in blocks of dates many times and the statistic recomputed each time.',
    good: 'An effect is only taken seriously when the whole interval is on one side of zero (or above the base rate). Green bars in the tables mean exactly that.',
    where: 'Accuracy comparison, strategy lab Excess column, doubler study.',
  },
  {
    id: 't-stat',
    name: 't-statistic',
    short: 'how many standard errors from zero',
    what: 'The mean net return divided by its standard error.',
    good: '|t| above 2 is the usual bar for "probably not zero". Below that the simulated return is noise.',
    where: 'Accuracy page, trading simulation.',
  },
  {
    id: 'cagr',
    name: 'CAGR',
    short: 'average annual growth, compounded',
    what: 'The constant yearly rate that would turn the starting capital into the ending capital over the window, after costs.',
    good: 'Only meaningful next to the buy & hold reference and the drawdown that came with it.',
    where: 'Strategy lab.',
  },
  {
    id: 'sharpe',
    name: 'Sharpe ratio',
    short: 'return per unit of risk',
    what: 'Annualized return over cash divided by annualized volatility. Sortino is the same with only downside volatility.',
    good: 'Above 1 is good for a single strategy over a long window. Over a few years with few stocks a high Sharpe is often luck, which is what the DSR checks.',
    where: 'Strategy lab, accuracy trading simulation.',
  },
  {
    id: 'max-drawdown',
    name: 'Max drawdown',
    short: 'the worst peak-to-trough fall',
    what: 'The largest percentage decline from a previous high to a later low in the equity curve.',
    good: 'Closer to zero is better. It is the pain a holder of the strategy would have had to sit through.',
    where: 'Strategy lab.',
  },
  {
    id: 'exposure',
    name: 'Exposure',
    short: 'how much of the capital was invested on average',
    what: 'The average fraction of capital in positions over the window; the rest sat in cash earning the fed funds rate.',
    good: 'Neither good nor bad. Low exposure usually means lower return and lower drawdown.',
    where: 'Strategy lab, AI decisions.',
  },
  {
    id: 'excess',
    name: 'Excess return',
    short: 'annual return above equal-weight buy & hold',
    what: 'The strategy\'s annualized return minus the reference\'s, with a block-bootstrap 95% interval.',
    good: 'Only an interval wholly above zero counts, and even then the DSR must agree.',
    where: 'Strategy lab.',
  },
  {
    id: 'dsr',
    name: 'Deflated Sharpe Ratio (DSR)',
    short: 'the chance the edge is real after trying many strategies',
    what: 'The probability that the strategy\'s Sharpe ratio over buy & hold is above zero once the number of strategies tried on the same history, the window length and the shape of the returns are accounted for.',
    good: 'At least 0.95 is required for a strategy to count as beating buy & hold.',
    where: 'Strategy lab.',
  },
  {
    id: 'costs',
    name: 'Trading costs',
    short: 'what a round trip costs the backtest',
    what: 'Every trade is charged a number of basis points per side on the amount traded (10 bp = 0.10% by default). Cost sensitivity re-runs the backtest at other levels.',
    good: 'A strategy that only works at zero cost is not a strategy.',
    where: 'Strategy lab, accuracy trading simulation.',
  },
  {
    id: 'base-rate',
    name: 'Base rate',
    short: 'how often the thing happens anyway',
    what: 'The share of all cases with the outcome: of all stock-days, how many beat the sector ETF, or how many doubled.',
    good: 'It is the chance level every model and screen has to beat.',
    where: 'Accuracy page, doubler study.',
  },
  {
    id: 'lift',
    name: 'Lift',
    short: 'the screen\'s hit rate divided by the base rate',
    what: 'How many times more often the outcome occurs when the screen fires than on a random stock-day.',
    good: '1× is chance. It counts only when the screen\'s confidence interval sits above the base rate.',
    where: 'Doubler study.',
  },
  {
    id: 'breadth',
    name: 'Breadth (universe size)',
    short: 'the same small edge is invisible on 40 stocks and measurable on 400',
    what: 'The fundamental law of active management: the information ratio grows with the square root of the number of independent bets. Ten times the stocks makes a given skill about three times as visible, and lets the statistical tests on the Accuracy and Strategy lab pages separate it from luck.',
    good: 'More stocks of the kind the models are meant for. Public float, as reported in the 10-K, is the size screen the Universe page offers because it needs no market-data licence.',
    where: 'Universe page (Expand the universe).',
  },
  {
    id: 'abstention',
    name: 'Abstention (act only when confident)',
    short: 'what the surest 10% of calls earned, after costs',
    what: 'Forecasts are ranked by confidence (distance from 50%, or the probability itself for a long-only rule). For the top 5%, 10%, 20%, 30%, 50% and 100% the table shows the bar a call had to clear, how often the direction was right, and the mean excess return over the sector ETF per position after costs, with a block-bootstrap interval.',
    good: 'A model with real skill earns more per position as the slice gets more selective, and the 10% row\'s interval sits above zero. If the rows all look alike, confidence carries no information and abstaining would not help.',
    where: 'Accuracy page (both forecasting models), Strategy lab (the AI\'s own forecasts).',
  },
  {
    id: 'position-sizing',
    name: 'Position sizing (volatility targeting)',
    short: 'risky stocks get less capital, calm ones more',
    what: 'The AI_SIZED strategy keeps AI_GBM\'s entries and exits but sizes each position as 0.04 divided by the stock\'s annualized 21-day volatility, capped at 20% of capital, with the whole book capped at 100% (no leverage). A stock at 32% volatility gets 12.5%, the equal slice; one at 64% gets 6%.',
    good: 'Sizing cannot create an edge, but it changes how much of one is kept: compare Sharpe and max drawdown with AI_GBM. A better Sharpe at a lower drawdown means the same calls were carried with less pain.',
    where: 'Strategy lab, AI decisions (sized weight on each card).',
  },
  {
    id: 'coverage',
    name: 'Band coverage',
    short: 'how often reality fell inside the forecast band',
    what: 'Each stock got a 10th to 90th percentile band for its return; coverage is the share of actual returns that landed inside.',
    good: 'About 80% is the target. Much higher means the band is lazily wide; much lower means over-confident.',
    where: 'Time machine.',
  },
];

export interface Term {
  id: string;
  term: string;
  def: string;
}

export const GLOSSARY: Term[] = [
  { id: 'target', term: 'Target', def: 'The one thing forecast: the probability that a stock\'s total return over the next 21 trading days beats its sector benchmark ETF, measured close to close.' },
  { id: 'horizon', term: 'Horizon', def: 'How far ahead a forecast looks, in trading days (21 for forecasts, 10 for the AI strategy, 5 to 63 in the time machine). 21 trading days is about one calendar month.' },
  { id: 'as-of', term: 'As-of date / data cutoff', def: 'The last close a forecast was allowed to see. Everything published, filed or priced after it is invisible to that forecast.' },
  { id: 'interval', term: 'Uncertainty interval', def: 'The 10th to 90th percentile of a probability across 30 bootstrap refits of the model. It measures estimation uncertainty only.' },
  { id: 'lean', term: 'Lean', def: 'Leans above when the whole interval is above 50%, leans below when wholly below, coin flip otherwise.' },
  { id: 'models', term: 'BASELINE / AUGMENTED', def: 'Two models forecast every stock. BASELINE sees prices and filed fundamentals. AUGMENTED also sees official policy events, weighted by the company\'s documented exposure. The difference between them measures whether the event data helps.' },
  { id: 'live-replay', term: 'LIVE / REPLAY', def: 'LIVE forecasts were published before their outcome window opened. REPLAY forecasts were computed later from data available at the cutoff; they are honest reconstructions but not live calls, and are scored separately.' },
  { id: 'reported-estimated', term: 'REPORTED / FILED vs ESTIMATED', def: 'Grey badges mark values taken directly from an SEC filing or recorded prices. Amber ESTIMATED badges mark values the platform inferred (a sector map, a keyword rule or a language model), always with lower confidence.' },
  { id: 'official-news', term: 'OFFICIAL / NEWS ONLY', def: 'An event is OFFICIAL when a primary document (a Federal Register notice, an FOMC statement) is stored. NEWS ONLY events come from press reports and are kept out of the models until a document is linked.' },
  { id: 'tags', term: 'Tags', def: 'Your own categories for a stock, set on the Universe page: a theme (AI, China exposed), a watchlist (core, watch only) or anything else worth filtering by. A tag is one tag universe-wide whatever its case. Tags only group and filter the Companies and Universe pages (and list_companies in the MCP tools); the models never see them.' },
  { id: 'benchmark', term: 'Sector benchmark ETF', def: 'The ETF a stock is measured against: XLK technology, XLV health care, XLY consumer discretionary, XLP staples, XLC communication, XLI industrials, XLB materials.' },
  { id: 'exposure-doc', term: 'Policy exposure', def: 'A documented link between a company and a policy target (a country, a product, interest rates) with a channel and a share, taken from a filing passage or XBRL fact.' },
  { id: 'walk-forward', term: 'Walk-forward evaluation', def: 'The history is cut into blocks. For each block the model is trained only on samples whose outcome was known before the block starts, then scored on the block. No block is ever used to tune the model that scores it.' },
  { id: 'out-of-sample', term: 'Out of sample', def: 'Data the model never saw during training. Every number on the Accuracy and Strategy lab pages is out of sample.' },
  { id: 'factors', term: 'Factors', def: 'For a forecast, each feature\'s coefficient times its standardized value, in log-odds. For an AI decision, each feature\'s contribution in probability points. Both are explanations of the model, not causes in the world.' },
  { id: 'version', term: 'Forecast version', def: 'A forecast is never edited. New evidence for the same company, model and as-of date produces version n+1 that supersedes version n. Both stay visible, and the forecast page shows what changed between them, factor by factor.' },
  { id: 'event-impact', term: 'Event impact', def: 'On an event page: the forecasts that were re-issued because the event arrived (each next to the version it replaced), and for every exposed company the last forecast before the event date against the first one after it.' },
  { id: 'accuracy-over-time', term: 'Accuracy over time', def: 'Resolved live forecasts grouped by the month their window closed: mean Brier score and hit rate per month, so you can see whether the models are improving or decaying rather than only their average.' },
  { id: 'stock-days', term: 'Stock-days', def: 'One stock on one trading day. The doubler study counts them: 30 stocks over 250 days are 7,500 stock-days.' },
  { id: 'episode', term: 'Episode', def: 'In the doubler study, one move of +100%: the first day the screen flagged it, the entry, and the day the close first reached twice the entry.' },
  { id: 'decision-layer', term: 'Decision layer', def: 'Everything between a probability and a position: whether to act at all (abstention, the AI_CONF rule), how much to buy (position sizing, the AI_SIZED rule) and when to leave (the exit threshold and the trailing stop). Traders earn most of their keep here; the Strategy lab tests each piece on the same probabilities.' },
  { id: 'rule-votes', term: 'Rule votes', def: 'On an AI decision card, which of the classic rules (golden cross, momentum, RSI pullback and so on) would hold the stock that day.' },
  { id: 'pipeline', term: 'Pipeline', def: 'The one background job that refreshes everything in order. Later runs only fetch what is new.' },
  { id: 'mcp', term: 'MCP', def: 'Model Context Protocol: the API also speaks it at /mcp, so Claude Code, Claude Desktop or any MCP client can read the research data and start jobs.' },
];

export interface Faq {
  q: string;
  a: string;
}

export const FAQ: Faq[] = [
  {
    q: 'Nearly every forecast is a coin flip. Is something broken?',
    a: 'No. Beating a sector ETF over one month is close to unpredictable, and the platform refuses to pretend otherwise. The interval around most probabilities straddles 50%, so the lean chip says coin flip. A forecast that leans strongly is rare and worth reading the factors for.',
  },
  {
    q: 'The Accuracy verdict says the claim is NOT supported. Should I stop using the platform?',
    a: 'That verdict is the platform doing its job. It will say supported only when a cost-adjusted, out-of-sample simulation has at least 36 periods with positive mean net return and a t-statistic above 2. Until then, use the platform to read filings, exposures and events, and to watch the evidence accumulate.',
  },
  {
    q: 'Why do BASELINE and AUGMENTED show the same number?',
    a: 'When no official policy event in the window touches a company with documented exposure, the event features are zero and both models see the same inputs. Add an event with an official source, or ingest more filings, and they will diverge where it matters.',
  },
  {
    q: 'What is the difference between a forecast, an AI decision and a strategy?',
    a: 'A forecast is a probability for one stock over 21 days from a logistic model. An AI decision is the gradient-boosted strategy\'s action for today over a 10-day horizon with fixed thresholds. A strategy is any rule, classic or AI, backtested in the Strategy lab.',
  },
  {
    q: 'Why does the AI appear several times in the Strategy lab?',
    a: 'Each AI row changes one thing and keeps the rest. AI_GBM is the standard rule. AI_GBM_TSTOP10 adds a trailing stop. AI_CONF raises the bar to act (abstention). AI_SIZED keeps the same trades but sizes them by volatility. AI_FUND sees only the financial reports; AI_DIV adds dividend signals. Reading them side by side shows which part of the decision layer, if any, earns its place.',
  },
  {
    q: 'Can I trade from this?',
    a: 'The platform places no orders and has no brokerage connection. It is research software; the disclaimers in the footer apply to every page.',
  },
  {
    q: 'Why are some values marked ESTIMATED?',
    a: 'Exposures that no filing states outright are inferred from a sector map, a keyword rule or a language model. They carry an amber badge and a lower confidence, and the forecast explanation shows which factors rest on them.',
  },
  {
    q: 'How often should I run the pipeline?',
    a: 'Once after each US close is plenty. Set CIVALPHA_PIPELINE_CRON in .env to schedule it; each run only fetches new prices, filings and events.',
  },
  {
    q: 'A page says no evaluation or backtest exists yet.',
    a: 'Run the pipeline from Data & pipeline. Evaluation needs about two years of prices for the tracked stocks and their benchmark ETFs; the status tiles there show what is missing.',
  },
];

export interface Shortcut {
  keys: string[];
  what: string;
}

export const SHORTCUTS: Shortcut[] = [
  { keys: ['⌘ / Ctrl', 'K'], what: 'Open the search palette: jump to a page or company, or add a ticker' },
  { keys: ['/'], what: 'Open the search palette (when not typing in a field)' },
  { keys: ['↑', '↓'], what: 'Move through palette results' },
  { keys: ['↵'], what: 'Open the selected result' },
  { keys: ['Esc'], what: 'Close the palette or the menu' },
];
