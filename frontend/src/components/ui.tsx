/**
 * Phase 2L - small shared presentational primitives used across every
 * panel, so the console reads as one consistent system rather than a
 * pile of one-off cards.
 */

import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  children,
  accent = false,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  accent?: boolean;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border bg-surface ${accent ? "border-accent/50" : "border-border"} ${className}`}
    >
      <header className="border-b border-border px-4 py-2.5">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-muted-strong">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs text-muted">{subtitle}</p> : null}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

const QUALITY_STYLE: Record<string, string> = {
  HIGH: "border-accent/40 text-accent-strong bg-accent/10",
  MEDIUM: "border-border-strong text-muted-strong bg-surface-raised",
  LOW: "border-warn/40 text-warn bg-warn/10",
  UNKNOWN: "border-border text-muted bg-surface-raised",
};

/** Quality/uncertainty-level pill. Deliberately never red/green - this
 * is a data-quality signal, not a fraud-certainty indicator (see
 * docs/phase-2-frontend.md "Design"). */
export function QualityBadge({ value }: { value: string }) {
  const style = QUALITY_STYLE[value] ?? QUALITY_STYLE.UNKNOWN;
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold ${style}`}>
      {value}
    </span>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warn" | "accent" }) {
  const toneClass =
    tone === "warn"
      ? "border-warn/40 text-warn bg-warn/10"
      : tone === "accent"
        ? "border-accent/40 text-accent-strong bg-accent/10"
        : "border-border-strong text-muted-strong bg-surface-raised";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneClass}`}>
      {children}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  return <Badge tone="accent">{status.replace(/_/g, " ")}</Badge>;
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[11px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className="text-sm font-medium text-foreground">{value}</dd>
    </div>
  );
}

export function Meter({ label, value, detail }: { label: string; value: number | null; detail?: string }) {
  const pct = value === null ? null : Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted">{label}</span>
        <span className="font-mono text-xs text-muted-strong">{pct === null ? "n/a" : `${pct}%`}</span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-surface-raised">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: pct === null ? "0%" : `${pct}%` }}
        />
      </div>
      {detail ? <p className="mt-1 text-[11px] text-muted">{detail}</p> : null}
    </div>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted italic">{children}</p>;
}

export function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "medium",
    });
  } catch {
    return iso;
  }
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
