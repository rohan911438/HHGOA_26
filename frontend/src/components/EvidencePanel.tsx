import type { Evidence } from "@/lib/types";
import { Badge, EmptyNote, QualityBadge, titleCase } from "./ui";

function EvidenceItem({ item }: { item: Evidence }) {
  return (
    <li className="rounded border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold text-foreground">{titleCase(item.evidence_type)}</span>
        <div className="flex items-center gap-1.5">
          <Badge>{item.status}</Badge>
          <QualityBadge value={item.quality} />
        </div>
      </div>

      {item.observation ? <p className="mt-2 text-sm text-muted-strong">{item.observation}</p> : null}
      {item.interpretation ? <p className="mt-1 text-xs text-muted">{item.interpretation}</p> : null}
      {item.quality_reason ? (
        <p className="mt-1 text-[11px] text-muted">
          <span className="font-semibold">Note:</span> {item.quality_reason}
        </p>
      ) : null}
      {item.error ? (
        <p className="mt-1 text-[11px] text-warn">
          {item.error.error_type}: {item.error.message}
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border pt-2 text-[11px] text-muted">
        <span className="font-mono">{item.evidence_id}</span>
        <span>
          provenance: <span className="font-mono">{item.provenance.source}</span> /{" "}
          <span className="font-mono">{item.provenance.source_query}</span>
        </span>
        {item.related_entities.length > 0 ? <span>{item.related_entities.length} related entit(y/ies)</span> : null}
      </div>
    </li>
  );
}

export function EvidencePanel({ evidence, detailAvailable, note }: { evidence: Evidence[]; detailAvailable: boolean; note?: string | null }) {
  if (!detailAvailable) {
    return (
      <div>
        <EmptyNote>{note ?? "Full per-item evidence detail is not available for this case."}</EmptyNote>
      </div>
    );
  }
  if (evidence.length === 0) {
    return <EmptyNote>No evidence items were returned for this investigation.</EmptyNote>;
  }
  return <ul className="flex flex-col gap-2">{evidence.map((item) => <EvidenceItem key={item.evidence_id} item={item} />)}</ul>;
}
