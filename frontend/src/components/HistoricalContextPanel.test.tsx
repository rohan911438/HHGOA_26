import { render, screen } from "@testing-library/react";
import { HistoricalContextPanel } from "./HistoricalContextPanel";
import { mockSimilarCasesResponse } from "@/test/fixtures";

describe("HistoricalContextPanel", () => {
  it("renders similarity score, matching features, previous actions and outcome", () => {
    render(<HistoricalContextPanel cases={mockSimilarCasesResponse.similar_cases} note={mockSimilarCasesResponse.note} />);

    expect(screen.getByText("case-synthetic-1")).toBeInTheDocument();
    expect(screen.getByText(/similarity 0.620/)).toBeInTheDocument();
    expect(screen.getByText("shared_card")).toBeInTheDocument();
    expect(screen.getByText(/UNRESOLVED/)).toBeInTheDocument();
  });

  it("clearly labels a synthetic case, never presenting it as a real bank case", () => {
    render(<HistoricalContextPanel cases={mockSimilarCasesResponse.similar_cases} />);
    expect(screen.getByText("SYNTHETIC DEVELOPMENT CASE")).toBeInTheDocument();
  });

  it("shows an empty state instead of fabricating cases", () => {
    render(<HistoricalContextPanel cases={[]} />);
    expect(screen.getByText(/no similar historical cases/i)).toBeInTheDocument();
  });
});
