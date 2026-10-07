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
      'The three dot-and-whisker charts put every evaluated model on one line per metric: the dot is the out-of-sample Brier skill, AUC or top-10% net excess, the whisker its 95% interval, the dashed line the bar (zero, 0.5, zero). Green means the whole whisker is on the good side, red wholly on the wrong side, grey that it crosses the line. The two live logistic models and the recorded book\'s gradient-boosted model (scored on the same 21-day target; evaluations before 2026-10-06 also show the 10-day label it traded then) sit side by side; the data table below the charts has the numbers.',
      'The calibration card asks what the probabilities are worth: the Brier change when each model\'s probabilities are mapped through an isotonic curve fitted on earlier folds (left of zero with the whole whisker means the model was overconfident), the spread and the confident-decile hit rate raw against calibrated, and one model\'s reliability diagram before and after. Raw and calibrated are compared on the same rows; the first three folds have no map and are left out of both.',
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
      'Start with "What the lab found": one card per question (does anything beat buy & hold, does the AI add anything beyond sizing, do its thresholds mean anything once probabilities are honest, do its surest calls pay, does any way of acting help, do extra inputs help). The badge is the short answer, the lines are the numbers behind it, the link opens the section with the detail. The full text the backend stored with the run is under "Full text summary".',
      'The tile "The recorded AI book" is the rule whose decisions are stored daily (AI_SIZED), not the plain AI rule.',
      'The bars in the table are scaled to the column\'s largest value, so the eye can rank without reading. The ✓ marks the best cell in a column.',
      'The Excess column shows the annual return above buy & hold with its confidence interval drawn against the zero line: green when the whole interval is above zero, red when wholly below, grey when it crosses.',
      'A strategy earns "Beats buy & hold" only with three years of data, an interval above zero and a Deflated Sharpe Ratio of at least 0.95. Anything else is treated as luck.',
      '"What the sizing does without a forecast" puts the recorded book next to the same volatility sizing on two selections that use no AI: the whole universe (sizing only) and the 12-1 momentum top 5. Growth of capital and drawdown are drawn for all four, with equal-weight buy & hold as the grey reference. If the book\'s lines sit with the sizing-only line, the sizing explains the book, not the forecast; the finding card "Does the AI forecast add anything beyond position sizing?" links here.',
      'The decision layer panel draws the AI rules as dots against buy & hold: excess return with its interval (the only one of the three with an interval), Sharpe ratio and max drawdown. A rule has beaten the reference only when its whole whisker sits right of the dashed line; the Sharpe and drawdown dots describe the window and prove nothing. The data table under the charts has the numbers.',
      'The AI family tests the decision layer on the same probabilities: "confident entries only" waits for p ≥ 0.60 (abstention), "sized by volatility" keeps the same entries but gives calm stocks more capital than volatile ones (position sizing), "book follows the ranking" lets a stock that beats the weakest holding by 0.08 take its slot, "ranking + conviction sizing" adds a tilt so surer calls get more capital, and "with policy-event features" adds the tariff and rate-shock inputs back to the model. The recorded book is "sized by volatility". The "Does confidence pay?" table ranks the AI\'s own forecasts by probability and shows the net return of acting only on the top slice.',
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
      'One card per stock with its probability of beating the sector ETF over the next 21 trading days (10 on decisions made before 2026-10-06, ADR-0001) and the action: Enter (green), Exit (red), Hold or Stay out.',
      'Next to the probability, "≈ x% calibrated" is what that raw probability has meant out of sample: the raw number mapped through an isotonic curve fitted on the walk-forward. The "Honest probabilities" tile counts how many of the day\'s decisions would clear the entry bar on calibrated numbers. The action is still taken on the raw probability (ADR-0002).',
      'The zoned meter puts the probability against the two thresholds: red zone below the exit level, green zone at or above the entry level.',
      'Open "Why" for the factors that moved the probability (blue raises it, red lowers it) and which classic rules would also hold the stock.',
      'The recorded book is the AI_SIZED rule: fixed thresholds (enter at p ≥ 0.55 in the top 8, exit below 0.48) and a weight of 0.04 divided by the stock\'s annualized 21-day volatility, at most 20%, with the whole book capped at 100%. The model behind it leaves out the policy-event features (tariff and rate shocks), which the lab showed hurt the book. A "confident" chip marks a probability that clears the 0.60 bar of the AI_CONF rule.',
      'On 2026-10-05 a second rule, AI_RANK_SIZED (a replacement rule plus a conviction tilt), was recorded for the same day and kept in the database for the record; the lab priced those two ideas at about 0.1 Sharpe and the book stayed with volatility sizing.',
      'With a language model configured, every entry candidate also carries a reviewer block: the model reads a brief of what the platform knows about the stock (factors, recent prices and corporate actions, results announcements, insider activity, filed facts) and records whether it agrees, urges caution or disagrees, with the numbers it relied on. In advisory mode (the default) the decision stands either way; in veto mode a disagreement stops the entry and the card says "Vetoed by the reviewer". Every review is stored with its brief so it can be scored against outcomes later.',
    ],
    caution: 'Decisions are recorded for research and never sent anywhere. The backtest of this exact strategy is on the Strategy lab page.',
  },
  {
    id: 'page-portfolio',
    path: '/portfolio',
    title: 'My portfolio',
    icon: 'wallet',
    area: 'strategy',
    question: 'What should I do with the stocks I hold, and when should I look again?',
    read: [
      'Enter each holding (symbol, shares, average cost in USD) and your cash under Holdings, then press Refresh advice. Advice is also made after every pipeline run and every AI-decisions job, on the book\'s latest decision date.',
      'Each card shows one action, decided by the first rule that fires: Not covered (the stock is not in the universe or has no price), Check the data (a broken-looking price series, a move of 30% or more with no recorded corporate action in the last 45 days, or an unreviewed 8-K that reads like a split or spin-off), Trim (the weight is above 20% or above 1.5 times its volatility size), Sell (the book\'s probability is below 0.48), Add (probability at least 0.55 and weight below 0.67 times its volatility size, limited by your cash), Hold.',
      'The volatility size is the book\'s own: 0.04 divided by the stock\'s annualized 21-day volatility, at most 20%. The weight meter shows your weight with the volatility size as its tick. The 1.5x and 0.67x band and the 0.48 / 0.55 thresholds keep the advice from flipping every day.',
      'The chip under the symbol names the layer: data check and risk rule act now and need no forecast skill; model opinion (Sell, Add) becomes the headline only once the book model passes its pre-registered live test (the Model evidence tile). Until then the headline is Hold and the card says "model says sell, unproven".',
      '"Look again by" is the end of the 21-trading-day forecast window; the chips under it say what would change the advice sooner. The platform says nothing about holding periods beyond 21 trading days, price targets or stop prices.',
      'Buy ideas are the recorded book\'s positions you do not own, sized as the book sizes them. The track record scores every past action: the stock\'s return minus its sector ETF\'s over the 21 trading days after the advice, with a 95% interval once 30 rows of an action have resolved.',
    ],
    caution: 'Holdings are personal data. In token mode with CIVALPHA_ADMIN_TOKEN set, this page needs the token even to read. In accounts mode each person sees only their own portfolios and can keep up to 20, picked in the Portfolio list at the top (the choice is in the address as ?p=); the first holding or cash entry creates one named default. Holdings never go to a language model or a log line. Advice is recorded and never edited; editing a holding adds new advice rows. Research software, not investment advice: it places no orders.',
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
    id: 'page-signals',
    path: '/signals',
    title: 'Signal health',
    icon: 'pulse',
    area: 'strategy',
    question: 'Which model inputs still carry information, and which are fading?',
    read: [
      'One row per feature the AI sees: prices, filed fundamentals, policy shocks, dividends, insiders, earnings. The sparkline is its monthly information coefficient: each day\'s rank correlation across stocks with the next 21 days\' excess return over the sector ETF, averaged per month.',
      'Mean IC and its t-statistic over months say whether the input carried information at all; the IC information ratio says how steadily; "right sign" is the share of months it pointed the right way.',
      'The grade is corrected for the number of features tested. "Decaying" marks an input that carried information over the whole history but lost its sign in the last 12 months: the classic trace of an edge others have found.',
    ],
    caution: 'With a few dozen stocks a monthly IC is noisy; read the month count and the right-sign share before trusting a mean.',
  },
  {
    id: 'page-ablation',
    path: '/ablation',
    title: 'Feature fragility',
    icon: 'diff',
    area: 'strategy',
    question: 'Do the recorded book\'s numbers depend on any one group of inputs?',
    read: [
      'The study takes the book\'s input list (GBM_AI_39) and scores it six more times: without the price/technical group, without the report profile, without the insider inputs, without the earnings inputs, and with the dividend or the policy-event features added.',
      'The first pair of charts is the honest measure: walk-forward Brier skill and AUC per input list, on the 21-day forecast target (runs made before 2026-10-06 also show, in orange, the 10-day label the book traded then), each with a 95% interval from a bootstrap over 21-day blocks of as-of dates. Only a whisker wholly right of the dashed line would mean the probabilities know something.',
      'The second pair is the lab Sharpe and max drawdown of the same probabilities under the standard rule and under the recorded book\'s sizing. It is marked "not a skill metric": one rule on one five-year window, no interval. It is shown because it is the number that moves while the forecast quality does not.',
      'Every variant is registered as a trial, so running the study makes the Deflated Sharpe Ratio on the strategy lab stricter.',
    ],
    caution: 'A Sharpe that halves while every skill interval overlaps is measuring which stocks a rule happened to hold. Feature-set decisions are made on the first pair of charts, never on the second.',
  },
  {
    id: 'page-playbook',
    path: '/playbook',
    title: 'Setup playbook',
    icon: 'zap',
    area: 'strategy',
    question: 'Which situations have been worth acting on, and which stocks are in one now?',
    read: [
      'Each row is a setup a trader waits for: an insider buying in the open market, a results day on which the stock jumped or dropped, guidance raised or lowered, earnings due within a week, an earnings beat becoming public, a dividend raise, a new 52-week high, a golden cross, an oversold pullback, a crash, a volume surge, a tariff or rate shock. It fires on the first day the condition holds.',
      'The columns say what followed, as the excess return over the sector ETF from the next close: how often it fired, the hit rate against the base rate of all stock-days, the mean excess with its interval, and the payoff (average win over average loss).',
      'The grade is the honest part. SUPPORTED needs the mean excess to clear a bar corrected for the 45 setup-horizon pairs tested; SUGGESTIVE means only the plain 95% interval is above zero; NEGATIVE means the stock trailed its ETF after firing.',
      '"Firing now" lists the stocks each setup fired on in the last five sessions. It is a scan, not a signal: read the row\'s grade before caring.',
    ],
    caution: 'Forty-five pairs tested on one history means two will clear a plain 95% interval by luck. Only the corrected grade separates a real base rate from a lucky one.',
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
      '"Run pipeline" does everything in order: prices, filings, insider transactions, macro and events, evaluation, forecasts, outcome resolution, the strategy lab, AI decisions, the doubler study, the setup playbook and signal health. It only fetches what is new.',
      'Advanced lists each step separately. The jobs table shows each running job\'s completion percentage and current step (a pipeline run counts one step per company), plus its log; it refreshes itself while something runs. A queued job has no duration until the worker picks it up.',
    ],
  },
  {
    id: 'page-login',
    path: '/login',
    title: 'Sign in',
    icon: 'lock',
    area: 'data',
    question: 'How do I get in, and what happens when I get it wrong?',
    read: [
      'Sign-in exists only in accounts mode (CIVALPHA_AUTH=accounts). In token mode the page says sign-in is off and every research page is open.',
      'Sign in with the username and password the owner gave you. There is no sign-up: the owner creates every account.',
      'A wrong username, a wrong password, a locked account and a disabled one all answer "wrong username or password".',
      '5 failed sign-ins in a row lock the account for 15 minutes. 20 failed sign-ins from one address within 15 minutes make that address wait 15 minutes.',
      'A session ends after 12 hours without use and after 7 days at most; the next page then asks you to sign in and brings you back.',
    ],
    caution: 'Forgot your password? Ask the owner of this installation to set a new one. There is no e-mail reset.',
  },
  {
    id: 'page-account',
    path: '/account',
    title: 'My account',
    icon: 'user',
    area: 'data',
    question: 'How do I change my password, and how do I let an AI assistant read CivAlpha as me?',
    read: [
      'The tiles show who you are signed in as, your role and whether your password is still the first one the owner set.',
      'Passwords are 12 to 200 characters and must not contain your username. After the owner creates your account or sets a new first password, every other page stays closed until you choose your own.',
      'A password change signs out every other session and stops every personal token; the session you change it from stays signed in.',
      'A personal token lets an AI assistant or a script call CivAlpha as you, with your role. It is valid for 1 to 90 days (30 by default) and is shown once, when created: copy it then. The table keeps only its prefix, when it was last used, when it expires and whether it is active, revoked or expired.',
      'To connect Claude Code over MCP: claude mcp add --transport http civalpha http://localhost:8088/mcp --header "Authorization: Bearer <token>".',
    ],
    caution: 'Treat a token like your password: anyone holding it reads your portfolios. Revoke a token you no longer use. In token mode this page only says that accounts are off.',
  },
  {
    id: 'page-access',
    path: '/access',
    title: 'Access',
    icon: 'shield',
    area: 'data',
    question: 'Who can sign in, with which role, and who changed what?',
    read: [
      'Only an OWNER sees this page. In token mode it works with the admin token: that is how the first owner account is created. After creating it, set CIVALPHA_AUTH=accounts and restart the api.',
      'Create an account with a username (3 to 40 lower-case letters, digits, ".", "_" or "-"), a display name, an optional e-mail, a role and a first password. Generate fills a random 16-character password and shows it so you can hand it over. The person must choose their own at the first sign-in.',
      'Creating the first OWNER gives it every portfolio entered so far in token mode.',
      'The role select, Disable and Enable act at once. Changing a role or disabling an account stops every session and token of that person. The last active owner cannot be demoted or disabled, and nobody can demote or disable themselves.',
      '"Set new first password" is the only way back in after a forgotten password: it stops the person\'s sessions and tokens and clears a lock. Locked until shows an account locked by 5 failed sign-ins; the lock ends by itself after 15 minutes.',
      'The audit trail lists every account, password, token, portfolio, holding and cash change, newest first, with the values before and after. Filter it by the person who made the change. Password hashes and token values never enter it.',
    ],
    caution: 'Outside users: do not create accounts for people outside your household until the data-licence and advice-regulation questions are answered (BACKLOG #11).',
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
    id: 'ranking-book',
    name: 'Book follows the ranking (replacement rule)',
    short: 'a stronger candidate takes the weakest holding\'s slot',
    what: 'With fixed thresholds alone the book is path-dependent: a holding drifting at p = 0.50 keeps its slot (exit is below 0.48) while a p = 0.65 candidate waits in cash. The AI_RANK rule adds a replacement: when the book is full and an outsider clears 0.55 and beats the weakest holding by 0.08, the weakest is sold and the outsider bought. The margin stops the book churning on noise. AI_RANK_VOL sizes those positions by volatility; AI_RANK_SIZED adds conviction sizing on top: volatility size × (p − 0.5) / 0.05, clipped to 0.5×–3×, capped at 20%.',
    good: 'Compare trades, Sharpe and max drawdown with AI_SIZED. On the first run (2026-10-06) the replacement rule cost about 0.07 Sharpe and the tilt a further 0.03 with a worse drawdown, so the recorded book stayed with volatility sizing; the rows remain so the price of following the ranking is always visible.',
    where: 'Strategy lab (decision-layer table), AI decisions.',
  },
  {
    id: 'ic',
    name: 'Information coefficient (IC)',
    short: 'the rank correlation between a signal and what followed',
    what: 'On one day, the Spearman correlation across the tracked stocks between a feature\'s value and each stock\'s excess return over its sector ETF in the following 21 trading days; a month averages its days. The IC information ratio is the mean monthly IC divided by its standard deviation.',
    good: 'A mean IC of 0.03 with a t-statistic above the corrected bar is a real, usable input; most inputs score near zero. An IC IR above 0.5 is steady.',
    where: 'Signal health.',
  },
  {
    id: 'decay',
    name: 'Edge decay',
    short: 'an input that used to work and no longer does',
    what: 'The mean IC of the last 12 months compared with the earlier months. An input is flagged DECAYING when it was informative over the whole history but the last 12 months have the opposite sign.',
    good: 'None flagged. A decaying input is a reason to retrain, drop the feature or stop trusting the setups built on it.',
    where: 'Signal health.',
  },
  {
    id: 'payoff',
    name: 'Payoff ratio',
    short: 'the average win divided by the average loss',
    what: 'Over the outcomes of a setup, the mean excess return of the winning cases divided by the absolute mean of the losing ones.',
    good: 'A 45% hit rate with a payoff of 2 is a profitable setup; a 60% hit rate with a payoff of 0.5 is not. Traders earn on this shape, not on the hit rate alone.',
    where: 'Setup playbook.',
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
  { id: 'earnings', term: 'Earnings announcements', def: 'Results become public with an 8-K (Item 2.02), days or weeks before the 10-Q or 10-K with the same numbers. The platform uses the 8-K\'s acceptance time: a release before the open trades that day, one after the close trades the next day, and that session\'s excess return over the sector ETF is the market\'s reaction. The guidance tone (raised, lowered, maintained, outlook given) is a keyword estimate from the press release with the matched sentence as evidence; the next date is a year after the announcement that followed the same announcement last year.' },
  { id: 'insiders', term: 'Insider transactions', def: 'Trades by a company\'s officers, directors and 10% owners in its own stock, reported to the SEC on Form 4 within two business days. Open-market purchases (code P) are the informative ones; sales (S) are mostly diversification; grants, option exercises, tax withholding and gifts carry no signal and are listed for the record. The platform uses them point-in-time: a filing counts from the end of its filing day.' },
  { id: 'setup', term: 'Setup', def: 'A situation a trader waits for, recognisable at the close from data known then: a catalyst (an earnings surprise, a dividend change, a policy shock) or a technical state (a breakout, a cross, an oversold reading). The playbook scores each one on what followed.' },
  { id: 'stock-days', term: 'Stock-days', def: 'One stock on one trading day. The doubler study counts them: 30 stocks over 250 days are 7,500 stock-days.' },
  { id: 'episode', term: 'Episode', def: 'In the doubler study, one move of +100%: the first day the screen flagged it, the entry, and the day the close first reached twice the entry.' },
  { id: 'decision-layer', term: 'Decision layer', def: 'Everything between a probability and a position: whether to act at all (abstention, the AI_CONF rule), how much to buy (position sizing, the AI_SIZED rule, which is the recorded book; conviction sizing in AI_RANK_SIZED), which names fill the slots (the replacement rule, AI_RANK) and when to leave (the exit threshold and the trailing stop). Traders earn most of their keep here; the Strategy lab tests each piece on the same probabilities.' },
  { id: 'rule-votes', term: 'Rule votes', def: 'On an AI decision card, which of the classic rules (golden cross, momentum, RSI pullback and so on) would hold the stock that day.' },
  { id: 'live-price', term: 'Live price', def: 'On the Companies list, every company page and My portfolio, the latest quote polled from Tiingo every 5 minutes on weekdays 09:25-16:10 New York (ADR-0006): Tiingo\'s reference price, the last trade on IEX or the middle of its bid and ask. It shows the price now and the move since yesterday\'s close; on My portfolio also what each holding is worth now. A quote older than 15 minutes while the market is open is marked stale. Display only: forecasts, the book and the advice use daily closes, and the advised share counts stay on the close of the advice date.' },
  { id: 'pipeline', term: 'Pipeline', def: 'The one background job that refreshes everything in order. Later runs only fetch what is new.' },
  { id: 'roles', term: 'OWNER / MEMBER', def: 'The two roles in accounts mode. OWNER runs jobs, edits the universe and events, manages accounts on the Access page and sees every page. MEMBER reads every research page, keeps their own portfolios and manages their own password and tokens. The admin token always acts as OWNER.' },
  { id: 'personal-token', term: 'Personal token', def: 'A secret starting with cvt_ that lets an AI assistant or a script call CivAlpha as you, sent as Authorization: Bearer. It lives 1 to 90 days, is shown once when created, and stops when you revoke it, change your password, or the owner resets your password, changes your role or disables you.' },
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
    a: 'A forecast is a probability for one stock over 21 days from a logistic model. An AI decision is the gradient-boosted strategy\'s action for today over the same 21-day horizon (10 days before 2026-10-06): fixed thresholds and volatility sizing (the AI_SIZED rule). A strategy is any rule, classic or AI, backtested in the Strategy lab.',
  },
  {
    q: 'Why does the AI appear several times in the Strategy lab?',
    a: 'Each AI row changes one thing and keeps the rest. AI_GBM is the standard rule. AI_GBM_TSTOP10 adds a trailing stop. AI_CONF raises the bar to act (abstention). AI_SIZED keeps the same trades but sizes them by volatility. AI_SIZED is the book recorded on the AI decisions page. AI_RANK lets a stronger candidate replace the weakest holding, AI_RANK_VOL sizes that by volatility and AI_RANK_SIZED adds a conviction tilt. AI_WITH_EVENTS adds the policy-event features back. AI_FUND sees only the financial reports; AI_DIV adds dividend signals. Reading them side by side shows which part of the decision layer, if any, earns its place.',
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
