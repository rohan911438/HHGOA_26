import type { CaseRecord, InvestigationResponse } from "@/lib/types";
import { Badge, KeyValue, QualityBadge } from "./ui";

export function SummaryHeader({
  result,
  caseRecord,
  caseStatusError,
}: {
  result: InvestigationResponse;
  caseRecord: CaseRecord | null;
  caseStatusError: boolean;
}) {
  const uncertainty = result.uncertainty;
  const policy = result.policy_decision;

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
        <KeyValue label="Case ID" value={<span className="font-mono text-xs">{result.case_id ?? "—"}</span>} />
        <KeyValue label="Transaction ID" value={<span className="font-mono text-xs">{result.transaction_id}</span>} />
        <KeyValue label="Investigation Status" value={<Badge tone="accent">{result.status}</Badge>} />
        <KeyValue
          label="Case Status"
          value={
            caseRecord ? (
              <Badge>{caseRecord.status.replace(/_/g, " ")}</Badge>
            ) : caseStatusError ? (
              <span className="text-xs text-muted italic">unavailable</span>
            ) : (
              <span className="text-xs text-muted italic">loading…</span>
            )
          }
        />
        <KeyValue
          label="Uncertainty Level"
          value={uncertainty ? <QualityBadge value={uncertainty.uncertainty_level} /> : "—"}
        />
        <KeyValue
          label="Overall Uncertainty"
          value={
            uncertainty?.overall_uncertainty !== null && uncertainty?.overall_uncertainty !== undefined
              ? uncertainty.overall_uncertainty.toFixed(3)
              : "n/a"
          }
        />
        <KeyValue label="Recommended Action" value={policy ? policy.action.replace(/_/g, " ") : "—"} />
        <KeyValue
          label="Approval Required"
          value={policy ? (policy.approval_required ? <Badge tone="warn">YES</Badge> : <Badge>No</Badge>) : "—"}
        />
        <KeyValue label="Approval Route" value={policy ? policy.approval_route.replace(/_/g, " ") : "—"} />
        <KeyValue
          label="Iterations / Tool Calls"
          value={`${result.iterations} / ${result.tool_calls}`}
        />
      </dl>
    </section>
  );
}
