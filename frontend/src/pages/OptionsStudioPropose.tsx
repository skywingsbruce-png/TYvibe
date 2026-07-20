import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  Ban,
  Loader2,
  Plus,
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Severity = "ALLOW" | "WATCH" | "BLOCK";
type PriceBasis = "verified_quote" | "user_estimate" | "unknown";

interface LegForm {
  underlying: string;
  right: "call" | "put";
  strike: string;
  expiry: string;
  quantity: string;
  premium: string;
  price_basis: PriceBasis;
}

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
  payoff: Payoff;
}

interface PortfolioRisk {
  total_defined_max_loss: number;
  unbounded_loss_strategies: number;
  near_dte_risk: number;
  strategies: StrategyRisk[];
}

interface Verdict {
  rule_id: string;
  severity: Severity;
  message: string;
}

interface CardPayload {
  mode: "propose";
  label: string;
  price_basis: PriceBasis;
  overall: Severity;
  proposed: PortfolioRisk;
  held_before: PortfolioRisk;
  portfolio_after: PortfolioRisk;
  rules_candidate: { overall: Severity; verdicts: Verdict[] };
  rules_portfolio: { overall: Severity; verdicts: Verdict[] };
  deltas: {
    total_defined_max_loss: number;
    unbounded_loss_strategies: number;
    near_dte_risk: number;
    held_option_count: number;
    proposed_option_count: number;
  };
  data_source: "sample" | "real";
  held_risk_usable: boolean;
  error: null;
}

interface ErrorPayload {
  error: { code: string; message: string };
}

type ProposeResponse = CardPayload | ErrorPayload;

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

function SevBadge({ severity, big }: { severity: Severity; big?: boolean }) {
  const Icon = severity === "ALLOW" ? ShieldCheck : severity === "WATCH" ? ShieldAlert : ShieldX;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border font-semibold",
        big ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs",
        SEV_STYLES[severity],
      )}
    >
      <Icon className={big ? "h-4 w-4" : "h-3.5 w-3.5"} />
      {severity}
    </span>
  );
}

function humanType(t: string): string {
  return t.replace(/_/g, " ");
}

