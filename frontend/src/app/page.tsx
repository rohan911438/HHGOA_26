"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { describeDependency } from "@/lib/health";
import {
  getCase,
  getCaseHistory,
  getDependencyHealth,
  getSimilarCases,
  startInvestigation,
} from "@/lib/api";
import type {
  CaseRecord,
  CaseTrigger,
  DetailedHealthResponse,
  HistoryEvent,
  InvestigationResponse,
  SimilarCaseResult,
} from "@/lib/types";
import { ApiError } from "@/lib/types";
import { TransactionForm } from "@/components/TransactionForm";
import { LoadingState } from "@/components/LoadingState";
import { ErrorBanner } from "@/components/ErrorBanner";
import { SummaryHeader } from "@/components/SummaryHeader";
import { NextBestActionPanel } from "@/components/NextBestActionPanel";
import { EvidencePanel } from "@/components/EvidencePanel";
import { GraphView } from "@/components/GraphView";
import { UncertaintyPanel } from "@/components/UncertaintyPanel";
import { AgentFindingsPanel } from "@/components/AgentFindingsPanel";
import { HistoricalContextPanel } from "@/components/HistoricalContextPanel";
import { RecurringPatternsPanel } from "@/components/RecurringPatternsPanel";
import { CaseTimeline } from "@/components/CaseTimeline";
import { Panel } from "@/components/ui";

type Status = "idle" | "loading" | "success" | "error";

const NAV_ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/investigate", label: "Workflow" },
  { href: "/cases", label: "Cases" },
  { href: "/evidence", label: "Evidence" },
];

