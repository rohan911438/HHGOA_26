/**
 * Shared presentational primitives for the investigation workspace.
 * Built around a restrained dark terminal aesthetic that still reads as a
 * premium analyst console rather than a generic dashboard shell.
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
      className={`panel-shell overflow-hidden ${accent ? "panel-accent" : ""} ${className}`.trim()}
    >
      <header className="border-b border-divider/80 px-4 py-3">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-strong">{title}</h2>
        {subtitle ? <p className="mt-1 text-xs text-muted">{subtitle}</p> : null}
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

export function QualityBadge({ value }: { value: string }) {
  const style = QUALITY_STYLE[value] ?? QUALITY_STYLE.UNKNOWN;
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${style}`}>
      {value}
    </span>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "warn" | "accent" | "success" | "danger";
}) {
  const toneClass =
    tone === "warn"
      ? "border-warn/40 text-warn bg-warn/10"
      : tone === "accent"
        ? "border-accent/40 text-accent-strong bg-accent/10"
        : tone === "success"
          ? "border-ok/40 text-ok bg-ok/10"
          : tone === "danger"
            ? "border-danger/40 text-danger bg-danger/10"
            : "border-border-strong text-muted-strong bg-surface-raised";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em] ${toneClass}`}>
      {children}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  return <Badge tone="accent">{status.replace(/_/g, " ")}</Badge>;
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-[10px] uppercase tracking-[0.16em] text-muted">{label}</dt>
      <dd className="text-sm font-medium text-foreground">{value}</dd>
    </div>
  );
}

export function Meter({ label, value, detail }: { label: string; value: number | null; detail?: string }) {
  const pct = value === null ? null : Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] uppercase tracking-[0.14em] text-muted">{label}</span>
        <span className="font-mono text-[11px] text-muted-strong">{pct === null ? "n/a" : `${pct}%`}</span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-raised">
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent via-accent-strong to-cyan-400"
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
