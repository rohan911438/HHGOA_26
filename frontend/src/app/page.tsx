"use client";

import { useCallback, useState } from "react";
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

export default function Home() {
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
    // Three independent calls, each with its own failure handling - one
    // panel failing (e.g. a transient 5xx on /similar) never blanks the
    // rest of an otherwise-successful investigation result.
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
    <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-1 border-b border-border pb-4">
        <h1 className="text-xl font-bold tracking-tight text-foreground">HHGoa Fraud Investigation Agent</h1>
        <p className="text-sm text-muted">
          Agent-grounded graph investigation console. Every field below is the backend&apos;s actual response - no
          value is invented in this UI.
        </p>
      </header>

      <section className="rounded-lg border border-border bg-surface p-4">
        <TransactionForm disabled={status === "loading"} onSubmit={handleSubmit} />
      </section>

      {status === "loading" ? <LoadingState /> : null}

      {status === "error" && error ? <ErrorBanner error={error} onDismiss={() => setStatus("idle")} /> : null}

      {status === "success" && result ? (
        <div className="flex flex-col gap-6">
          <SummaryHeader result={result} caseRecord={caseRecord} caseStatusError={caseError} />

          <NextBestActionPanel policy={result.policy_decision} nextStep={result.next_step} />

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Panel title="Evidence" subtitle={`${result.evidence.length} item(s) gathered by the agent`}>
              <EvidencePanel evidence={result.evidence} detailAvailable={result.evidence.length > 0} />
            </Panel>
            <Panel title="Graph Relationships" subtitle="Derived from the same evidence shown to the left">
              <GraphView transactionId={result.transaction_id} evidence={result.evidence} />
            </Panel>
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Panel title="Investigation Uncertainty">
              <UncertaintyPanel uncertainty={result.uncertainty} />
            </Panel>
            <Panel title="Agent Findings">
              <AgentFindingsPanel result={result} />
            </Panel>
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Panel
              title="Historical Context"
              subtitle="Similar cases via CaseMemory.retrieve_similar() - GET /cases/{id}/similar"
            >
              <HistoricalContextPanel cases={similarCases} note={similarNote} />
            </Panel>
            <Panel title="Recurring Evidence Patterns">
              <RecurringPatternsPanel patterns={result.context?.recurring_patterns ?? []} />
            </Panel>
          </div>

          <Panel title="Case Timeline" subtitle="GET /cases/{id}/history">
            <CaseTimeline events={history} note={historyNote} />
          </Panel>
        </div>
      ) : null}

      {status === "idle" ? (
        <p className="text-sm text-muted">
          Enter a transaction id and click <span className="font-semibold text-muted-strong">Investigate</span> to
          start a live agent investigation.
        </p>
      ) : null}
    </div>
  );
}
