import type { SimilarCaseResult } from "@/lib/types";
import { Badge, EmptyNote } from "./ui";

function SimilarCase({ result }: { result: SimilarCaseResult }) {
  return (
    <li className="rounded border border-border-strong bg-surface-raised p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-strong">{result.case_id}</span>
        <span className="font-mono text-xs text-accent-strong">
          similarity {result.similarity_score.toFixed(3)}
        </span>
      </div>

      {result.matching_features.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {result.matching_features.map((f) => (
            <Badge key={f}>{f}</Badge>
          ))}
        </div>
      ) : null}

      {result.relevant_findings.length > 0 ? (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-muted">
          {result.relevant_findings.map((f) => (
            <li key={f.finding_id}>{f.description}</li>
          ))}
        </ul>
      ) : null}

      {result.previous_decisions.length > 0 || result.previous_actions.length > 0 ? (
        <div className="mt-2 text-xs text-muted">
          {result.previous_actions.map((a) => (
            <div key={a.action_id}>
              Previous action: <span className="text-muted-strong">{a.action.replace(/_/g, " ")}</span> (
              {a.status.toLowerCase()})
            </div>
          ))}
        </div>
      ) : null}

      {result.outcome ? (
        <div className="mt-2 flex items-center gap-2 text-xs">
          <span className="text-muted">Outcome:</span>
          <span className="font-semibold text-muted-strong">{result.outcome.outcome_type.replace(/_/g, " ")}</span>
          {result.outcome.is_synthetic ? <Badge tone="warn">SYNTHETIC DEVELOPMENT CASE</Badge> : null}
        </div>
      ) : null}
    </li>
  );
}

export function HistoricalContextPanel({ cases, note }: { cases: SimilarCaseResult[]; note?: string }) {
  return (
    <div>
      {cases.length === 0 ? (
        <EmptyNote>No similar historical cases were found in this development environment yet.</EmptyNote>
      ) : (
        <ul className="flex flex-col gap-2">
          {cases.map((c) => (
            <SimilarCase key={c.case_id} result={c} />
          ))}
        </ul>
      )}
      {note ? <p className="mt-3 text-[11px] text-muted">{note}</p> : null}
    </div>
  );
}
