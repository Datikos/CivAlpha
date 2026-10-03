import { ForecastSummary, MODEL_KINDS, ModelKind } from './models';

/** Newest first: as-of date, then version, then publication time. */
export function compareForecastsDesc(a: ForecastSummary, b: ForecastSummary): number {
  return (
    b.asOfDate.localeCompare(a.asOfDate) ||
    b.version - a.version ||
    (b.issuedAt ?? '').localeCompare(a.issuedAt ?? '') ||
    b.id - a.id
  );
}

/** Latest forecast per model kind from an arbitrary list. */
export function latestByModel(list: ForecastSummary[]): Partial<Record<ModelKind, ForecastSummary>> {
  const out: Partial<Record<ModelKind, ForecastSummary>> = {};
  for (const f of [...list].sort(compareForecastsDesc)) {
    if (!out[f.modelKind]) out[f.modelKind] = f;
  }
  return out;
}

export interface CompanyForecastRow {
  symbol: string;
  companyName: string;
  benchmarkSymbol: string;
  forecasts: { kind: ModelKind; f: ForecastSummary | null }[];
}

/** Group a list of forecasts per company, BASELINE and AUGMENTED side by side. */
export function groupByCompany(list: ForecastSummary[]): CompanyForecastRow[] {
  const map = new Map<string, ForecastSummary[]>();
  for (const f of list) {
    const arr = map.get(f.symbol) ?? [];
    arr.push(f);
    map.set(f.symbol, arr);
  }
  return [...map.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([symbol, fs]) => {
      const latest = latestByModel(fs);
      return {
        symbol,
        companyName: fs[0].companyName,
        benchmarkSymbol: fs[0].benchmarkSymbol,
        forecasts: MODEL_KINDS.map((kind) => ({ kind, f: latest[kind] ?? null })),
      };
    });
}
