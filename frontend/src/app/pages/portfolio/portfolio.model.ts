import { AdviceAction, AdviceLayer, AdviceReason, AdviceResponse, AdviceTrigger, Holding } from '../../core/models';

/** The advice vocabulary (ADR-0004): one label, tone and glyph per action, the same on every part of the page. */
export const LABEL: Record<AdviceAction, string> = {
  NOT_COVERED: 'Not covered',
  REVIEW: 'Check the data',
  TRIM: 'Trim',
  SELL: 'Sell',
  ADD: 'Add',
  HOLD: 'Hold',
};
export const TONE: Record<AdviceAction, string> = {
  NOT_COVERED: 'neutral',
  REVIEW: 'warn',
  TRIM: 'warn',
  SELL: 'bad',
  ADD: 'good',
  HOLD: 'info',
};
export const GLYPH: Record<AdviceAction, string> = { NOT_COVERED: '○', REVIEW: '⚠', TRIM: '◐', SELL: '▼', ADD: '▲', HOLD: '●' };
export const LAYER_LABEL: Record<AdviceLayer, string> = {
  DATA: 'data check',
  RISK: 'risk rule',
  MODEL: 'model opinion',
  NONE: 'no rule fired',
};
export const LAYER_HINT: Record<AdviceLayer, string> = {
  DATA: 'The numbers for this stock are missing or look broken. Nothing else is advised until that is resolved.',
  RISK: 'Sizing and concentration: a rule that needs no forecast skill. It does not claim to raise returns, it limits how much one stock can hurt.',
  MODEL: "The book model's forecast of beating the sector ETF over 21 trading days. Shown as the headline only after the model passes its pre-registered live test.",
  NONE: 'Nothing fired: the position is inside its size band and the probability is between the exit and entry thresholds.',
};
/** Order of urgency, used for sorting and the to-do list. */
export const URGENCY: AdviceAction[] = ['REVIEW', 'SELL', 'TRIM', 'ADD', 'NOT_COVERED', 'HOLD'];

export type PriceSource = 'live' | 'close' | 'cost';

/** One holding as the page shows it: the advice of its day joined with the current holding and the live quote. */
export interface Position {
  symbol: string;
  name: string | null;
  sector: string | null;
  companyId: number | null;
  shares: number;
  avgCost: number;
  costBasis: number;
  price: number | null;
  priceSource: PriceSource;
  changeToday: number | null;
  quotedAt: string | null;
  stale: boolean;
  valueNow: number;
  weightNow: number;
  weightAdvice: number;
  target: number | null;
  action: AdviceAction;
  headline: AdviceAction;
  layer: AdviceLayer;
  opinionOnly: boolean;
  tradeShares: number | null;
  tradeUsdNow: number | null;
  probability: number | null;
  probabilityCalibrated: number | null;
  entryP: number | null;
  exitP: number | null;
  rank: number | null;
  bookAction: string | null;
  reviewOn: string;
  gainUsd: number | null;
  gainPct: number | null;
  reasons: AdviceReason[];
  triggers: AdviceTrigger[];
  changed: boolean;
  previousHeadline: AdviceAction | null;
  outcome: { excessReturn: number; windowEndDate: string } | null;
}

export interface ToDo {
  symbol: string;
  action: AdviceAction;
  tone: string;
  layer: AdviceLayer;
  opinionOnly: boolean;
  text: string;
}

export interface Segment {
  key: string;
  label: string;
  weight: number;
  valueUsd: number;
  tone: string;
  title: string;
}

/** Joins the day's advice with the current holdings (shares, cost) and the live quotes. */
export function positions(a: AdviceResponse | undefined, holdings: Holding[]): Position[] {
  if (!a) return [];
  const bySymbol = new Map(holdings.map((h) => [h.symbol, h]));
  const rows = a.advice.map((r) => {
    const h = bySymbol.get(r.symbol);
    const shares = h?.shares ?? r.shares;
    const avgCost = h?.avgCostUsd ?? 0;
    const live = r.live ?? null;
    const price = live?.price ?? r.close ?? null;
    const priceSource: PriceSource = live ? 'live' : r.close !== null ? 'close' : 'cost';
    const valueNow = price !== null ? shares * price : r.valueUsd;
    const costBasis = shares * avgCost;
    const gainUsd = avgCost > 0 && price !== null ? valueNow - costBasis : null;
    return {
      symbol: r.symbol,
      name: r.name,
      sector: r.sector ?? null,
      companyId: r.companyId,
      shares,
      avgCost,
      costBasis,
      price,
      priceSource,
      changeToday: live?.change ?? null,
      quotedAt: live?.quotedAt ?? null,
      stale: live?.stale ?? false,
      valueNow,
      weightNow: 0,
      weightAdvice: r.weight,
      target: r.targetWeight,
      action: r.action,
      headline: r.headline,
      layer: r.layer,
      opinionOnly: r.headline !== r.action,
      tradeShares: r.tradeShares,
      tradeUsdNow: r.tradeShares !== null && price !== null ? Math.abs(r.tradeShares) * price : null,
      probability: r.model.probability ?? null,
      probabilityCalibrated: r.model.probabilityCalibrated ?? null,
      entryP: r.model.entryP ?? null,
      exitP: r.model.exitP ?? null,
      rank: r.model.rank ?? null,
      bookAction: r.model.bookAction ?? null,
      reviewOn: r.reviewOn,
      gainUsd,
      gainPct: gainUsd !== null && costBasis > 0 ? gainUsd / costBasis : null,
      reasons: r.reasons,
      triggers: r.triggers,
      changed: r.changed,
      previousHeadline: r.previousHeadline,
      outcome: r.outcome,
    } satisfies Position;
  });
  const total = rows.reduce((s, p) => s + p.valueNow, 0) + (a.cashUsd ?? 0);
  for (const p of rows) p.weightNow = total > 0 ? p.valueNow / total : 0;
  return rows;
}

