import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BarChart3, CalendarDays, Loader2, RefreshCw, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";

interface Metrics {
  close: number;
  return_1d: number | null;
  return_5d: number | null;
  return_20d: number | null;
  relative_return_20d_vs_qqq?: number | null;
  ma20: number;
  ma50: number;
  drawdown_60d: number | null;
  volume_ratio_20d: number | null;
  support_20d: number;
  breakout_20d: number;
  as_of: string;
}

interface Candidate {
  symbol: string;
  status: "watch" | "event_gate" | "data_unavailable" | "insufficient_history";
  score: number | null;
  score_max: number;
  metrics: Metrics | null;
  components: { name: string; score: number | null; max: number; status: string }[];
  setup: { observation: string; trigger: string; invalidation: string; instrument_note: string } | null;
  event_gates: { name: string; date: string; days_away: number }[];
  message: string;
}

interface Theme {
  key: string;
  label: string;
  return_20d: number | null;
  relative_return_20d: number | null;
  data_status: "available" | "unavailable";
  etfs: { symbol: string; metrics: Metrics | null }[];
}

interface Radar {
  generated_at: string | null;
  data_source: { name: string; grade: string; execution_safe: boolean; message?: string };
  benchmarks: Record<string, Metrics | null>;
  themes: Theme[];
  candidates: Candidate[];
  holdings: {
    data_source: string;
    as_of: string | null;
    related_themes: { theme: string; symbols: { symbol: string; strategy_count: number; near_dte: boolean; earliest_expiry: string | null }[] }[];
  };
  failures: Record<string, string>;
  limitations: string[];
}

