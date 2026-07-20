import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Ban,
  ChevronDown,
  ChevronRight,
  FileText,
  Layers,
  Link2,
  Loader2,
  Lock,
  MessageSquare,
  RefreshCw,
  Wifi,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types (mirror of GET /market-notes/data)
// ---------------------------------------------------------------------------

type Direction = "bullish" | "bearish" | "neutral" | "conditional" | "unknown";
type Privacy = "offline" | "enabled_with_remote_consent" | "enabled_missing_consent";

interface Note {
  note_id: string;
  author: string;
  timestamp: string;
  timestamp_parsed: string | null;
  timestamp_ok: boolean;
  symbols: string[];
  direction: Direction;
  content: string;
  key_levels: string[];
  horizon: string;
  setup_condition: string;
  invalidation_condition: string;
  excerpt: string;
  source_file: string;
  source_ref: string;
  outcome_1d: string;
  outcome_1w: string;
  outcome_1m: string;
  theme_associations?: ThemeAssociation[];
}

interface ThemeAssociation {
  theme_key: string;
  label: string;
  association_types: ("direct_symbol" | "configured_keyword")[];
  matched_symbols: string[];
  matched_keywords: string[];
}

interface ThemeSummary {
  label: string;
  members: string[];
  note_count: number;
  held_symbols: string[];
  strategy_count: number;
  earliest_expiry: string | null;
  has_near_dte: boolean;
}

interface ImportStats {
  files: {
    file: string;
    kind: string;
    raw_messages: number;
    structured_notes: number;
    parseable_timestamps: number;
    skipped: number;
    skip_reasons: Record<string, number>;
    channel_ref: string | null;
    distinct_authors: number;
  }[];
  errors: { file: string; message: string }[];
  totals: { files: number; raw_messages: number; structured_notes: number; parseable_timestamps: number; skipped: number };
  authors: { distinct: number; per_author: { author_ref: string; messages: number }[] };
  channels: { distinct: number; refs: string[] };
}

