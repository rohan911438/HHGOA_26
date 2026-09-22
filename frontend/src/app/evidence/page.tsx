"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Panel } from "@/components/ui";
import { EvidencePanel } from "@/components/EvidencePanel";
import { ErrorBanner } from "@/components/ErrorBanner";
import { getCaseEvidence, getCases } from "@/lib/api";
import type { CaseRecord, EvidenceResponse } from "@/lib/types";
import { ApiError } from "@/lib/types";

export default function EvidencePage() {
  const [cases, setCases] = useState<CaseRecord[] | null>(null);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCases()
      .then((response) => {
        if (cancelled) return;
        setCases(response);
        if (response.length > 0) setSelectedCaseId(response[response.length - 1].case_id);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error("Unknown error"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedCaseId) return;
    let cancelled = false;
    getCaseEvidence(selectedCaseId)
      .then((response) => {
        if (!cancelled) setEvidence(response);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error("Unknown error"));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedCaseId]);

  // Stale while a different case's evidence is still in flight - derived,
  // never a separate piece of state set synchronously inside the effect.
  const loading = selectedCaseId !== null && evidence?.case_id !== selectedCaseId;
  const lowQualityCount = evidence?.evidence.filter((e) => e.quality === "LOW").length ?? 0;
  const distinctTypes = evidence ? new Set(evidence.evidence.map((e) => e.evidence_type)).size : 0;

  return (
    <AppShell title="Evidence">
      {error ? <ErrorBanner error={error} onDismiss={() => setError(null)} /> : null}

      {cases !== null && cases.length === 0 ? (
        <div className="panel-shell p-8 text-center">
          <p className="text-sm text-muted">
            No cases exist yet. Investigate a transaction on the{" "}
            <a href="/investigate" className="text-accent-strong underline">
              Investigate
            </a>{" "}
            page, then its evidence will appear here.
          </p>
        </div>
      ) : (
        <>
          <div className="panel-shell p-4">
            <label className="flex flex-col gap-2 sm:max-w-sm">
              <span className="text-[11px] uppercase tracking-[0.18em] text-muted">Case</span>
              <select
                value={selectedCaseId ?? ""}
                onChange={(e) => setSelectedCaseId(e.target.value)}
                className="rounded-xl border border-divider bg-surface-raised px-3 py-3 text-sm text-foreground outline-none transition focus:border-accent/50"
              >
                {(cases ?? []).map((c) => (
                  <option key={c.case_id} value={c.case_id}>
                    {c.case_id} ({c.transaction_id})
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid gap-6 xl:grid-cols-3">
            <div className="panel-shell p-4">
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted">Evidence items</p>
              <p className="mt-3 text-3xl font-semibold text-foreground">{evidence ? evidence.evidence.length : "—"}</p>
            </div>
            <div className="panel-shell p-4">
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted">Low-quality signals</p>
              <p className="mt-3 text-3xl font-semibold text-foreground">{evidence ? lowQualityCount : "—"}</p>
            </div>
            <div className="panel-shell p-4">
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted">Distinct evidence types</p>
              <p className="mt-3 text-3xl font-semibold text-foreground">{evidence ? distinctTypes : "—"}</p>
            </div>
          </div>

          <Panel
            title="Evidence Explorer"
            subtitle={
              selectedCaseId ? `GET /cases/${selectedCaseId}/evidence` : "Select a case to load its evidence"
            }
          >
            {loading ? (
              <p className="text-sm text-muted italic">Loading evidence…</p>
            ) : evidence ? (
              <EvidencePanel evidence={evidence.evidence} detailAvailable={evidence.detail_available} note={evidence.note} />
            ) : (
              <p className="text-sm text-muted italic">No evidence loaded yet.</p>
            )}
          </Panel>
        </>
      )}
    </AppShell>
  );
}