export default function Home() {
  const pathname = usePathname();
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<InvestigationResponse | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [dependencyHealth, setDependencyHealth] = useState<DetailedHealthResponse | null>(null);

  const [caseRecord, setCaseRecord] = useState<CaseRecord | null>(null);
  const [caseError, setCaseError] = useState(false);

  const [history, setHistory] = useState<HistoryEvent[]>([]);
  const [historyNote, setHistoryNote] = useState<string | undefined>(undefined);

  const [similarCases, setSimilarCases] = useState<SimilarCaseResult[]>([]);
  const [similarNote, setSimilarNote] = useState<string | undefined>(undefined);

  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiUrl) return;

    void getDependencyHealth()
      .then((response) => setDependencyHealth(response))
      .catch(() => setDependencyHealth(null));
  }, []);

  const loadSecondaryData = useCallback(async (caseId: string) => {
    const [caseSettled, historySettled, similarSettled] = await Promise.allSettled([
      getCase(caseId),
      getCaseHistory(caseId),
      getSimilarCases(caseId),
    ]);

    if (caseSettled.status === "fulfilled") {
      setCaseRecord(caseSettled.value);
    } else {
      setCaseError(true);
    }

    if (historySettled.status === "fulfilled") {
      setHistory(historySettled.value.events);
      setHistoryNote(historySettled.value.note);
    }

    if (similarSettled.status === "fulfilled") {
      setSimilarCases(similarSettled.value.similar_cases);
      setSimilarNote(similarSettled.value.note);
    }
  }, []);

  const handleSubmit = useCallback(
    async (transactionId: string, trigger: CaseTrigger) => {
      setStatus("loading");
      setError(null);
      setResult(null);
      setCaseRecord(null);
      setCaseError(false);
      setHistory([]);
      setHistoryNote(undefined);
      setSimilarCases([]);
      setSimilarNote(undefined);

      try {
        const investigation = await startInvestigation({ transaction_id: transactionId, trigger });
        setResult(investigation);
        setStatus("success");
        if (investigation.case_id) {
          void loadSecondaryData(investigation.case_id);
        }
      } catch (err) {
        setError(err instanceof Error ? err : new Error("Unknown error"));
        setStatus("error");
      }
    },
    [loadSecondaryData]
  );

  const tigerGraphStatus = describeDependency(dependencyHealth, (n) => n.includes("graph") || n.includes("tiger"), {
    ok: "TIGERGRAPH CONNECTED",
    degraded: "TIGERGRAPH DEGRADED",
    unavailable: "GRAPH ENGINE UNAVAILABLE",
  });
  const llmStatus = describeDependency(dependencyHealth, (n) => n.includes("llm"), {
    ok: "AGENT LLM CONFIGURED",
    degraded: "AGENT LLM DEGRADED",
    unavailable: "AGENT LLM NOT CONFIGURED",
  });

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-6 px-4 py-5 sm:px-6 lg:px-8">
        <header className="panel-shell px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-accent/40 bg-accent/10 font-mono text-[10px] font-bold text-accent-strong">
                FG
              </div>
              <div>
                <p className="brand-mark text-[12px] tracking-[0.38em] text-accent-strong">FRAUD//GRAPH</p>
                <p className="mt-1 text-[12px] uppercase tracking-[0.18em] text-muted">Agentic Fraud Investigation Platform</p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-muted-strong">
              <span className="inline-flex items-center gap-2 rounded-full border border-divider bg-surface-raised px-2.5 py-1.5">
                <span className={`status-dot ${tigerGraphStatus.tone}`} aria-hidden="true" />
                {tigerGraphStatus.label}
              </span>
              <span className="inline-flex items-center gap-2 rounded-full border border-divider bg-surface-raised px-2.5 py-1.5">
                <span className={`status-dot ${llmStatus.tone}`} aria-hidden="true" />
                {llmStatus.label}
              </span>
            </div>
          </div>
        </header>

        <div className="grid gap-6 xl:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="panel-shell p-3">
            <nav className="flex flex-col gap-2" aria-label="Investigation navigation">
              {NAV_ITEMS.map(({ href, label }) => {
                const current = pathname === href;
                return (
                  <Link
                    key={href}
                    href={href}
                    className={`rounded border px-3 py-2 text-left text-[11px] uppercase tracking-[0.18em] transition ${
                      current
                        ? "border-accent/40 bg-accent/10 text-accent-strong"
                        : "border-transparent bg-transparent text-muted hover:border-divider hover:bg-surface-raised hover:text-foreground"
                    }`}
                  >
                    {label}
                  </Link>
                );
              })}
            </nav>
          </aside>

          <main className="flex flex-col gap-6">
            <section className="panel-shell overflow-hidden p-0">
              <div className="border-b border-divider/80 px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.2em] text-muted-strong">Start Investigation</p>
              </div>
              <div className="p-4 sm:p-5">
                <TransactionForm disabled={status === "loading"} onSubmit={handleSubmit} />
              </div>
            </section>

            {status === "loading" ? <LoadingState /> : null}
            {status === "error" && error ? <ErrorBanner error={error} onDismiss={() => setStatus("idle")} /> : null}

            {status === "idle" ? (
              <div className="panel-shell p-8 text-center">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-divider bg-surface-raised text-2xl text-accent-strong">
                  ◇
                </div>
                <p className="mt-5 text-[11px] uppercase tracking-[0.28em] text-muted-strong">Fraud Investigation</p>
                <h2 className="mt-2 text-2xl font-semibold text-foreground">Graph-powered evidence workflow</h2>
                <p className="mx-auto mt-2 max-w-2xl text-sm text-muted">
                  Investigate a transaction through connected entities, historical case memory, uncertainty analysis,
                  and approval-gated next-best actions.
                </p>
                <div className="mt-6 flex flex-wrap justify-center gap-2 text-[10px] uppercase tracking-[0.18em] text-muted">
                  {[
                    "Graph Evidence",
                    "Uncertainty",
                    "Case Memory",
                    "Next-Best Action",
                  ].map((label) => (
                    <span key={label} className="rounded-full border border-divider bg-surface-raised px-2.5 py-1.5">
                      {label}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}

            {status === "success" && result ? (
              <div className="flex flex-col gap-6">
                <SummaryHeader result={result} caseRecord={caseRecord} caseStatusError={caseError} />
                <NextBestActionPanel policy={result.policy_decision} nextStep={result.next_step} />

                <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                  <Panel title="Evidence" subtitle={`${result.evidence.length} source(s) investigated`}>
                    <EvidencePanel evidence={result.evidence} detailAvailable={result.evidence.length > 0} />
                  </Panel>
                  <Panel title="Investigation Graph" subtitle="Entity relationships derived from live evidence">
                    <GraphView transactionId={result.transaction_id} evidence={result.evidence} />
                  </Panel>
                </div>

                <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                  <Panel title="Investigation Uncertainty">
                    <UncertaintyPanel uncertainty={result.uncertainty} />
                  </Panel>
                  <Panel title="AI Investigation Findings">
                    <AgentFindingsPanel result={result} />
                  </Panel>
                </div>

                <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                  <Panel title="Historical Context" subtitle="Similar case memory and outcomes">
                    <HistoricalContextPanel cases={similarCases} note={similarNote} />
                  </Panel>
                  <Panel title="Recurring Patterns">
                    <RecurringPatternsPanel patterns={result.context?.recurring_patterns ?? []} />
                  </Panel>
                </div>

                <Panel title="Case Timeline" subtitle="Activity sequence for the current case">
                  <CaseTimeline events={history} note={historyNote} />
                </Panel>
              </div>
            ) : null}
          </main>
        </div>
      </div>
    </div>
  );
}
