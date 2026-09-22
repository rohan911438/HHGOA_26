import { render, screen } from "@testing-library/react";
import { UncertaintyPanel } from "./UncertaintyPanel";
import { mockUncertainty } from "@/test/fixtures";

describe("UncertaintyPanel", () => {
  it("renders the uncertainty level and the raw overall_uncertainty value", () => {
    render(<UncertaintyPanel uncertainty={mockUncertainty} />);
    expect(screen.getByText("LOW")).toBeInTheDocument();
    expect(screen.getByText(/overall_uncertainty = 0.178/)).toBeInTheDocument();
  });

  it("never calls uncertainty a fraud probability/confidence", () => {
    render(<UncertaintyPanel uncertainty={mockUncertainty} />);
    // The disclaimer itself legitimately says "...is not fraud probability" -
    // what must never appear is a *label* presenting the number as one.
    expect(screen.getByText(/investigation uncertainty is not fraud probability/i)).toBeInTheDocument();
    expect(screen.queryByText(/^fraud probability$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^fraud confidence$/i)).not.toBeInTheDocument();
    expect(document.body.textContent?.toLowerCase()).not.toContain("probability of fraud");
  });

  it("shows evidence-collection-complete when there is no missing evidence", () => {
    render(<UncertaintyPanel uncertainty={mockUncertainty} />);
    expect(screen.getByText(/evidence collection complete/i)).toBeInTheDocument();
  });

  it("shows additional-evidence-required with reasons when evidence is missing", () => {
    render(
      <UncertaintyPanel
        uncertainty={{
          ...mockUncertainty,
          missing_evidence: [
            { evidence_type: "shared_device", reason: "NOT_INVESTIGATED", source_status: null, impact: "Device linkage was never queried." },
          ],
        }}
      />
    );
    expect(screen.getByText(/additional evidence required/i)).toBeInTheDocument();
    expect(screen.getByText(/device linkage was never queried/i)).toBeInTheDocument();
  });

  it("renders a fallback when no assessment exists", () => {
    render(<UncertaintyPanel uncertainty={null} />);
    expect(screen.getByText(/no uncertainty assessment was produced/i)).toBeInTheDocument();
  });
});
