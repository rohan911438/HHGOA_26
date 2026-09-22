import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Home from "./page";
import {
  DEMO_CASE_ID,
  DEMO_TXN,
  mockCaseRecord,
  mockHistoryResponse,
  mockInvestigationResponse,
  mockSimilarCasesResponse,
} from "@/test/fixtures";

/**
 * Phase 2L - the integration test spec item: transaction input -> API
 * call -> result rendering. `global.fetch` is mocked (not `lib/api.ts`
 * itself), so this exercises the real request-building/parsing code in
 * src/lib/api.ts against the actual backend response shape - see
 * src/test/fixtures.ts.
 */

const API_BASE = "http://localhost:8000";

// A minimal fetch Response stand-in - jest-environment-jsdom does not
// implement the real WHATWG Response class, and src/lib/api.ts's
// request() only ever calls `.ok` and `.json()` on what fetch() returns,
// so a full polyfill isn't needed to exercise the real request-building/
// parsing code in src/lib/api.ts.
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

// The real page also fetches GET /health/dependencies on mount (a real,
// honest health check - see src/lib/health.ts) - every test's mock must
// answer it, never leave it unhandled, since it fires before any user
// interaction.
const mockHealthyDependencies = {
  status: "ok",
  service: "hhgoa-fraud-agent",
  dependencies: [
    { name: "tigergraph", checked: true, healthy: true, detail: "reachable", latency_ms: 12.3 },
    { name: "llm", checked: true, healthy: true, detail: "configured", latency_ms: null },
  ],
};

function mockFetchImplementation(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? "GET";

  if (method === "GET" && url === `${API_BASE}/health/dependencies`) {
    return Promise.resolve(jsonResponse(mockHealthyDependencies));
  }
  if (method === "POST" && url === `${API_BASE}/investigations`) {
    return Promise.resolve(jsonResponse(mockInvestigationResponse));
  }
  if (method === "GET" && url === `${API_BASE}/cases/${DEMO_CASE_ID}`) {
    return Promise.resolve(jsonResponse(mockCaseRecord));
  }
  if (method === "GET" && url === `${API_BASE}/cases/${DEMO_CASE_ID}/history`) {
    return Promise.resolve(jsonResponse(mockHistoryResponse));
  }
  if (method === "GET" && url.startsWith(`${API_BASE}/cases/${DEMO_CASE_ID}/similar`)) {
    return Promise.resolve(jsonResponse(mockSimilarCasesResponse));
  }
  throw new Error(`Unexpected fetch in test: ${method} ${url}`);
}

/** Overrides only the POST /investigations response, delegating every
 * other route (notably the mount-time health check) to the normal mock -
 * avoids the call-order fragility of `mockImplementationOnce`, since the
 * health check always fires before any user-triggered request. */
function withInvestigationsOverride(
  handler: (url: string, init?: RequestInit) => Promise<Response>
): typeof fetch {
  return jest.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (method === "POST" && url === `${API_BASE}/investigations`) {
      return handler(url, init);
    }
    return mockFetchImplementation(url, init);
  }) as unknown as typeof fetch;
}

describe("Home page - full investigation flow", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = jest.fn((input: RequestInfo | URL, init?: RequestInit) =>
      mockFetchImplementation(typeof input === "string" ? input : input.toString(), init)
    ) as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("renders the premium investigation shell before any investigation starts", () => {
    render(<Home />);
    expect(screen.getByText(/fraud\/\/graph/i)).toBeInTheDocument();
    expect(screen.getByText(/agentic fraud investigation platform/i)).toBeInTheDocument();
    expect(screen.getAllByText(/start investigation/i).length).toBeGreaterThan(0);
  });

  it("does not investigate automatically on load", () => {
    render(<Home />);
    // A real health check is expected on mount - what must never happen
    // automatically is starting an investigation.
    expect(global.fetch).not.toHaveBeenCalledWith(
      `${API_BASE}/investigations`,
      expect.objectContaining({ method: "POST" })
    );
    expect(screen.getByText(/graph-powered evidence workflow/i)).toBeInTheDocument();
  });

  it("shows the loading state while the request is genuinely still pending", async () => {
    // A deliberately-unresolved fetch - deterministic proof the loading
    // state renders before any result, with no timing race against a
    // real (however small) delay.
    let releaseResponse: (() => void) | undefined;
    const pending = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    global.fetch = withInvestigationsOverride(async (url, init) => {
      await pending;
      return mockFetchImplementation(url, init);
    });

    const user = userEvent.setup();
    render(<Home />);
    await user.click(screen.getByRole("button", { name: /investigate/i }));

    expect(await screen.findByText(/ai investigation in progress/i)).toBeInTheDocument();

    releaseResponse?.();
    await waitFor(() => expect(screen.getByText(DEMO_CASE_ID)).toBeInTheDocument());
  });

  it("runs transaction input -> API call -> rendered result end to end", async () => {
    const user = userEvent.setup();
    render(<Home />);

    await user.click(screen.getByRole("button", { name: /investigate/i }));

    // The real response, rendered.
    await waitFor(() => expect(screen.getByText(DEMO_CASE_ID)).toBeInTheDocument());

    expect(global.fetch).toHaveBeenCalledWith(
      `${API_BASE}/investigations`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ transaction_id: DEMO_TXN, trigger: "FRAUD_SIGNAL" }),
      })
    );

    // Summary header reflects the actual response, never a hardcoded value.
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(screen.getAllByText(/CREATE_CASE|CREATE CASE/).length).toBeGreaterThan(0);

    // Secondary panels populated from their own dedicated endpoints.
    await waitFor(() => expect(screen.getByText("PENDING REVIEW")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("Case Created")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("case-synthetic-1")).toBeInTheDocument());
  });

  it("surfaces a structured API error without crashing when the backend rejects the request", async () => {
    global.fetch = withInvestigationsOverride(() =>
      Promise.resolve(
        jsonResponse(
          { error: { code: "INVALID_REQUEST", message: "transaction_id is required.", details: {} } },
          400
        )
      )
    );

    const user = userEvent.setup();
    render(<Home />);
    await user.click(screen.getByRole("button", { name: /investigate/i }));

    await waitFor(() => expect(screen.getByText("INVALID REQUEST")).toBeInTheDocument());
    expect(screen.queryByText(/ai investigation in progress/i)).not.toBeInTheDocument();
  });

  it("shows a friendly error when the API is unreachable, never a raw network stack trace", async () => {
    global.fetch = withInvestigationsOverride(() => Promise.reject(new TypeError("Failed to fetch")));

    const user = userEvent.setup();
    render(<Home />);
    await user.click(screen.getByRole("button", { name: /investigate/i }));

    await waitFor(() => expect(screen.getAllByText(/could not be reached/i).length).toBeGreaterThan(0));
  });
});
