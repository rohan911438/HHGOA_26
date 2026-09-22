"use client";

import { useEffect, useState } from "react";

const MESSAGES = [
  "Connecting evidence sources…",
  "Analyzing transaction network…",
  "Building investigation context…",
  "Evaluating next-best action…",
  "Preparing analyst recommendation…",
];

export function LoadingState() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setIndex((i) => (i + 1) % MESSAGES.length), 1600);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="panel-shell flex flex-col items-center justify-center gap-4 px-8 py-14 text-center">
      <div className="flex items-center gap-3">
        <span className="relative flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
        </span>
        <span className="text-[11px] uppercase tracking-[0.2em] text-muted-strong">AI investigation in progress</span>
      </div>
      <p className="mt-1 font-mono text-sm text-muted transition-opacity duration-300">{MESSAGES[index]}</p>
      <p className="max-w-xl text-xs text-muted">
        This can take several seconds while the agent queries live graph evidence and evaluates uncertainty.
      </p>
    </div>
  );
}
