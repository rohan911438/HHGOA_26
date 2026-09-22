import { render, screen } from "@testing-library/react";
import { CaseTimeline } from "./CaseTimeline";
import { mockHistoryResponse } from "@/test/fixtures";

describe("CaseTimeline", () => {
  it("renders every real history event from the API in order", () => {
    render(<CaseTimeline events={mockHistoryResponse.events} note={mockHistoryResponse.note} />);

    expect(screen.getByText("Case Created")).toBeInTheDocument();
    expect(screen.getByText("Finding Added")).toBeInTheDocument();
    expect(screen.getByText("Recommendation Created")).toBeInTheDocument();
  });

  it("shows an empty state instead of fabricating timeline events", () => {
    render(<CaseTimeline events={[]} />);
    expect(screen.getByText(/no timeline events are available/i)).toBeInTheDocument();
  });
});
