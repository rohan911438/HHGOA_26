"use client";

import { AppShell } from "@/components/AppShell";
import { TransactionForm } from "@/components/TransactionForm";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LoadingState } from "@/components/LoadingState";
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
import {
  getCase,
  getCaseHistory,
  getSimilarCases,
  startInvestigation,
} from "@/lib/api";
import type {
  CaseRecord,
  CaseTrigger,
  HistoryEvent,
  InvestigationResponse,
  SimilarCaseResult,
} from "@/lib/types";
import { ApiError } from "@/lib/types";
import { useCallback, useState } from "react";

type Status = "idle" | "loading" | "success" | "error";

export default function InvestigatePage() {
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<InvestigationResponse | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [caseRecord, setCaseRecord] = useState<CaseRecord | null>(null);
  const [caseError, setCaseError] = useState(false);
  const [history, setHistory] = useState<HistoryEvent[]>([]);
  const [historyNote, setHistoryNote] = useState<string | undefined>(undefined);
  const [similarCases, setSimilarCases] = useState<SimilarCaseResult[]>([]);
  const [similarNote, setSimilarNote] = useState<string | undefined>(undefined);

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

  return (
    <AppShell title="Investigate">
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
    </AppShell>
  );
}
