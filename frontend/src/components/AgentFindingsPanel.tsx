import type { InvestigationResponse } from "@/lib/types";
import { Badge, EmptyNote, formatTimestamp } from "./ui";

/**
 * Phase 2L - only the final, grounded explanation and structured
 * findings are shown here. `AgentInvestigationResult.agent_messages`
 * (the orchestrator's internal event/audit trail - see
 * backend/docs/phase-2-api.md §5.3) is deliberately not exposed by the
 * API at all, so there is nothing resembling a hidden chain-of-thought
 * for this component to accidentally render.
 */
export function AgentFindingsPanel({ result }: { result: InvestigationResponse }) {
  const uncertain = result.uncertainty?.missing_evidence ?? [];
  const requiresMore = result.policy_decision?.requires_more_evidence ?? false;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">Grounded Explanation</h3>
        {result.explanation ? (
          <p className="mt-1 text-sm text-muted-strong">{result.explanation}</p>
        ) : (
          <EmptyNote>No explanation was produced.</EmptyNote>
        )}
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">What Was Found</h3>
        {result.findings.length === 0 ? (
          <EmptyNote>No findings were recorded.</EmptyNote>
        ) : (
          <ul className="mt-1 list-disc space-y-1 pl-4 text-sm text-muted-strong">
            {result.findings.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">What Remains Uncertain</h3>
        {uncertain.length === 0 ? (
          <EmptyNote>No unresolved evidence gaps were identified.</EmptyNote>
        ) : (
          <ul className="mt-1 list-disc space-y-1 pl-4 text-sm text-muted-strong">
            {uncertain.map((m, i) => (
              <li key={i}>{m.impact}</li>
            ))}
          </ul>
        )}
      </div>

      {result.policy_decision ? (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">
            Why This Recommendation
          </h3>
          <p className="mt-1 text-sm text-muted-strong">{result.policy_decision.rationale}</p>
        </div>
      ) : null}

      <div className="flex items-center gap-2 rounded border border-border-strong bg-surface-raised px-3 py-2">
        <span className="text-xs font-semibold text-muted-strong">More evidence required?</span>
        <Badge tone={requiresMore ? "warn" : "neutral"}>{requiresMore ? "Yes" : "No"}</Badge>
      </div>

      {result.requested_evidence.length > 0 ? (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">
            Evidence Requested By The Agent
          </h3>
          <ul className="mt-2 flex flex-col gap-2">
            {result.requested_evidence.map((r) => (
              <li key={r.request_id} className="rounded border border-border-strong bg-surface-raised px-3 py-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-muted-strong">{r.requested_evidence_type.replace(/_/g, " ")}</span>
                  <span className="text-muted">{formatTimestamp(r.requested_at)}</span>
                </div>
                <p className="mt-1 text-muted">{r.reason}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
