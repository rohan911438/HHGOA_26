import { render, screen } from "@testing-library/react";
import { EvidencePanel } from "./EvidencePanel";
import { mockEvidence } from "@/test/fixtures";

describe("EvidencePanel", () => {
  it("renders each evidence item's type, observation, quality, status, provenance and id", () => {
    render(<EvidencePanel evidence={mockEvidence} detailAvailable={true} />);

    expect(screen.getByText("Shared Card")).toBeInTheDocument();
    expect(screen.getByText(/12 other transaction\(s\) share the same card identifier/)).toBeInTheDocument();
    expect(screen.getByText(mockEvidence[0].evidence_id)).toBeInTheDocument();
    expect(screen.getByText("find_shared_card_activity")).toBeInTheDocument();
  });

  it("keeps LOW-quality evidence visibly labeled LOW, never upgraded", () => {
    render(<EvidencePanel evidence={mockEvidence} detailAvailable={true} />);

    const addressCard = screen.getByText("Shared Address").closest("li")!;
    expect(addressCard).toHaveTextContent("LOW");
    expect(addressCard).toHaveTextContent("312 other transaction(s) share the same address identifier");
    expect(addressCard).not.toHaveTextContent("HIGH");
  });

  it("shows a note instead of fabricating detail when full evidence is unavailable", () => {
    render(<EvidencePanel evidence={[]} detailAvailable={false} note="Full detail not available for this case." />);
    expect(screen.getByText(/full detail not available/i)).toBeInTheDocument();
  });
});
