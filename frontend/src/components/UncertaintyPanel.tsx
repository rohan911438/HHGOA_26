import type { UncertaintyAssessment } from "@/lib/types";
import { Badge, EmptyNote, Meter, QualityBadge, titleCase } from "./ui";

export function UncertaintyPanel({ uncertainty }: { uncertainty: UncertaintyAssessment | null }) {
  if (!uncertainty) {
    return <EmptyNote>No uncertainty assessment was produced for this investigation.</EmptyNote>;
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <QualityBadge value={uncertainty.uncertainty_level} />
        <span className="font-mono text-sm text-muted-strong">
          overall_uncertainty ={" "}
          {uncertainty.overall_uncertainty !== null ? uncertainty.overall_uncertainty.toFixed(3) : "n/a"}
        </span>
      </div>

      <p className="rounded border border-border-strong bg-surface-raised px-3 py-2 text-xs text-muted">
        <span className="font-semibold text-muted-strong">Investigation uncertainty is not fraud probability.</span>{" "}
        It measures how complete, reliable, and internally consistent the gathered evidence is - never the
        likelihood that this transaction is fraudulent.
      </p>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Meter label="Evidence Coverage" value={uncertainty.evidence_coverage} />
        <Meter label="Signal Quality" value={uncertainty.signal_quality} />
        <Meter label="Signal Conflict" value={uncertainty.signal_conflict} />
        <Meter label="Data Completeness" value={uncertainty.data_completeness} />
      </div>

      <p className="text-xs text-muted">{uncertainty.rationale}</p>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">
          {uncertainty.missing_evidence.length > 0 ? "Additional Evidence Required" : "Evidence Collection Complete"}
        </h3>
        {uncertainty.missing_evidence.length === 0 ? (
          <p className="mt-1 text-xs text-muted">No missing-evidence gaps were identified by the uncertainty engine.</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {uncertainty.missing_evidence.map((m, i) => (
              <li key={i} className="rounded border border-warn/30 bg-warn/5 px-3 py-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-muted-strong">{titleCase(m.evidence_type)}</span>
                  <Badge tone="warn">{m.reason.replace(/_/g, " ")}</Badge>
                </div>
                <p className="mt-1 text-muted">
                  <span className="font-semibold">Reason:</span> {m.impact}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>

      {uncertainty.conflicting_evidence.length > 0 ? (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">Conflicting Evidence</h3>
          <ul className="mt-2 flex flex-col gap-2">
            {uncertainty.conflicting_evidence.map((c) => (
              <li key={c.conflict_id} className="rounded border border-border-strong bg-surface-raised px-3 py-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-muted-strong">{c.conflict_type.replace(/_/g, " ")}</span>
                  <Badge tone={c.severity === "HIGH" ? "warn" : "neutral"}>{c.severity}</Badge>
                </div>
                <p className="mt-1 text-muted">{c.description}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {uncertainty.factors.length > 0 ? (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-strong">Uncertainty Factors</h3>
          <ul className="mt-2 flex flex-col gap-1">
            {uncertainty.factors.map((f, i) => (
              <li key={i} className="flex items-baseline justify-between text-xs text-muted">
                <span>
                  {f.factor} <span className="text-[10px]">({f.source})</span>
                </span>
                <span className="font-mono text-muted-strong">{f.value !== null ? f.value.toFixed(3) : "n/a"}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