function pct(value: number | null | undefined) {
  return value === null || value === undefined ? "--" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function num(value: number | null | undefined) {
  return value === null || value === undefined ? "--" : value.toFixed(2);
}

function Move({ value }: { value: number | null | undefined }) {
  return <span className={cn("tabular-nums", value !== null && value !== undefined && value > 0 && "text-emerald-600", value !== null && value !== undefined && value < 0 && "text-red-600")}>{pct(value)}</span>;
}

function Status({ status }: { status: Candidate["status"] }) {
  const styles = {
    watch: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
    event_gate: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    data_unavailable: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
    insufficient_history: "border-muted bg-muted text-muted-foreground",
  };
  return <span className={cn("rounded-md border px-1.5 py-0.5 text-xs font-medium", styles[status])}>{status.replace(/_/g, " ")}</span>;
}

export function MarketRadar() {
  const [data, setData] = useState<Radar | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  async function load(refresh = false) {
    refresh ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const response = await fetch("/market-radar/data", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      setData((await response.json()) as Radar);
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "Could not load market radar.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => { void load(); }, []);
  const current = useMemo(() => data?.candidates.find((item) => item.symbol === selected) ?? data?.candidates[0] ?? null, [data, selected]);

  if (loading) return <div className="flex h-[60vh] items-center justify-center text-muted-foreground"><Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading Market Radar...</div>;
  if (error || !data) return <div className="mx-auto max-w-2xl p-8 text-center"><AlertTriangle className="mx-auto h-8 w-8 text-red-500" /><p className="mt-3 text-sm text-red-600">{error ?? "No radar data."}</p><button type="button" onClick={() => void load(true)} className="mt-4 rounded-md border px-3 py-1.5 text-sm hover:bg-muted">Retry</button></div>;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight"><BarChart3 className="h-5 w-5 text-primary" /> Market Radar</h1>
          <p className="text-sm text-muted-foreground">Sector context, candidate watchlist, and position-aware setup review</p>
        </div>
        <button type="button" onClick={() => void load(true)} className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"><RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} /> Refresh</button>
      </div>

      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-800 dark:text-amber-200">
        <ShieldAlert className="mr-1.5 inline h-4 w-4" /> {data.data_source.message ?? "Public price data only."} Candidate scores rank observations; they do not authorize a trade.
      </div>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">Market benchmarks</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(data.benchmarks).map(([symbol, metrics]) => (
            <div key={symbol} className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between"><span className="font-semibold">{symbol}</span><span className="text-xs text-muted-foreground">{metrics?.as_of ?? "data unavailable"}</span></div>
              {metrics ? <div className="mt-3 grid grid-cols-3 gap-3 text-sm"><div><p className="text-xs text-muted-foreground">Close</p><p className="font-medium tabular-nums">{num(metrics.close)}</p></div><div><p className="text-xs text-muted-foreground">5D</p><Move value={metrics.return_5d} /></div><div><p className="text-xs text-muted-foreground">20D</p><Move value={metrics.return_20d} /></div></div> : <p className="mt-3 text-sm text-red-600">No usable daily history.</p>}
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border bg-card">
        <div className="border-b p-4"><h2 className="text-sm font-semibold text-muted-foreground">Sector and theme relative strength</h2></div>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground"><th className="p-3 font-medium">Theme</th><th className="p-3 font-medium">ETFs</th><th className="p-3 text-right font-medium">20D</th><th className="p-3 text-right font-medium">vs QQQ</th><th className="p-3 font-medium">Status</th></tr></thead><tbody>{data.themes.map((theme) => <tr key={theme.key} className="border-b last:border-0"><td className="p-3 font-medium">{theme.label}</td><td className="p-3 text-muted-foreground">{theme.etfs.map((etf) => etf.symbol).join(", ") || "--"}</td><td className="p-3 text-right"><Move value={theme.return_20d} /></td><td className="p-3 text-right"><Move value={theme.relative_return_20d} /></td><td className="p-3 text-xs text-muted-foreground">{theme.data_status}</td></tr>)}</tbody></table></div>
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(300px,1fr)]">
        <section className="rounded-lg border bg-card"><div className="border-b p-4"><h2 className="text-sm font-semibold text-muted-foreground">Candidate watchlist</h2></div><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground"><th className="p-3 font-medium">Symbol</th><th className="p-3 text-right font-medium">Score</th><th className="p-3 text-right font-medium">20D vs QQQ</th><th className="p-3 text-right font-medium">60D drawdown</th><th className="p-3 font-medium">State</th></tr></thead><tbody>{data.candidates.map((candidate) => <tr key={candidate.symbol} onClick={() => setSelected(candidate.symbol)} className={cn("cursor-pointer border-b last:border-0 hover:bg-muted/50", current?.symbol === candidate.symbol && "bg-muted/50")}><td className="p-3 font-medium">{candidate.symbol}</td><td className="p-3 text-right tabular-nums">{candidate.score === null ? "--" : `${candidate.score}/${candidate.score_max}`}</td><td className="p-3 text-right"><Move value={candidate.metrics?.relative_return_20d_vs_qqq} /></td><td className="p-3 text-right"><Move value={candidate.metrics?.drawdown_60d} /></td><td className="p-3"><Status status={candidate.status} /></td></tr>)}</tbody></table></div></section>

        <section className="rounded-lg border bg-card p-4"><h2 className="text-sm font-semibold text-muted-foreground">Setup card</h2>{current ? <div className="mt-4 space-y-4"><div className="flex items-center justify-between"><span className="text-lg font-semibold">{current.symbol}</span><Status status={current.status} /></div><p className="text-sm text-muted-foreground">{current.message}</p>{current.setup && <><Detail label="Observation" value={current.setup.observation} /><Detail label="Trigger" value={current.setup.trigger} /><Detail label="Invalidation" value={current.setup.invalidation} /><Detail label="Instrument" value={current.setup.instrument_note} /></>}{current.event_gates.length > 0 && <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm"><CalendarDays className="mr-1 inline h-4 w-4 text-amber-600" />{current.event_gates.map((event) => `${event.name}: ${event.date} (${event.days_away}d)`).join("; ")}</div>}<div className="border-t pt-3"><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Score inputs</p>{current.components.map((component) => <div key={component.name} className="flex justify-between py-1 text-xs"><span className="text-muted-foreground">{component.name.replace(/_/g, " ")}</span><span className="tabular-nums">{component.score === null ? "unavailable" : `${component.score}/${component.max}`}</span></div>)}</div></div> : <p className="mt-3 text-sm text-muted-foreground">No candidate data.</p>}</section>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-lg border bg-card p-4"><h2 className="text-sm font-semibold text-muted-foreground">Held-book theme exposure</h2>{data.holdings.related_themes.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">No configured theme matches in the held book.</p> : <div className="mt-3 space-y-3">{data.holdings.related_themes.map((theme) => <div key={theme.theme}><p className="text-sm font-medium">{theme.theme}</p><div className="mt-1 flex flex-wrap gap-2">{theme.symbols.map((symbol) => <span key={symbol.symbol} className={cn("rounded-md border px-2 py-1 text-xs", symbol.near_dte ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300" : "border-border text-muted-foreground")}>{symbol.symbol} · {symbol.strategy_count} strategy{symbol.strategy_count === 1 ? "" : "ies"}{symbol.near_dte ? " · near expiry" : ""}</span>)}</div></div>)}</div>}</section>
        <section className="rounded-lg border bg-card p-4"><h2 className="text-sm font-semibold text-muted-foreground">Data and event gaps</h2><ul className="mt-3 space-y-2 text-sm text-muted-foreground">{Object.entries(data.failures).map(([symbol, message]) => <li key={symbol}><span className="font-medium text-foreground">{symbol}</span>: {message}</li>)}{Object.keys(data.failures).length === 0 && <li>No public-data failures reported.</li>}{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section>
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</p><p className="mt-1 text-sm">{value}</p></div>;
}
