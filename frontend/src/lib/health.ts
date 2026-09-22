/**
 * Shared, honest dependency-status derivation.
 *
 * Every caller of this function is showing the *real* result of
 * `GET /health/dependencies` - never a hardcoded "online" badge. A
 * dependency is only ever reported healthy after it has actually been
 * checked (`DependencyStatus.checked`/`healthy` from the backend, which
 * itself never claims TigerGraph is healthy without a real probe - see
 * backend/docs/phase-2-api.md §5.2).
 */

import type { DetailedHealthResponse } from "./types";

export interface DependencyStatusDisplay {
  label: string;
  tone: "ok" | "warn" | "danger";
}

export function describeDependency(
  health: DetailedHealthResponse | null | undefined,
  matchName: (name: string) => boolean,
  labels: { ok: string; degraded: string; unavailable: string }
): DependencyStatusDisplay {
  if (!health) {
    return { label: labels.unavailable, tone: "danger" };
  }

  const dependency = health.dependencies.find((d) => matchName(d.name.toLowerCase()));
  if (!dependency) {
    return { label: labels.unavailable, tone: "danger" };
  }

  if (dependency.healthy === true) {
    return { label: labels.ok, tone: "ok" };
  }
  if (dependency.healthy === false) {
    return { label: labels.degraded, tone: "warn" };
  }
  return { label: labels.unavailable, tone: "danger" };
}
