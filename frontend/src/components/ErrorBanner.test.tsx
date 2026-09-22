import { render, screen } from "@testing-library/react";
import { ErrorBanner } from "./ErrorBanner";
import { ApiError } from "@/lib/types";

describe("ErrorBanner", () => {
  it("shows a friendly message for a known API error code, never a raw stack trace", () => {
    const error = new ApiError(404, { code: "NOT_FOUND", message: "Case 'x' was not found.", details: {} });
    render(<ErrorBanner error={error} />);

    expect(screen.getByText("NOT FOUND")).toBeInTheDocument();
    expect(screen.getAllByText(/was not found/i).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/at\s+\w+\.\w+\s+\(/); // no stack-trace-shaped text
  });

  it("shows a friendly message when the API is unreachable", () => {
    const error = new ApiError(0, {
      code: "API_UNAVAILABLE",
      message: "The investigation API could not be reached. Confirm the backend is running.",
      details: {},
    });
    render(<ErrorBanner error={error} />);
    expect(screen.getAllByText(/could not be reached/i).length).toBeGreaterThan(0);
  });

  it("calls onDismiss when the dismiss button is clicked", () => {
    const onDismiss = jest.fn();
    const error = new Error("generic failure");
    render(<ErrorBanner error={error} onDismiss={onDismiss} />);
    screen.getByText(/dismiss/i).click();
    expect(onDismiss).toHaveBeenCalled();
  });
});
