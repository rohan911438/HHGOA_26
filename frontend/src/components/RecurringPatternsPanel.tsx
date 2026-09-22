import type { ContextItem } from "@/lib/types";
import { Badge, EmptyNote } from "./ui";

/**
 * Phase 2L - the backend's `RecurringPattern` domain model
 * (pattern/occurrences/related_case_ids/historical_actions/historical_outcomes,
 * see backend/app/case/models.py) is not exposed by the API as separate
 * structured fields - only as `InvestigationContext.recurring_patterns`
 * (`ContextItem[]`, `context_type=DERIVED_PATTERN`), whose `content` is
 * already a full grounded sentence naming the evidence types, related
 * case ids, and historical actions/outcomes, and whose `relevance`
 * field carries the occurrence count. This panel renders exactly that -
 * see docs/phase-2-frontend.md "Known limitations" for why occurrences/
 * actions/outcomes aren't broken out into separate columns here.
 */
export function RecurringPatternsPanel({ patterns }: { patterns: ContextItem[] }) {
  if (patterns.length === 0) {
    return <EmptyNote>No recurring evidence patterns were detected across stored cases.</EmptyNote>;
  }

  return (
    <ul className="flex flex-col gap-2">
      {patterns.map((p) => (
        <li key={p.context_id} className="rounded border border-border-strong bg-surface-raised p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-muted-strong">Recurring evidence pattern</span>
            {p.relevance !== null ? <Badge>{Math.round(p.relevance)} occurrence(s)</Badge> : null}
          </div>
          <p className="mt-1 text-sm text-muted-strong">{p.content}</p>
          {p.case_ids.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {p.case_ids.map((id) => (
                <span key={id} className="font-mono text-[11px] text-muted">
                  {id}
                </span>
              ))}
            </div>
          ) : null}
          <p className="mt-1 text-[11px] text-muted">{p.provenance}</p>
        </li>
      ))}
    </ul>
  );
}
