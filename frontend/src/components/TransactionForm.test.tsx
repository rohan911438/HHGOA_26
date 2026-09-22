import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DEMO_TRANSACTION_ID, TransactionForm } from "./TransactionForm";

describe("TransactionForm", () => {
  it("defaults the transaction id to the demo transaction without auto-submitting", () => {
    const onSubmit = jest.fn();
    render(<TransactionForm disabled={false} onSubmit={onSubmit} />);

    const input = screen.getByLabelText(/transaction id/i) as HTMLInputElement;
    expect(input.value).toBe(DEMO_TRANSACTION_ID);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("lets the user change the transaction id", async () => {
    const user = userEvent.setup();
    const onSubmit = jest.fn();
    render(<TransactionForm disabled={false} onSubmit={onSubmit} />);

    const input = screen.getByLabelText(/transaction id/i);
    await user.clear(input);
    await user.type(input, "1234567");

    expect((input as HTMLInputElement).value).toBe("1234567");
  });

  it("submits the current transaction id and trigger on click", async () => {
    const user = userEvent.setup();
    const onSubmit = jest.fn();
    render(<TransactionForm disabled={false} onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: /investigate/i }));

    expect(onSubmit).toHaveBeenCalledWith(DEMO_TRANSACTION_ID, "FRAUD_SIGNAL");
  });

  it("disables the button while an investigation is already running", () => {
    render(<TransactionForm disabled={true} onSubmit={jest.fn()} />);
    expect(screen.getByRole("button", { name: /investigating/i })).toBeDisabled();
  });
});
