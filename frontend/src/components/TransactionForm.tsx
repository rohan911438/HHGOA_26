"use client";

import { useState } from "react";
import type { CaseTrigger } from "@/lib/types";

const TRIGGERS: CaseTrigger[] = ["UNKNOWN", "FRAUD_SIGNAL", "CUSTOMER_REPORT", "ANALYST_REQUEST", "AGENT_REQUEST"];

export const DEMO_TRANSACTION_ID = "2987937";

export function TransactionForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (transactionId: string, trigger: CaseTrigger) => void;
}) {
  const [transactionId, setTransactionId] = useState(DEMO_TRANSACTION_ID);
  const [trigger, setTrigger] = useState<CaseTrigger>("FRAUD_SIGNAL");
  const invalid = !transactionId.trim();

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = transactionId.trim();
        if (!trimmed || disabled) return;
        onSubmit(trimmed, trigger);
      }}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] uppercase tracking-[0.22em] text-muted-strong">Start Investigation</p>
          <p className="mt-1 text-sm text-muted">Enter a transaction or entity reference to begin a graph-backed inquiry.</p>
        </div>
        <div className="inline-flex items-center gap-2 rounded-full border border-divider bg-surface-raised px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-muted">
          <span className="h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
          ENTER → INVESTIGATE
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_220px_auto] xl:items-end">
        <label className="flex flex-col gap-2">
          <span className="text-[11px] uppercase tracking-[0.18em] text-muted">Transaction ID</span>
          <div
            className={`flex items-center gap-2 rounded-xl border bg-surface-raised px-3 py-3 transition ${
              invalid ? "border-danger/60" : "border-divider focus-within:border-accent/50"
            }`}
          >
            <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">TX</span>
            <input
              aria-label="Transaction ID"
              value={transactionId}
              onChange={(e) => setTransactionId(e.target.value)}
              disabled={disabled}
              placeholder="e.g. 2987937"
              className="w-full border-0 bg-transparent px-0 py-0 font-mono text-sm text-foreground outline-none placeholder:text-muted disabled:cursor-not-allowed"
            />
            {transactionId ? (
              <button
                type="button"
                onClick={() => setTransactionId("")}
                className="text-[10px] uppercase tracking-[0.18em] text-muted transition hover:text-foreground"
              >
                Clear
              </button>
            ) : null}
          </div>
        </label>

        <label className="flex flex-col gap-2">
          <span className="text-[11px] uppercase tracking-[0.18em] text-muted">Trigger</span>
          <select
            value={trigger}
            onChange={(e) => setTrigger(e.target.value as CaseTrigger)}
            disabled={disabled}
            className="rounded-xl border border-divider bg-surface-raised px-3 py-3 text-sm text-foreground outline-none transition focus:border-accent/50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {TRIGGERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        <button
          type="submit"
          disabled={disabled || !transactionId.trim()}
          className="rounded-xl bg-gradient-to-r from-accent to-accent-strong px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.2em] text-white shadow-[0_0_0_1px_rgba(96,165,250,0.25)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {disabled ? "INVESTIGATING..." : "INVESTIGATE"}
        </button>
      </div>

      <div className="flex flex-col gap-1 border-t border-divider/80 pt-3 text-xs text-muted sm:flex-row sm:items-center sm:justify-between">
        <span>
          Default demo transaction: <span className="font-mono text-muted-strong">{DEMO_TRANSACTION_ID}</span>
        </span>
        <span>{invalid ? "Validation: a transaction ID is required." : "Ready to investigate."}</span>
      </div>
    </form>
  );
}
