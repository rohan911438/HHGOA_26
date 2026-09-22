import { ApiError } from "@/lib/types";

const FRIENDLY_MESSAGE: Record<string, string> = {
  API_UNAVAILABLE: "The investigation API could not be reached. Confirm the backend is running and NEXT_PUBLIC_API_URL is correct.",
  INVALID_REQUEST: "That request was rejected as invalid.",
  NOT_FOUND: "That case or investigation was not found.",
  INVESTIGATION_FAILED: "The investigation pipeline failed before a result could be produced.",
  AGENT_FAILED: "The agent's decision step failed.",
  DEPENDENCY_UNAVAILABLE: "A required backend dependency (TigerGraph or the LLM) is unavailable.",
  INTERNAL_ERROR: "The backend hit an internal error.",
};

export function ErrorBanner({ error, onDismiss }: { error: ApiError | Error; onDismiss?: () => void }) {
  const code = error instanceof ApiError ? error.code : "UNKNOWN_ERROR";
  const message = FRIENDLY_MESSAGE[code] ?? error.message;

  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3">
      <div>
        <p className="text-sm font-semibold text-warn">{code.replace(/_/g, " ")}</p>
        <p className="mt-0.5 text-sm text-muted-strong">{message}</p>
        {error instanceof ApiError && error.message !== message ? (
          <p className="mt-1 text-xs text-muted">{error.message}</p>
        ) : null}
      </div>
      {onDismiss ? (
        <button onClick={onDismiss} className="shrink-0 text-xs text-muted hover:text-muted-strong">
          Dismiss
        </button>
      ) : null}
    </div>
  );
}
