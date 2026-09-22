import type { Evidence, EvidenceType, SignalQuality } from "@/lib/types";
import { EmptyNote } from "./ui";

const ENTITY_TYPES: Partial<Record<EvidenceType, string>> = {
  shared_card: "Card",
  shared_device: "Device",
  shared_address: "Address",
  shared_email_domain: "Email Domain",
};

const QUALITY_STROKE: Record<SignalQuality, string> = {
  HIGH: "#60a5fa",
  MEDIUM: "#8b96a5",
  LOW: "#d97706",
  UNKNOWN: "#4b5563",
};

interface EntityNode {
  key: string;
  label: string;
  entityId: string | null;
  quality: SignalQuality;
  status: string;
  relatedCount: number;
  relatedSample: string[];
  observation: string | null;
}

function buildNodes(evidence: Evidence[]): EntityNode[] {
  const nodes: EntityNode[] = [];
  for (const item of evidence) {
    const label = ENTITY_TYPES[item.evidence_type];
    if (!label) continue;
    const relatedCount =
      typeof item.metrics.related_transaction_count === "number"
        ? item.metrics.related_transaction_count
        : item.related_entities.length;
    nodes.push({
      key: item.evidence_id,
      label,
      entityId: item.provenance.entity_id,
      quality: item.quality,
      status: item.status,
      relatedCount,
      relatedSample: item.related_entities.slice(0, 6),
      observation: item.observation,
    });
  }
  return nodes;
}

const RADIUS = 150;
const CENTER = 210;
const VIEW = 420;

export function GraphView({ transactionId, evidence }: { transactionId: string; evidence: Evidence[] }) {
  const nodes = buildNodes(evidence);

  if (nodes.length === 0) {
    return <EmptyNote>No graph-linkable evidence (card/device/address/email domain) was returned.</EmptyNote>;
  }

  const angleStep = (2 * Math.PI) / nodes.length;

  return (
    <div>
      <svg viewBox={`0 0 ${VIEW} ${VIEW}`} className="w-full" role="img" aria-label="Evidence relationship graph">
        {nodes.map((node, i) => {
          const angle = i * angleStep - Math.PI / 2;
          const x = CENTER + RADIUS * Math.cos(angle);
          const y = CENTER + RADIUS * Math.sin(angle);
          const stroke = QUALITY_STROKE[node.quality];
          const linked = node.status === "SUCCESS" && node.relatedCount > 0;

          return (
            <g key={node.key}>
              <line
                x1={CENTER}
                y1={CENTER}
                x2={x}
                y2={y}
                stroke={stroke}
                strokeWidth={linked ? 2 : 1}
                strokeDasharray={linked ? undefined : "4 3"}
                opacity={0.85}
              />
              {linked && node.relatedCount > 0
                ? (() => {
                    const satelliteAngleSpread = 0.5;
                    const shown = Math.min(node.relatedSample.length, 4);
                    return Array.from({ length: shown }).map((_, si) => {
                      const sAngle = angle + (si - (shown - 1) / 2) * (satelliteAngleSpread / Math.max(shown, 1));
                      const sx = x + 46 * Math.cos(sAngle);
                      const sy = y + 46 * Math.sin(sAngle);
                      return (
                        <g key={`${node.key}-sat-${si}`}>
                          <line x1={x} y1={y} x2={sx} y2={sy} stroke="#333b48" strokeWidth={1} />
                          <circle cx={sx} cy={sy} r={5} fill="#171b22" stroke="#333b48" strokeWidth={1} />
                        </g>
                      );
                    });
                  })()
                : null}
              <circle cx={x} cy={y} r={30} fill="#171b22" stroke={stroke} strokeWidth={2} />
              <text x={x} y={y - 4} textAnchor="middle" fontSize={11} fontWeight={600} fill="#e6e9ee">
                {node.label}
              </text>
              <text x={x} y={y + 11} textAnchor="middle" fontSize={9} fill="#8b96a5">
                {node.status === "EMPTY" ? "none linked" : `${node.relatedCount} linked`}
              </text>
            </g>
          );
        })}

        <circle cx={CENTER} cy={CENTER} r={38} fill="#12151b" stroke="#3b82f6" strokeWidth={2.5} />
        <text x={CENTER} y={CENTER - 4} textAnchor="middle" fontSize={10} fontWeight={700} fill="#e6e9ee">
          TXN
        </text>
        <text x={CENTER} y={CENTER + 11} textAnchor="middle" fontSize={9} fill="#aab4c2" className="font-mono">
          {transactionId.length > 10 ? `${transactionId.slice(0, 10)}…` : transactionId}
        </text>
      </svg>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-accent-strong" /> current transaction
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full border border-border-strong bg-surface-raised" /> related
          entity (card / device / address / email domain)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-1.5 w-1.5 rounded-full border border-border-strong bg-surface" /> sample
          related transaction
        </span>
      </div>

      <ul className="mt-3 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {nodes.map((node) => (
          <li key={node.key} className="text-[11px] text-muted">
            <span className="font-semibold text-muted-strong">{node.label}</span>
            {node.entityId ? <span className="font-mono"> ({node.entityId})</span> : null}: {node.observation ?? "—"}
          </li>
        ))}
      </ul>
    </div>
  );
}
