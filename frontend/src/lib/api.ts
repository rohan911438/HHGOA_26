/**
 * Phase 2L - the only place in this frontend that talks to the backend.
 * No component calls `fetch` directly, and nothing here ever talks to
 * TigerGraph, an LLM provider, or MCP - only this API, via
 * NEXT_PUBLIC_API_URL (see .env.example).
 */

import type {
  ApiErrorEnvelope,
  CaseRecord,
  ContextResponse,
  DetailedHealthResponse,
  EvidenceResponse,
  HealthResponse,
  HistoryResponse,
  InvestigationRequest,
  InvestigationResponse,
  SimilarCasesResponse,
} from "./types";
import { ApiError } from "./types";

function apiBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_URL;
  if (!url) {
    throw new Error(
      "NEXT_PUBLIC_API_URL is not set. Copy .env.example to .env.local and point it at the backend API."
    );
  }
  return url.replace(/\/+$/, "");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // Network failure (backend down, DNS, CORS) - fetch() itself threw,
    // there is no response/status to read. Never a raw browser stack trace.
    throw new ApiError(0, {
      code: "API_UNAVAILABLE",
      message: "The investigation API could not be reached. Confirm the backend is running.",
      details: { url: apiBaseUrl() + path },
    });
  }

  if (!response.ok) {
    let body: ApiErrorEnvelope | null = null;
    try {
      body = (await response.json()) as ApiErrorEnvelope;
    } catch {
      // Response wasn't JSON (e.g. a proxy/gateway error page) - fall through.
    }
    if (body?.error) {
      throw new ApiError(response.status, body.error);
    }
    throw new ApiError(response.status, {
      code: "UNKNOWN_ERROR",
      message: `The API returned an unexpected error (HTTP ${response.status}).`,
      details: {},
    });
  }

  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function getDependencyHealth(): Promise<DetailedHealthResponse> {
  return request<DetailedHealthResponse>("/health/dependencies");
}

export function startInvestigation(body: InvestigationRequest): Promise<InvestigationResponse> {
  return request<InvestigationResponse>("/investigations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getInvestigation(investigationId: string): Promise<InvestigationResponse> {
  return request<InvestigationResponse>(`/investigations/${encodeURIComponent(investigationId)}`);
}

export function getCases(): Promise<CaseRecord[]> {
  return request<CaseRecord[]>("/cases");
}

export function getCase(caseId: string): Promise<CaseRecord> {
  return request<CaseRecord>(`/cases/${encodeURIComponent(caseId)}`);
}

export function getCaseEvidence(caseId: string): Promise<EvidenceResponse> {
  return request<EvidenceResponse>(`/cases/${encodeURIComponent(caseId)}/evidence`);
}

export function getCaseHistory(caseId: string): Promise<HistoryResponse> {
  return request<HistoryResponse>(`/cases/${encodeURIComponent(caseId)}/history`);
}

export function getSimilarCases(
  caseId: string,
  params?: { limit?: number; min_similarity?: number }
): Promise<SimilarCasesResponse> {
  const query = new URLSearchParams();
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.min_similarity !== undefined) query.set("min_similarity", String(params.min_similarity));
  const qs = query.toString();
  return request<SimilarCasesResponse>(`/cases/${encodeURIComponent(caseId)}/similar${qs ? `?${qs}` : ""}`);
}

export function getCaseContext(caseId: string): Promise<ContextResponse> {
  return request<ContextResponse>(`/cases/${encodeURIComponent(caseId)}/context`);
}
