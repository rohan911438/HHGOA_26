import { render, screen } from "@testing-library/react";
import { LoadingState } from "./LoadingState";

describe("LoadingState", () => {
  it("shows a generic in-progress indicator, not a fabricated backend stage", () => {
    render(<LoadingState />);
    expect(screen.getByText(/ai investigation in progress/i)).toBeInTheDocument();
  });
});
