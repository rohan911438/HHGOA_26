"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getDependencyHealth } from "@/lib/api";
import type { DetailedHealthResponse } from "@/lib/types";
import { describeDependency } from "@/lib/health";

const NAV_ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/investigate", label: "Investigate" },
  { href: "/cases", label: "Cases" },
  { href: "/evidence", label: "Evidence" },
];

const TONE_DOT: Record<string, string> = {
  ok: "status-dot ok",
  warn: "status-dot warn",
  danger: "status-dot danger",
};

export function AppShell({ children, title }: { children: ReactNode; title?: string }) {
  const pathname = usePathname();
  // undefined = not yet checked, null = checked and unreachable. Never
  // defaults to "ok" while the real check is still in flight.
  const [health, setHealth] = useState<DetailedHealthResponse | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    getDependencyHealth()
      .then((response) => {
        if (!cancelled) setHealth(response);
      })
      .catch(() => {
        if (!cancelled) setHealth(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tigergraph = describeDependency(health, (n) => n.includes("graph") || n.includes("tiger"), {
    ok: "TIGERGRAPH CONNECTED",
    degraded: "TIGERGRAPH DEGRADED",
    unavailable: "TIGERGRAPH UNAVAILABLE",
  });
  const llm = describeDependency(health, (n) => n.includes("llm"), {
    ok: "AGENT LLM CONFIGURED",
    degraded: "AGENT LLM DEGRADED",
    unavailable: "AGENT LLM NOT CONFIGURED",
  });

  const checking = health === undefined;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-6 px-4 py-5 sm:px-6 lg:px-8">
        <header className="panel-shell px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-accent/40 bg-accent/10 font-mono text-[10px] font-bold text-accent-strong">
                FG
              </div>
              <div>
                <p className="brand-mark text-[12px] tracking-[0.38em] text-accent-strong">FRAUD//GRAPH</p>
                <p className="mt-1 text-[12px] uppercase tracking-[0.18em] text-muted">Agentic Fraud Investigation Platform</p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-muted-strong">
              <span className="inline-flex items-center gap-2 rounded-full border border-divider bg-surface-raised px-2.5 py-1.5">
                <span className={TONE_DOT[checking ? "warn" : tigergraph.tone]} aria-hidden="true" />
                {checking ? "CHECKING TIGERGRAPH…" : tigergraph.label}
              </span>
              <span className="inline-flex items-center gap-2 rounded-full border border-divider bg-surface-raised px-2.5 py-1.5">
                <span className={TONE_DOT[checking ? "warn" : llm.tone]} aria-hidden="true" />
                {checking ? "CHECKING AGENT LLM…" : llm.label}
              </span>
            </div>
          </div>
        </header>

        <div className="grid gap-6 xl:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="panel-shell p-3">
            <nav className="flex flex-col gap-2" aria-label="Investigation navigation">
              {NAV_ITEMS.map(({ href, label }) => {
                const active = pathname === href;
                return (
                  <Link
                    key={href}
                    href={href}
                    className={`rounded border px-3 py-2 text-left text-[11px] uppercase tracking-[0.18em] transition ${
                      active
                        ? "border-accent/40 bg-accent/10 text-accent-strong"
                        : "border-transparent bg-transparent text-muted hover:border-divider hover:bg-surface-raised hover:text-foreground"
                    }`}
                  >
                    {label}
                  </Link>
                );
              })}
            </nav>
          </aside>

          <main className="flex flex-col gap-6">
            {title ? (
              <section className="panel-shell px-4 py-3 sm:px-5">
                <p className="text-[11px] uppercase tracking-[0.22em] text-muted-strong">{title}</p>
              </section>
            ) : null}
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
