import { render, screen } from "@testing-library/react";
import { NextBestActionPanel } from "./NextBestActionPanel";
import { mockPolicy } from "@/test/fixtures";

describe("NextBestActionPanel", () => {
  it("renders whatever the API actually returned, not a hardcoded action", () => {
    render(<NextBestActionPanel policy={mockPolicy} nextStep="Awaiting ANALYST approval for CREATE_CASE." />);

    expect(screen.getByText("Next Best Action")).toBeInTheDocument();
    expect(screen.getByText("CREATE CASE")).toBeInTheDocument();
    expect(screen.getByText("ANALYST").closest("*")).toBeInTheDocument();
  });

  it("distinguishes recommendation from execution when executable is false", () => {
    render(<NextBestActionPanel policy={mockPolicy} nextStep="x" />);
    expect(screen.getByText(/recommendation only/i)).toBeInTheDocument();
    expect(screen.getByText(/no real-world action executed/i)).toBeInTheDocument();
  });

  it("renders a different action correctly when the backend returns one (never hardcoded)", () => {
    render(
      <NextBestActionPanel
        policy={{ ...mockPolicy, action: "MONITOR_ACCOUNT", executable: true, approval_required: false }}
        nextStep="No approval required."
      />
    );
    expect(screen.getByText("MONITOR ACCOUNT")).toBeInTheDocument();
    expect(screen.queryByText(/recommendation only/i)).not.toBeInTheDocument();
  });

  it("shows a fallback when no policy decision exists", () => {
    render(<NextBestActionPanel policy={null} nextStep="" />);
    expect(screen.getByText(/no policy decision was produced/i)).toBeInTheDocument();
  });
});