interface Citation {
  note_id: string;
  excerpt: string;
  source_file: string;
  source_ref: string;
}
interface Conclusion {
  conclusion_id: string;
  text: string;
  origin: "deterministic" | "llm";
  needs_review: boolean;
  citations: Citation[];
}
interface Conflict {
  symbol: string;
  directions: string[];
  note_ids: string[];
}
interface SourceFile {
  name: string;
  kind: string;
  imported_at: string;
  message_count: number;
  sha256: string;
}
interface HeldStrategy {
  strategy_id: string;
  type: string;
  dte: number | null;
  earliest_expiry: string | null;
  legs: { right: string; strike: number; expiry: string; quantity: number }[];
}
interface HoldingsEntry {
  count: number;
  strategies: HeldStrategy[];
  earliest_expiry: string | null;
  has_near_dte: boolean;
}
interface CurrentCard {
  available: boolean;
  message: string | null;
  windows: Record<string, string>;
  included_count: number;
  earliest: string | null;
  latest: string | null;
  excluded_unknown_time: number;
  excluded_stale: number;
  conclusions: Conclusion[];
  conflicts: Conflict[];
}
interface Bundle {
  sources: SourceFile[];
  import_errors: { file: string; message: string }[];
  notes: Note[];
  current: CurrentCard;
  holdings: { data_source: string; as_of: string | null; by_symbol: Record<string, HoldingsEntry> };
  themes?: Record<string, ThemeSummary>;
  import_stats?: ImportStats;
  llm_enabled: boolean;
  llm_privacy: Privacy;
  note_count: number;
  source_count: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DIR_STYLES: Record<Direction, string> = {
  bullish: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
  bearish: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30",
  neutral: "bg-muted text-muted-foreground border-border",
  conditional: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
  unknown: "bg-muted text-muted-foreground/70 border-border",
};

function DirBadge({ direction }: { direction: Direction }) {
  return <span className={cn("rounded-md border px-1.5 py-0.5 text-xs font-medium", DIR_STYLES[direction])}>{direction}</span>;
}

function fmtTime(iso: string | null): string {
  if (!iso || iso === "unknown") return "unknown";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function PrivacyBanner({ privacy }: { privacy: Privacy }) {
  if (privacy === "enabled_with_remote_consent") {
    return (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm font-medium text-amber-700 dark:text-amber-300">
        <Wifi className="mr-1.5 inline h-4 w-4" />
        LLM 已启用并授权：<strong>原文摘录会发送至已配置的远端 LLM</strong>（Note excerpts WILL be sent to the configured
        remote LLM). It only rephrases/cites — never computes numbers or trade actions.
      </div>
    );
  }
  const text =
    privacy === "enabled_missing_consent"
      ? "LLM flag is on but remote consent is missing — running fully offline. 原文仅在本机处理。"
      : "原文仅在本机处理 · Notes are processed only on this machine (offline).";
  return (
    <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm text-emerald-700 dark:text-emerald-300">
      <Lock className="mr-1.5 inline h-4 w-4" />
      {text}
    </div>
  );
}

function ExpandableCitations({ citations, open }: { citations: Citation[]; open: boolean }) {
  if (!open) return null;
  return (
    <div className="ml-6 mt-2 space-y-1">
      {citations.length === 0 ? (
        <p className="text-xs text-amber-600">No citation — not shown as a formal conclusion.</p>
      ) : (
        citations.map((cite, i) => (
          <div key={i} className="rounded bg-muted/50 p-2 text-xs">
            <span className="font-mono text-[10px] text-muted-foreground">
              {cite.source_file} · {cite.source_ref} · {cite.note_id}
            </span>
            <p className="mt-0.5 italic">“{cite.excerpt}”</p>
          </div>
        ))
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function MarketNotes() {
  const [data, setData] = useState<Bundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fAuthor, setFAuthor] = useState("all");
  const [fSymbol, setFSymbol] = useState("all");
  const [fDirection, setFDirection] = useState("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  async function load(mode: "initial" | "refresh" = "refresh") {
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const res = await fetch("/market-notes/data", { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`API returned ${res.status}`);
      setData((await res.json()) as Bundle);
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? `${err.message}. Is the backend running?` : "Failed to load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void load("initial");
  }, []);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const authors = useMemo(() => Array.from(new Set((data?.notes ?? []).map((n) => n.author))).sort(), [data]);
  const symbols = useMemo(() => Array.from(new Set((data?.notes ?? []).flatMap((n) => n.symbols))).sort(), [data]);
  const filtered = useMemo(
    () =>
      (data?.notes ?? []).filter((n) => {
        if (fAuthor !== "all" && n.author !== fAuthor) return false;
        if (fSymbol !== "all" && !n.symbols.includes(fSymbol)) return false;
        if (fDirection !== "all" && n.direction !== fDirection) return false;
        return true;
      }),
    [data, fAuthor, fSymbol, fDirection],
  );

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading Market Notes…
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="mx-auto max-w-2xl p-8 text-center">
        <Ban className="mx-auto h-8 w-8 text-red-500" />
        <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error ?? "No data."}</p>
        <button onClick={() => void load("refresh")} className="mt-4 rounded-md border px-3 py-1.5 text-sm hover:bg-muted">
          Retry
        </button>
      </div>
    );
  }

  const holdings = data.holdings.by_symbol;
  const current = data.current;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight">
            <MessageSquare className="h-5 w-5 text-primary" />
            Market Notes · 观点审计
          </h1>
          <p className="text-sm text-muted-foreground">Local, read-only opinion audit · Discord JSON / Telegram / manual</p>
        </div>
        <button
          onClick={() => void load("refresh")}
          className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} /> Refresh
        </button>
      </div>

      <PrivacyBanner privacy={data.llm_privacy} />