const usd = (v: number) => `$${v.toLocaleString('en-US', { maximumFractionDigits: v >= 1000 ? 0 : 2 })}`;
const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`;

/** The to-do list: every holding whose headline is not Hold, plus the model's unproven opinions, most urgent first. */
export function todo(list: Position[]): ToDo[] {
  return list
    .filter((p) => p.headline !== 'HOLD' || p.opinionOnly)
    .sort((x, y) => URGENCY.indexOf(x.action) - URGENCY.indexOf(y.action) || y.weightNow - x.weightNow)
    .map((p) => ({ symbol: p.symbol, action: p.action, tone: p.opinionOnly ? 'neutral' : TONE[p.action], layer: p.layer,
                   opinionOnly: p.opinionOnly, text: sentence(p) }));
}

/** One plain sentence per action: what to do, how much, and why. */
export function sentence(p: Position): string {
  const n = p.tradeShares !== null ? Math.abs(p.tradeShares) : null;
  const cost = p.tradeUsdNow !== null ? ` (≈ ${usd(p.tradeUsdNow)} now)` : '';
  switch (p.action) {
    case 'TRIM':
      return `Sell ${n ?? 'some'} of ${p.shares} shares${cost}: it is ${pct(p.weightAdvice)} of the portfolio, the size its volatility allows is ${pct(p.target ?? 0)}.`;
    case 'SELL':
      return `${p.opinionOnly ? 'Model opinion, unproven: sell' : 'Sell'} all ${p.shares} shares${cost}; its probability of beating the sector is ${pct(p.probability ?? 0, 0)}, below the ${pct(p.exitP ?? 0.48, 0)} exit line.`;
    case 'ADD':
      return n
        ? `${p.opinionOnly ? 'Model opinion, unproven: buy' : 'Buy'} ${n} more shares${cost} up to its ${pct(p.target ?? 0)} size; probability ${pct(p.probability ?? 0, 0)}.`
        : `${p.opinionOnly ? 'Model opinion, unproven: add' : 'Add'} up to ${pct(p.target ?? 0)}, but there is no cash entered to size it.`;
    case 'REVIEW':
      return p.reasons.find((r) => r.action === 'REVIEW')?.text ?? 'The price data looks broken; check the price chart before acting.';
    case 'NOT_COVERED':
      return p.reasons.find((r) => r.action === 'NOT_COVERED')?.text ?? 'Not covered by the platform.';
    default:
      return 'Inside its size band; nothing to do.';
  }
}

/** Allocation by holding (largest first, the rest grouped as "Other") plus cash, coloured by each holding's action. */
export function allocation(list: Position[], cash: number, top = 7): Segment[] {
  const total = list.reduce((s, p) => s + p.valueNow, 0) + cash;
  if (total <= 0) return [];
  const sorted = [...list].filter((p) => p.valueNow > 0).sort((x, y) => y.valueNow - x.valueNow);
  const head = sorted.slice(0, top).map((p) => ({
    key: p.symbol, label: p.symbol, weight: p.valueNow / total, valueUsd: p.valueNow, tone: TONE[p.headline],
    title: `${p.symbol}: ${usd(p.valueNow)}, ${pct(p.valueNow / total)} · ${LABEL[p.headline]}`,
  }));
  const rest = sorted.slice(top);
  const restValue = rest.reduce((s, p) => s + p.valueNow, 0);
  if (restValue > 0) {
    head.push({ key: 'other', label: `${rest.length} more`, weight: restValue / total, valueUsd: restValue, tone: 'info',
                title: `${rest.map((p) => p.symbol).join(', ')}: ${usd(restValue)}` });
  }
  if (cash > 0) head.push({ key: 'cash', label: 'Cash', weight: cash / total, valueUsd: cash, tone: 'cash', title: `Cash: ${usd(cash)}` });
  return head;
}

/** Value by sector; holdings without a sector (outside the universe) are "Not covered". */
export function sectors(list: Position[]): { sector: string; weight: number; valueUsd: number; symbols: string[] }[] {
  const total = list.reduce((s, p) => s + p.valueNow, 0);
  const m = new Map<string, { valueUsd: number; symbols: string[] }>();
  for (const p of list) {
    const k = p.sector ?? 'Not covered';
    const e = m.get(k) ?? { valueUsd: 0, symbols: [] };
    e.valueUsd += p.valueNow;
    e.symbols.push(p.symbol);
    m.set(k, e);
  }
  return [...m.entries()]
    .map(([sector, e]) => ({ sector, weight: total > 0 ? e.valueUsd / total : 0, ...e }))
    .sort((x, y) => y.valueUsd - x.valueUsd);
}

/** "01:15 PM" in New York time. */
export function nyTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit' }) : '';
}
