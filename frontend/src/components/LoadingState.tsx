"use client";

import { useEffect, useState } from "react";

/**
 * Phase 2L - a purely cosmetic progress indicator.
 *
 * The backend API is synchronous: `POST /investigations` returns only
 * once the entire agent run has finished (see
 * backend/docs/phase-2-api.md §7 - the sync/async boundary decision).
 * There is no server-sent stage/progress event to reflect here. These
 * labels rotate on a fixed local timer and do NOT claim the backend is
 * "currently" doing any one of them - they exist only so a multi-second
 * wait (dominated by live TigerGraph latency, typically 6-8s - see
 * backend/docs/phase-2-benchmark-report.md §3a) doesn't look frozen.
 */
const MESSAGES = [
  "Mapping graph relationships…",
  "Analyzing evidence…",
  "Assessing uncertainty…",
  "Building case context…",
  "Determining next-best-action…",
];

export function LoadingState() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setIndex((i) => (i + 1) % MESSAGES.length), 1600);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-border bg-surface px-8 py-14 text-center">
      <div className="flex items-center gap-3">
        <span className="relative flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
        </span>
        <span className="text-sm font-semibold uppercase tracking-wide text-muted-strong">
          AI investigation in progress
        </span>
      </div>
      <p className="font-mono text-sm text-muted transition-opacity duration-300">{MESSAGES[index]}</p>
      <p className="max-w-md text-xs text-muted">
        This can take several seconds - the agent is querying a live TigerGraph instance, not a cached result.
      </p>
    </div>
  );
}