      {data.note_count === 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-700 dark:text-amber-300">
          No notes found. v1 supports <strong>DiscordChatExporter JSON</strong> (export your channel as JSON), plus
          Telegram text and manual <code>.md</code>/<code>.txt</code>. Drop files into
          <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">data/private/market-notes/</code> and refresh.
        </div>
      )}

      {data.import_errors.length > 0 && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600 dark:text-red-400">
          {data.import_errors.map((e, i) => (
            <div key={i}>
              <AlertTriangle className="mr-1 inline h-3.5 w-3.5" /> {e.file}: {e.message}
            </div>
          ))}
        </div>
      )}

      {/* Sources */}
      <div className="rounded-lg border bg-card p-4">
        <h2 className="mb-2 text-sm font-semibold text-muted-foreground">Imported sources · {data.source_count}</h2>
        <div className="space-y-1 text-sm">
          {data.sources.map((s) => (
            <div key={s.name} className="flex flex-wrap items-center gap-2 text-muted-foreground">
              <FileText className="h-3.5 w-3.5" />
              <span className="font-medium text-foreground">{s.name}</span>
              <span className="rounded bg-muted px-1.5 py-0.5 text-xs">{s.kind}</span>
              <span className="text-xs">{s.message_count} msgs</span>
              <span className="text-xs">imported {fmtTime(s.imported_at)}</span>
              <span className="font-mono text-[10px]">sha256:{s.sha256.slice(0, 12)}…</span>
            </div>
          ))}
        </div>
      </div>

      {/* Import status (de-identified) */}
      {data.import_stats && data.source_count > 0 && (
        <ImportStatusCard stats={data.import_stats} />
      )}

      {/* Configured theme associations */}
      {data.themes && Object.keys(data.themes).length > 0 && (
        <ThemesCard themes={data.themes} />
      )}

      {/* Current conclusions (time-windowed) */}
      <div className="rounded-lg border bg-card p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground">Current conclusions (time-windowed)</h2>
          <span className="text-xs text-muted-foreground">
            windows: intraday {current.windows.intraday} · days {current.windows.days} · weeks {current.windows.weeks}
          </span>
        </div>

        {!current.available ? (
          <p className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-700 dark:text-amber-300">
            {current.message}
            <span className="mt-1 block text-xs text-muted-foreground">
              excluded: {current.excluded_stale} stale · {current.excluded_unknown_time} unknown-time (kept in history below)
            </span>
          </p>
        ) : (
          <>
            <div className="mb-3 text-xs text-muted-foreground">
              {current.included_count} opinion(s) in window · {fmtTime(current.earliest)} → {fmtTime(current.latest)} ·
              excluded {current.excluded_stale} stale / {current.excluded_unknown_time} unknown-time
            </div>
            {current.conflicts.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-2">
                {current.conflicts.map((c) => (
                  <span
                    key={c.symbol}
                    className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300"
                  >
                    观点冲突 · {c.symbol} ({c.directions.join(" / ")})
                  </span>
                ))}
              </div>
            )}
            <div className="space-y-2">
              {current.conclusions.map((c) => {
                const id = `concl:${c.conclusion_id}`;
                return (
                  <div key={c.conclusion_id} className={cn("rounded-md border p-2", c.needs_review && "border-amber-500/40 bg-amber-500/5")}>
                    <button onClick={() => toggle(id)} className="flex w-full items-start gap-2 text-left text-sm">
                      {expanded.has(id) ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0" /> : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0" />}
                      <span>
                        {c.text}{" "}
                        <span className="text-xs text-muted-foreground">
                          [{c.origin}
                          {c.needs_review ? " · needs manual review (no valid citation)" : ""}]
                        </span>
                      </span>
                    </button>
                    <ExpandableCitations citations={c.citations} open={expanded.has(id)} />
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">Filter history</span>
        <select value={fAuthor} onChange={(e) => setFAuthor(e.target.value)} className="rounded-md border bg-background px-2 py-1 text-sm">
          <option value="all">All authors</option>
          {authors.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={fSymbol} onChange={(e) => setFSymbol(e.target.value)} className="rounded-md border bg-background px-2 py-1 text-sm">
          <option value="all">All symbols</option>
          {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={fDirection} onChange={(e) => setFDirection(e.target.value)} className="rounded-md border bg-background px-2 py-1 text-sm">
          <option value="all">All directions</option>
          {["bullish", "bearish", "neutral", "conditional", "unknown"].map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <span className="text-xs text-muted-foreground">{filtered.length} / {data.note_count} notes</span>
      </div>

      {/* History timeline */}
      <div className="space-y-2">
        {filtered.map((n) => {
          const id = `note:${n.note_id}`;
          const open = expanded.has(id);
          const heldSyms = n.symbols.filter((s) => holdings[s]);
          return (
            <div key={n.note_id} className="rounded-lg border bg-card p-3">
              <button onClick={() => toggle(id)} className="flex w-full items-start gap-2 text-left">
                {open ? <ChevronDown className="mt-1 h-4 w-4 shrink-0" /> : <ChevronRight className="mt-1 h-4 w-4 shrink-0" />}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium">{n.author}</span>
                    <span className={cn("text-xs", n.timestamp_ok ? "text-muted-foreground" : "italic text-muted-foreground/60")}>
                      {n.timestamp_ok ? fmtTime(n.timestamp_parsed) : "unknown time"}
                    </span>
                    <DirBadge direction={n.direction} />
                    {n.symbols.map((s) => (
                      <span key={s} className={cn("rounded px-1.5 py-0.5 text-xs", holdings[s] ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>
                        {s}
                      </span>
                    ))}
                    {heldSyms.length > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs text-primary">
                        <Link2 className="h-3 w-3" /> held
                      </span>
                    )}
                  </div>
                  <p className="mt-1 truncate text-sm text-muted-foreground">{n.content}</p>
                </div>
              </button>

              {open && (
                <div className="ml-6 mt-2 space-y-3 text-sm">
                  <div className="rounded bg-muted/50 p-2 text-xs">
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {n.source_file} · {n.source_ref} · {n.note_id}
                    </span>
                    <p className="mt-0.5 italic">“{n.excerpt}”</p>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                    <Field label="Horizon" value={n.horizon} />
                    <Field label="Key levels" value={n.key_levels.length ? n.key_levels.join(", ") : "unavailable"} />
                    <Field label="Setup" value={n.setup_condition} />
                    <Field label="Invalidation" value={n.invalidation_condition} />
                    <Field label="1d / 1w / 1m outcome" value={`${n.outcome_1d} / ${n.outcome_1w} / ${n.outcome_1m}`} />
                  </div>

                  {(n.theme_associations?.length ?? 0) > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-xs text-muted-foreground">Themes:</span>
                      {n.theme_associations!.map((a) => (
                        <span key={a.theme_key} className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-1.5 py-0.5 text-xs">
                          <Layers className="h-3 w-3 text-muted-foreground" />
                          {a.label}
                          {a.association_types.includes("direct_symbol") && (
                            <span className="text-primary">· via {a.matched_symbols.join("/")}</span>
                          )}
                          {a.association_types.includes("configured_keyword") && (
                            <span className="text-amber-600" title="configured theme association (not a direct mention)">
                              · configured keyword: {a.matched_keywords.join("/")}
                            </span>
                          )}
                        </span>
                      ))}
                    </div>
                  )}

                  {heldSyms.map((sym) => {
                    const h = holdings[sym];
                    return (
                      <div key={sym} className="rounded-md border border-primary/30 bg-primary/5 p-2 text-xs">
                        <div className="mb-1 flex flex-wrap items-center gap-2">
                          <Link2 className="h-3.5 w-3.5 text-primary" />
                          <span className="font-medium">{sym}: {h.count} held strategy(ies)</span>
                          <span className="text-muted-foreground">earliest expiry {h.earliest_expiry ?? "—"}</span>
                          {h.has_near_dte && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600">≤14 DTE</span>}
                          <Link to="/options-studio" className="ml-auto text-primary hover:underline">
                            open in Options Studio →
                          </Link>
                        </div>
                        <ul className="space-y-0.5">
                          {h.strategies.map((s) => (
                            <li key={s.strategy_id} className="text-muted-foreground">
                              <span className="capitalize text-foreground">{s.type.replace(/_/g, " ")}</span> · DTE {s.dte ?? "—"} ·{" "}
                              {s.legs.map((l) => `${l.quantity > 0 ? "+" : ""}${l.quantity} ${l.right === "call" ? "C" : "P"}${l.strike} ${l.expiry}`).join("  ")}
                            </li>
                          ))}
                        </ul>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          Information link only — no live Delta / IV / price, no suggested action.
                        </p>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="text-xs text-muted-foreground">
        Opinions are stored, structured, and cross-referenced only. Symbols come from cashtags / known indices / your held
        book / the whitelist; missing fields show <em>unknown</em> / <em>unavailable</em> and are never guessed. This page
        has no buy, sell, add, trim, or order controls.
      </p>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span className={cn(value === "unknown" || value === "unavailable" ? "text-muted-foreground/60 italic" : "font-medium")}>{value}</span>
    </div>
  );
}

function ImportStatusCard({ stats }: { stats: ImportStats }) {
  const t = stats.totals;
  return (
    <div className="rounded-lg border bg-card p-4">
      <h2 className="mb-2 text-sm font-semibold text-muted-foreground">Import status (de-identified)</h2>
      <div className="mb-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Files" value={t.files} />
        <Stat label="Messages" value={t.raw_messages} />
        <Stat label="Structured notes" value={t.structured_notes} />
        <Stat label="Parseable times" value={t.parseable_timestamps} />
        <Stat label="Skipped" value={t.skipped} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left uppercase tracking-wide text-muted-foreground">
              <th className="p-2 font-medium">File</th>
              <th className="p-2 font-medium">Kind</th>
              <th className="p-2 text-right font-medium">Raw</th>
              <th className="p-2 text-right font-medium">Notes</th>
              <th className="p-2 text-right font-medium">Parse-time</th>
              <th className="p-2 text-right font-medium">Skipped</th>
              <th className="p-2 font-medium">Channel</th>
              <th className="p-2 text-right font-medium">Authors</th>
            </tr>
          </thead>
          <tbody>
            {stats.files.map((f) => (
              <tr key={f.file} className="border-b last:border-0">
                <td className="p-2 font-medium">{f.file}</td>
                <td className="p-2">{f.kind}</td>
                <td className="p-2 text-right tabular-nums">{f.raw_messages}</td>
                <td className="p-2 text-right tabular-nums">{f.structured_notes}</td>
                <td className="p-2 text-right tabular-nums">{f.parseable_timestamps}</td>
                <td className="p-2 text-right tabular-nums">
                  {f.skipped}
                  {f.skipped > 0 && (
                    <span className="ml-1 text-muted-foreground">({Object.keys(f.skip_reasons).join(", ")})</span>
                  )}
                </td>
                <td className="p-2 font-mono text-[10px] text-muted-foreground">{f.channel_ref ?? "—"}</td>
                <td className="p-2 text-right tabular-nums">{f.distinct_authors}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-xs text-muted-foreground">
        Distinct authors: {stats.authors.distinct} · distinct channels: {stats.channels.distinct} · authors and channels
        are shown as de-identified references only (no names, no account, no message text, no full path).
      </div>
      {stats.errors.length > 0 && (
        <div className="mt-2 space-y-0.5 text-xs text-red-600 dark:text-red-400">
          {stats.errors.map((e, i) => (
            <div key={i}>
              <AlertTriangle className="mr-1 inline h-3 w-3" /> {e.file}: {e.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border bg-background p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function ThemesCard({ themes }: { themes: Record<string, ThemeSummary> }) {
  const entries = Object.entries(themes).sort((a, b) => b[1].note_count - a[1].note_count);
  return (
    <div className="rounded-lg border bg-card p-4">
      <h2 className="mb-1 text-sm font-semibold text-muted-foreground">
        Configured theme associations
      </h2>
      <p className="mb-3 text-xs text-muted-foreground">
        Themes reuse <code>config/market_radar.yaml</code>. Keyword links are a <strong>configured theme association</strong>,
        never presented as a direct mention. Holdings shown are information only.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {entries.map(([key, s]) => (
          <div key={key} className="rounded-md border p-3">
            <div className="flex items-center gap-2">
              <Layers className="h-4 w-4 text-primary" />
              <span className="font-medium">{s.label}</span>
              <span className="text-xs text-muted-foreground">· {s.note_count} note(s)</span>
            </div>
            {s.held_symbols.length > 0 ? (
              <div className="mt-1 text-xs text-muted-foreground">
                Held in theme: <span className="font-medium text-foreground">{s.held_symbols.join(", ")}</span> ·{" "}
                {s.strategy_count} strategy(ies) · earliest expiry {s.earliest_expiry ?? "—"}
                {s.has_near_dte && <span className="ml-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600">≤14 DTE</span>}
              </div>
            ) : (
              <div className="mt-1 text-xs text-muted-foreground/60 italic">No current holdings in this theme.</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
