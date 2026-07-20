import { Fragment, useEffect, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Loader2,
  RefreshCw,
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  Layers,
  Ban,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types (mirror of the read-only /options-studio/review payload)
// ---------------------------------------------------------------------------

type Severity = "ALLOW" | "WATCH" | "BLOCK";

interface Payoff {
  available: boolean;
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  unbounded_profit: boolean;
  unbounded_loss: boolean;
}

interface StrategyRisk {
  strategy_id: string;
  strategy_type: string;
  underlying: string;
  dte: number | null;
  multiplier: number;
  payoff: Payoff;
  greeks: { available: boolean };
  scenarios: { available: boolean };
}

interface Verdict {
  rule_id: string;
  severity: Severity;
  message: string;
}

interface Issue {
  code: string;
  message: string;
  context?: string;
}

interface OptionLegDetail {
  quantity: number;
  contract: {
    underlying: string;
    right: "call" | "put";
    strike: number;
    expiry: string;
    multiplier: number;
    average_cost: number | null;
    market_value: number | null;
  };
}

interface StockLegDetail {
  symbol: string;
  quantity: number;
  average_cost: number | null;
  market_value: number | null;
}

interface StrategyDetail {
  strategy_id: string;
  strategy_type: string;
  underlying: string;
  option_legs: OptionLegDetail[];
  stock_legs: StockLegDetail[];
  note: string | null;
}

interface ReviewPayload {
  data_source: "sample" | "real";
  risk_usable: boolean;
  blocking_issues: Issue[];
  error: { code: string; message: string } | null;
  snapshot: {
    account_label: string;
    as_of: string;
    base_currency: string;
    cash: number;
    source_label: string;
    underlyings: unknown[];
    options: unknown[];
    trade_lots: unknown[];
    strategies: StrategyDetail[];
  } | null;
  portfolio_risk: {
    total_defined_max_loss: number;
    unbounded_loss_strategies: number;
    indeterminate_risk_strategies: number;
    unclassified_strategies: number;
    near_dte_risk: number;
    concentration_by_underlying: { underlying: string; fraction: number }[];
    strategies: StrategyRisk[];
  } | null;
  rules: { overall: Severity; verdicts: Verdict[] } | null;
  warnings: Issue[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function payoffLoss(p: Payoff): string {
  if (!p.available) return "unavailable";
  if (p.unbounded_loss || p.max_loss === null) return "unbounded";
  return money(p.max_loss);
}

function payoffProfit(p: Payoff): string {
  if (!p.available) return "unavailable";
  if (p.unbounded_profit || p.max_profit === null) return "unbounded";
  return money(p.max_profit);
}

const SEV_STYLES: Record<Severity, string> = {
  ALLOW: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
  WATCH: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
  BLOCK: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30",
};

function SevBadge({ severity }: { severity: Severity }) {
  const Icon = severity === "ALLOW" ? ShieldCheck : severity === "WATCH" ? ShieldAlert : ShieldX;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold",
        SEV_STYLES[severity],
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {severity}
    </span>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

function humanType(t: string): string {
  return t.replace(/_/g, " ");
}

function signedQuantity(quantity: number): string {
  return quantity > 0 ? `+${quantity}` : String(quantity);
}

function optionLegLabel(leg: OptionLegDetail): string {
  const right = leg.contract.right === "call" ? "C" : "P";
  return `${signedQuantity(leg.quantity)} ${right} ${leg.contract.strike} | ${leg.contract.expiry} | x${leg.contract.multiplier}`;
}

// Persistent, sticky top banner. Always visible while scrolling so the data
// provenance (sample / real-unusable) can never be mistaken.
function StickyBanner({ kind, children }: { kind: "sample" | "danger"; children: ReactNode }) {
  return (
    <div
      className={cn(
        "sticky top-0 z-20 -mx-6 mb-4 border-b px-6 py-2 text-sm font-medium backdrop-blur",
        kind === "sample"
          ? "border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-300"
          : "border-red-500/40 bg-red-500/15 text-red-700 dark:text-red-300",
      )}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function OptionsStudio() {
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedStrategies, setExpandedStrategies] = useState<Set<string>>(new Set());

  async function load(mode: "initial" | "refresh" = "refresh") {
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      // Single source: the read-only API (which derives the sample from the
      // bundled sample CSV). No client-side sample fallback — if the backend is
      // unreachable we show an error, never stale/sample data.
      const res = await fetch("/options-studio/review", { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`API returned ${res.status}`);
      const payload = (await res.json()) as ReviewPayload;
      setData(payload);
    } catch (err) {
      setData(null);
      setError(
        err instanceof Error
          ? `Could not load review data (${err.message}). Is the backend running?`
          : "Could not load review data.",
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void load("initial");
  }, []);

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading Options Studio…
      </div>
    );
  }

  // Backend unreachable / hard failure — never fall back to sample.
  if (error || !data) {
    return (
      <div className="mx-auto max-w-2xl p-8 text-center">
        <Ban className="mx-auto h-8 w-8 text-red-500" />
        <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error ?? "No data."}</p>
        <button
          onClick={() => void load("refresh")}
          className="mt-4 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Retry
        </button>
      </div>
    );
  }

  const isSample = data.data_source === "sample";
  const realUnusable = data.data_source === "real" && !data.risk_usable;
  const { snapshot, portfolio_risk: risk, rules, warnings } = data;
  const strategyDetails = new Map(snapshot?.strategies.map((strategy) => [strategy.strategy_id, strategy]));

  function toggleStrategy(strategyId: string) {
    setExpandedStrategies((current) => {
      const next = new Set(current);
      if (next.has(strategyId)) next.delete(strategyId);
      else next.add(strategyId);
      return next;
    });
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      {/* Persistent provenance banner (sticky) */}
      {isSample && (
        <StickyBanner kind="sample">
          SAMPLE DATA (fictional) — not your positions. Drop a real IBKR export at
          <code className="mx-1 rounded bg-black/10 px-1 py-0.5 text-xs dark:bg-white/10">
            data/private/ibkr_statement.csv
          </code>
          and refresh.
        </StickyBanner>
      )}
      {realUnusable && (
        <StickyBanner kind="danger">
          真实数据不可用于风险汇总 · REAL DATA NOT USABLE FOR RISK — the parser reported issues below. Fix the parser
          first; the numbers here are NOT trustworthy.
        </StickyBanner>
      )}

      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight">
            <Layers className="h-5 w-5 text-primary" />
            Options Risk Studio
          </h1>
          <p className="text-sm text-muted-foreground">
            Held-book review · local &amp; read-only · not sent to any LLM or broker
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "rounded-md border px-2 py-1 text-xs font-medium",
              isSample
                ? "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                : realUnusable
                  ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
                  : "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
            )}
          >
            {isSample ? "SAMPLE DATA (fictional)" : realUnusable ? "REAL — NOT USABLE" : "Your data"}
          </span>
          <Link
            to="/options-studio/propose"
            className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <ShieldAlert className="h-3.5 w-3.5" /> Propose a trade
          </Link>
          <button
            onClick={() => void load("refresh")}
            className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} /> Refresh
          </button>
        </div>
      </div>

      {/* Blocking issues (real data unusable / parse failure) */}
      {(realUnusable || data.error) && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-red-600 dark:text-red-400">
            <Ban className="h-4 w-4" /> Why this real statement is not usable for risk
          </h2>
          {data.error && (
            <p className="mb-2 text-sm text-red-600 dark:text-red-400">{data.error.message}</p>
          )}
          <ul className="space-y-1 text-sm">
            {data.blocking_issues.map((b, i) => (
              <li key={i}>
                <span className="font-medium">{b.code}</span>
                <span className="text-muted-foreground"> — {b.message}</span>
                {b.context && <span className="text-muted-foreground"> ({b.context})</span>}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">
            The parser must be fixed before these positions can be trusted. No sample data is substituted.
          </p>
        </div>
      )}

      {/* If the statement did not parse at all, stop here (no tables). */}
      {!snapshot || !risk || !rules ? null : (
        <>
          {/* Rules verdict */}
          <div className={cn("rounded-lg border bg-card p-4", realUnusable && "opacity-60")}>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-muted-foreground">Risk rules — held-book review</h2>
              <SevBadge severity={rules.overall} />
            </div>
            <div className="space-y-1.5">
              {rules.verdicts.map((v) => (
                <div key={v.rule_id} className="flex items-start gap-3 text-sm">
                  <div className="w-16 shrink-0">
                    <SevBadge severity={v.severity} />
                  </div>
                  <div>
                    <span className="font-medium">{v.rule_id.replace(/_/g, " ")}</span>
                    <span className="text-muted-foreground"> — {v.message}</span>
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              In review mode, a held position over your per-trade preference is a WATCH exposure alert — not a block.
              Held BLOCKs are structural only (real-trading toggle, unbounded loss, or an incomplete portfolio).
            </p>
          </div>

          {/* Parse summary tiles */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Tile label="Stocks" value={String(snapshot.underlyings.length)} />
            <Tile label="Options" value={String(snapshot.options.length)} />
            <Tile label="Cash" value={money(snapshot.cash)} />
            <Tile label="Trades" value={String(snapshot.trade_lots.length)} />
            <Tile label="Strategies" value={String(risk.strategies.length)} />
            <Tile
              label="Total max loss"
              value={money(risk.total_defined_max_loss)}
              hint={risk.unbounded_loss_strategies ? `+${risk.unbounded_loss_strategies} unbounded` : "defined risk"}
            />
          </div>

          {/* Strategies */}
          <div className="rounded-lg border bg-card">
            <div className="border-b p-4">
              <h2 className="text-sm font-semibold text-muted-foreground">
                Recognized strategies · {risk.strategies.length}
                {risk.unclassified_strategies > 0 && (
                  <span className="ml-2 text-red-600">({risk.unclassified_strategies} unclassified)</span>
                )}
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="p-3 font-medium">Underlying</th>
                    <th className="p-3 font-medium">Strategy</th>
                    <th className="p-3 font-medium">DTE</th>
                    <th className="p-3 text-right font-medium">Max loss</th>
                    <th className="p-3 text-right font-medium">Max profit</th>
                    <th className="p-3 font-medium">Breakevens</th>
                    <th className="p-3 font-medium">Greeks</th>
                  </tr>
                </thead>
                <tbody>
                  {risk.strategies.map((s) => {
                    const near = s.dte !== null && s.dte <= 14 && s.dte >= 0;
                    const detail = strategyDetails.get(s.strategy_id);
                    const expanded = expandedStrategies.has(s.strategy_id);
                    return (
                      <Fragment key={s.strategy_id}>
                      <tr className="border-b hover:bg-muted/40">
                        <td className="p-3 font-medium">
                          <button
                            type="button"
                            onClick={() => toggleStrategy(s.strategy_id)}
                            className="inline-flex items-center gap-1.5 text-left hover:text-primary"
                            aria-expanded={expanded}
                            aria-controls={`strategy-${s.strategy_id}`}
                          >
                            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            {s.underlying}
                          </button>
                        </td>
                        <td className="p-3 capitalize">{humanType(s.strategy_type)}</td>
                        <td className="p-3">
                          <span className={cn("tabular-nums", near && "font-semibold text-amber-600")}>
                            {s.dte ?? "—"}
                          </span>
                        </td>
                        <td className="p-3 text-right tabular-nums">
                          <span className={s.payoff.unbounded_loss ? "font-semibold text-red-600" : undefined}>
                            {payoffLoss(s.payoff)}
                          </span>
                        </td>
                        <td className="p-3 text-right tabular-nums">{payoffProfit(s.payoff)}</td>
                        <td className="p-3 tabular-nums text-muted-foreground">
                          {s.payoff.available && s.payoff.breakevens.length
                            ? s.payoff.breakevens.map((b) => b.toFixed(2)).join(", ")
                            : "—"}
                        </td>
                        <td className="p-3 text-xs text-muted-foreground">
                          {s.greeks.available ? "available" : "unavailable"}
                        </td>
                      </tr>
                      {expanded && (
                        <tr id={`strategy-${s.strategy_id}`} className="border-b bg-muted/25">
                          <td colSpan={7} className="p-4">
                            {!detail ? (
                              <p className="text-sm text-amber-700 dark:text-amber-300">
                                Strategy detail is unavailable. Reconcile the statement again before relying on this classification.
                              </p>
                            ) : (
                              <div className="grid gap-4 lg:grid-cols-2">
                                <div>
                                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                    Option legs ({detail.option_legs.length})
                                  </p>
                                  {detail.option_legs.length === 0 ? (
                                    <p className="mt-2 text-sm text-muted-foreground">No option legs assigned.</p>
                                  ) : (
                                    <ul className="mt-2 space-y-1.5 text-sm">
                                      {detail.option_legs.map((leg, index) => (
                                        <li key={index} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                          <span className="font-mono font-medium">{optionLegLabel(leg)}</span>
                                          <span className="text-xs text-muted-foreground">
                                            avg cost {leg.contract.average_cost ?? "unavailable"} | market value {leg.contract.market_value ?? "unavailable"}
                                          </span>
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                </div>
                                <div>
                                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                    Stock legs ({detail.stock_legs.length})
                                  </p>
                                  {detail.stock_legs.length === 0 ? (
                                    <p className="mt-2 text-sm text-muted-foreground">No shares assigned to this strategy.</p>
                                  ) : (
                                    <ul className="mt-2 space-y-1.5 text-sm">
                                      {detail.stock_legs.map((leg, index) => (
                                        <li key={index} className="text-sm">
                                          <span className="font-mono font-medium">
                                            {signedQuantity(leg.quantity)} {leg.symbol} shares
                                          </span>
                                          <span className="ml-2 text-xs text-muted-foreground">
                                            avg cost {leg.average_cost ?? "unavailable"} | market value {leg.market_value ?? "unavailable"}
                                          </span>
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                </div>
                                <p className="lg:col-span-2 text-xs text-muted-foreground">
                                  Classification is deterministic from the displayed legs. Verify these match your intended structure before using payoff fields.
                                  {detail.note ? ` Note: ${detail.note}` : ""}
                                </p>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Concentration + warnings */}
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-lg border bg-card p-4">
              <h2 className="mb-3 text-sm font-semibold text-muted-foreground">Concentration by underlying</h2>
              <div className="space-y-2">
                {risk.concentration_by_underlying.length === 0 && (
                  <p className="text-sm text-muted-foreground">No defined-risk concentration to show.</p>
                )}
                {risk.concentration_by_underlying.map((c) => (
                  <div key={c.underlying}>
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="font-medium">{c.underlying}</span>
                      <span className="tabular-nums text-muted-foreground">{(c.fraction * 100).toFixed(0)}%</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn("h-full rounded-full", c.fraction > 0.35 ? "bg-amber-500" : "bg-primary")}
                        style={{ width: `${Math.min(100, c.fraction * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-3 text-xs text-muted-foreground">
                Near-14-DTE risk: <span className="font-medium">{money(risk.near_dte_risk)}</span>
              </div>
            </div>

            <div className="rounded-lg border bg-card p-4">
              <h2 className="mb-3 text-sm font-semibold text-muted-foreground">Warnings · {warnings.length}</h2>
              {warnings.length === 0 ? (
                <p className="text-sm text-emerald-600 dark:text-emerald-400">No parse/reconciliation warnings.</p>
              ) : (
                <ul className="space-y-2">
                  {warnings.map((w, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                      <span>
                        <span className="font-medium">{w.code}</span>
                        <span className="text-muted-foreground"> — {w.message}</span>
                        {w.context && <span className="text-muted-foreground"> ({w.context})</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-xs text-muted-foreground">
                Greeks, IV, and live prices are shown as <em>unavailable</em> unless a real market-data provider is
                configured — never simulated.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
