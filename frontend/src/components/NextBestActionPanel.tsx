import type { PolicyDecision } from "@/lib/types";
import { Badge, KeyValue } from "./ui";

export function NextBestActionPanel({ policy, nextStep }: { policy: PolicyDecision | null; nextStep: string }) {
  if (!policy) {
    return (
      <section className="rounded-lg border border-border bg-surface p-4">
        <p className="text-sm text-muted italic">No policy decision was produced for this investigation.</p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border-2 border-accent/50 bg-surface p-5">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-accent-strong">Next Best Action</p>
      <p className="mt-1 text-2xl font-bold tracking-tight text-foreground">{policy.action.replace(/_/g, " ")}</p>
      <p className="mt-2 text-sm text-muted-strong">{policy.rationale}</p>

      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <KeyValue
          label="Approval Required"
          value={policy.approval_required ? <Badge tone="warn">YES</Badge> : <Badge>No</Badge>}
        />
        <KeyValue label="Approval Route" value={policy.approval_route.replace(/_/g, " ")} />
        <KeyValue
          label="Executable"
          value={policy.executable ? <Badge tone="warn">YES</Badge> : <Badge>No</Badge>}
        />
        <KeyValue label="Requires More Evidence" value={policy.requires_more_evidence ? "Yes" : "No"} />
      </div>

      <div className="mt-4 rounded border border-border-strong bg-surface-raised px-3 py-2.5">
        <p className="text-xs font-semibold text-muted-strong">
          {policy.executable ? "Executable action" : "Recommendation only — no real-world action executed."}
        </p>
        <p className="mt-1 text-xs text-muted">{nextStep}</p>
      </div>

      <p className="mt-3 text-[11px] text-muted">{policy.policy_basis}</p>
    </section>
  );
}
