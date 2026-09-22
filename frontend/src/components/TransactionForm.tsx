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

  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = transactionId.trim();
        if (!trimmed || disabled) return;
        onSubmit(trimmed, trigger);
      }}
    >
      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">Transaction ID</span>
        <input
          value={transactionId}
          onChange={(e) => setTransactionId(e.target.value)}
          disabled={disabled}
          placeholder="e.g. 2987937"
          className="w-56 rounded border border-border-strong bg-surface-raised px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-accent disabled:opacity-50"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">Trigger</span>
        <select
          value={trigger}
          onChange={(e) => setTrigger(e.target.value as CaseTrigger)}
          disabled={disabled}
          className="rounded border border-border-strong bg-surface-raised px-3 py-2 text-sm text-foreground outline-none focus:border-accent disabled:opacity-50"
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
        className="rounded bg-accent px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
      >
        {disabled ? "Investigating…" : "Investigate"}
      </button>
      <span className="text-xs text-muted">
        Default demo transaction: <span className="font-mono">{DEMO_TRANSACTION_ID}</span>. Change it to investigate
        any other transaction id.
      </span>
    </form>
  );
}