const EMPTY_LEG: LegForm = {
  underlying: "",
  right: "call",
  strike: "",
  expiry: "",
  quantity: "1",
  premium: "",
  price_basis: "user_estimate",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function OptionsStudioPropose() {
  const [label, setLabel] = useState("My candidate trade");
  const [legs, setLegs] = useState<LegForm[]>([{ ...EMPTY_LEG }]);
  const [card, setCard] = useState<CardPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function updateLeg(i: number, patch: Partial<LegForm>) {
    setLegs((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  }

  function addLeg() {
    setLegs((prev) => [...prev, { ...EMPTY_LEG }]);
  }

  function removeLeg(i: number) {
    setLegs((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev));
  }

  async function check() {
    setLoading(true);
    setError(null);
    setCard(null);
    try {
      const body = {
        label,
        legs: legs.map((l) => ({
          underlying: l.underlying.trim().toUpperCase(),
          right: l.right,
          strike: Number(l.strike),
          expiry: l.expiry,
          quantity: Number(l.quantity),
          premium: l.premium.trim() === "" ? null : Number(l.premium),
          price_basis: l.price_basis,
        })),
      };
      const res = await fetch("/options-studio/propose", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`API returned ${res.status}`);
      const payload = (await res.json()) as ProposeResponse;
      if ("error" in payload && payload.error) {
        setError(payload.error.message);
      } else {
        setCard(payload as CardPayload);
      }
    } catch (err) {
      setError(err instanceof Error ? `${err.message}. Is the backend running?` : "Request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight">
            <ShieldAlert className="h-5 w-5 text-primary" />
            Proposed Trade Authorization
          </h1>
          <p className="text-sm text-muted-foreground">
            Read-only pre-trade check · NOT an order · candidate is judged in strict PROPOSE mode
          </p>
        </div>
        <Link
          to="/options-studio"
          className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to review
        </Link>
      </div>

      {/* Leg builder */}
      <div className="rounded-lg border bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted-foreground">Candidate legs</h2>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="w-64 rounded-md border bg-background px-2 py-1 text-sm"
            placeholder="Label"
          />
        </div>
        <div className="space-y-2">
          {legs.map((l, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <input
                value={l.underlying}
                onChange={(e) => updateLeg(i, { underlying: e.target.value })}
                placeholder="Underlying"
                className="w-24 rounded-md border bg-background px-2 py-1 text-sm uppercase"
              />
              <select
                value={l.right}
                onChange={(e) => updateLeg(i, { right: e.target.value as "call" | "put" })}
                className="rounded-md border bg-background px-2 py-1 text-sm"
              >
                <option value="call">Call</option>
                <option value="put">Put</option>
              </select>
              <input
                value={l.strike}
                onChange={(e) => updateLeg(i, { strike: e.target.value })}
                placeholder="Strike"
                inputMode="decimal"
                className="w-20 rounded-md border bg-background px-2 py-1 text-sm tabular-nums"
              />
              <input
                value={l.expiry}
                onChange={(e) => updateLeg(i, { expiry: e.target.value })}
                placeholder="YYYY-MM-DD"
                className="w-32 rounded-md border bg-background px-2 py-1 text-sm tabular-nums"
              />
              <input
                value={l.quantity}
                onChange={(e) => updateLeg(i, { quantity: e.target.value })}
                placeholder="Qty (+/-)"
                inputMode="numeric"
                className="w-20 rounded-md border bg-background px-2 py-1 text-sm tabular-nums"
                title="Signed: + long, - short"
              />
              <input
                value={l.premium}
                onChange={(e) => updateLeg(i, { premium: e.target.value })}
                placeholder="Premium"
                inputMode="decimal"
                className="w-24 rounded-md border bg-background px-2 py-1 text-sm tabular-nums"
              />
              <select
                value={l.price_basis}
                onChange={(e) => updateLeg(i, { price_basis: e.target.value as PriceBasis })}
                className="rounded-md border bg-background px-2 py-1 text-sm"
                title="Price provenance"
              >
                <option value="verified_quote">verified_quote</option>
                <option value="user_estimate">user_estimate</option>
                <option value="unknown">unknown</option>
              </select>
              <button
                onClick={() => removeLeg(i)}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-danger"
                title="Remove leg"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={addLeg}
            className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" /> Add leg
          </button>
          <button
            onClick={() => void check()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            Check authorization
          </button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          A premium marked <code>user_estimate</code> (or left blank ⇒ <code>unknown</code>) can never be approved —
          WATCH at best, never ALLOW. Nothing is ever placed; this is decision support only.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4 text-sm text-red-600 dark:text-red-400">
          <Ban className="mr-1.5 inline h-4 w-4" />
          {error}
        </div>
      )}

      {card && <ProposalCard card={card} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

function priceBasisBanner(basis: PriceBasis) {
  if (basis === "verified_quote") return null;
  const text =
    basis === "user_estimate"
      ? "ASSUMED PRICE (user estimate) — payoff below is hypothetical. This candidate cannot be approved on estimated prices (WATCH at best)."
      : "PRICE UNAVAILABLE (unknown) — no premium supplied; payoff / IV / Greeks are unavailable and nothing is fabricated. Cannot be approved.";
  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm font-medium text-amber-700 dark:text-amber-300">
      ⚠ {text}
    </div>
  );
}

function ProposalCard({ card }: { card: CardPayload }) {
  const d = card.deltas;
  return (
    <div className="space-y-4">
      {/* Decision */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4">
        <div>
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Decision · {card.label}</div>
          <div className="mt-1 flex items-center gap-2">
            <SevBadge severity={card.overall} big />
            <span className="text-sm text-muted-foreground">
              price basis: <span className="font-medium">{card.price_basis}</span> · held book:{" "}
              <span className="font-medium">{card.data_source}</span>
              {!card.held_risk_usable && <span className="text-red-600"> (held not usable)</span>}
            </span>
          </div>
        </div>
        <div className="text-xs text-muted-foreground">
          candidate: <SevBadge severity={card.rules_candidate.overall} /> · portfolio impact:{" "}
          <SevBadge severity={card.rules_portfolio.overall} />
        </div>
      </div>

      {priceBasisBanner(card.price_basis)}

      {/* Candidate standalone risk */}
      <div className="rounded-lg border bg-card">
        <div className="border-b p-3">
          <h2 className="text-sm font-semibold text-muted-foreground">Candidate standalone risk</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="p-3 font-medium">Strategy</th>
                <th className="p-3 font-medium">Underlying</th>
                <th className="p-3 font-medium">DTE</th>
                <th className="p-3 text-right font-medium">Max loss</th>
                <th className="p-3 text-right font-medium">Max profit</th>
                <th className="p-3 font-medium">Breakevens</th>
              </tr>
            </thead>
            <tbody>
              {card.proposed.strategies.map((s) => (
                <tr key={s.strategy_id} className="border-b last:border-0">
                  <td className="p-3 capitalize">{humanType(s.strategy_type)}</td>
                  <td className="p-3 font-medium">{s.underlying}</td>
                  <td className="p-3 tabular-nums">{s.dte ?? "—"}</td>
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Incremental impact */}
      <div className="rounded-lg border bg-card p-4">
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
          Incremental portfolio impact (held → held + candidate)
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="p-2 font-medium">Metric</th>
                <th className="p-2 text-right font-medium">Before (held)</th>
                <th className="p-2 text-right font-medium">After</th>
                <th className="p-2 text-right font-medium">Delta</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b">
                <td className="p-2">Total defined max loss</td>
                <td className="p-2 text-right tabular-nums">{money(card.held_before.total_defined_max_loss)}</td>
                <td className="p-2 text-right tabular-nums">{money(card.portfolio_after.total_defined_max_loss)}</td>
                <td className="p-2 text-right tabular-nums">{money(d.total_defined_max_loss)}</td>
              </tr>
              <tr className="border-b">
                <td className="p-2">Unbounded-loss strategies</td>
                <td className="p-2 text-right tabular-nums">{card.held_before.unbounded_loss_strategies}</td>
                <td className="p-2 text-right tabular-nums">{card.portfolio_after.unbounded_loss_strategies}</td>
                <td className="p-2 text-right tabular-nums">
                  {d.unbounded_loss_strategies > 0 ? `+${d.unbounded_loss_strategies}` : d.unbounded_loss_strategies}
                </td>
              </tr>
              <tr>
                <td className="p-2">Near-DTE risk</td>
                <td className="p-2 text-right tabular-nums">{money(card.held_before.near_dte_risk)}</td>
                <td className="p-2 text-right tabular-nums">{money(card.portfolio_after.near_dte_risk)}</td>
                <td className="p-2 text-right tabular-nums">{money(d.near_dte_risk)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Held option lines: {d.held_option_count} (unchanged) · candidate option lines: {d.proposed_option_count}{" "}
          (kept separate)
        </p>
      </div>

      {/* Rule tables */}
      <div className="grid gap-3 lg:grid-cols-2">
        <RuleTable title="Candidate rules (PROPOSE — cap is a hard block)" report={card.rules_candidate} />
        <RuleTable title="Portfolio-impact rules (REVIEW — held size not blocked)" report={card.rules_portfolio} />
      </div>

      <p className="text-xs text-muted-foreground">
        Read-only. The rules engine outranks any LLM or persona. A BLOCK is final; resolve it before you place anything
        yourself in your broker. Greeks / IV / prices are unavailable unless a real market-data provider is configured
        — never simulated.
      </p>
    </div>
  );
}

function RuleTable({ title, report }: { title: string; report: { overall: Severity; verdicts: Verdict[] } }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted-foreground">{title}</h3>
        <SevBadge severity={report.overall} />
      </div>
      <div className="space-y-1.5">
        {report.verdicts.map((v) => (
          <div key={v.rule_id} className="flex items-start gap-2 text-sm">
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
    </div>
  );
}
