"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Panel } from "@/components/ui";
import { getCases } from "@/lib/api";
import type { CaseRecord, CaseStatus } from "@/lib/types";
import { ApiError } from "@/lib/types";
import { ErrorBanner } from "@/components/ErrorBanner";

const OPEN_STATUSES: CaseStatus[] = ["OPEN", "INVESTIGATING", "PENDING_EVIDENCE"];

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

export default function CasesPage() {
  const [cases, setCases] = useState<CaseRecord[] | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCases()
      .then((response) => {
        if (!cancelled) setCases(response);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error("Unknown error"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const summary = cases
    ? [
        { label: "Open", value: cases.filter((c) => OPEN_STATUSES.includes(c.status)).length },
        { label: "Action Recommended", value: cases.filter((c) => c.status === "ACTION_RECOMMENDED").length },
        { label: "Pending Review", value: cases.filter((c) => c.status === "PENDING_REVIEW").length },
        { label: "Closed", value: cases.filter((c) => c.status === "CLOSED").length },
      ]
    : [];

  return (
    <AppShell title="Cases">
      {error ? <ErrorBanner error={error} onDismiss={() => setError(null)} /> : null}

      <div className="grid gap-6 xl:grid-cols-4">
        {(cases ? summary : [0, 1, 2, 3]).map((item, i) =>
          cases ? (
            <div key={(item as { label: string }).label} className="panel-shell p-4">
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted">
                {(item as { label: string }).label}
              </p>
              <p className="mt-3 text-3xl font-semibold tracking-tight text-foreground">
                {(item as { value: number }).value}
              </p>
            </div>
          ) : (
            <div key={i} className="panel-shell p-4">
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted">Loading…</p>
              <p className="mt-3 text-3xl font-semibold tracking-tight text-muted">—</p>
            </div>
          )
        )}
      </div>

      <Panel
        title="Case Queue"
        subtitle={
          cases
            ? `${cases.length} case(s) in this API process's case store — GET /cases`
            : "Loading from GET /cases…"
        }
      >
        {cases === null ? (
          <p className="text-sm text-muted italic">Loading cases…</p>
        ) : cases.length === 0 ? (
          <p className="text-sm text-muted italic">
            No cases yet. Investigate a transaction on the{" "}
            <a href="/investigate" className="text-accent-strong underline">
              Investigate
            </a>{" "}
            page to create one.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-y-2 text-left">
              <thead>
                <tr className="text-[10px] uppercase tracking-[0.18em] text-muted">
                  <th className="px-2 py-1">Case</th>
                  <th className="px-2 py-1">Status</th>
                  <th className="px-2 py-1">Trigger</th>
                  <th className="px-2 py-1">Transaction</th>
                  <th className="px-2 py-1">Recommended Action</th>
                  <th className="px-2 py-1">Created</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((item) => (
                  <tr key={item.case_id} className="rounded-lg border border-divider bg-surface-raised text-sm text-foreground">
                    <td className="rounded-l-lg px-2 py-3 font-mono text-xs text-muted-strong">{item.case_id}</td>
                    <td className="px-2 py-3">
                      <span className="rounded border border-divider bg-background px-1.5 py-0.5 text-[10px] uppercase tracking-[0.14em] text-muted-strong">
                        {item.status.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="px-2 py-3 text-muted">{item.trigger.replace(/_/g, " ")}</td>
                    <td className="px-2 py-3 font-mono text-xs text-muted-strong">{item.transaction_id}</td>
                    <td className="px-2 py-3 text-muted">
                      {item.policy_decision ? item.policy_decision.action.replace(/_/g, " ") : "—"}
                    </td>
                    <td className="rounded-r-lg px-2 py-3 text-muted">{formatTimestamp(item.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </AppShell>
  );
}
