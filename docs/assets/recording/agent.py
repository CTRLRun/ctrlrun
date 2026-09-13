import sys

import ctrlrun


class FakeStripe:
    """Stands in for the provider: it records calls instead of making them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def refund(self, payment_id: str, amount: int) -> dict:
        self.calls.append((payment_id, amount))
        return {"id": f"re_{payment_id}", "amount": amount, "status": "succeeded"}


stripe = FakeStripe()


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}")
def refund(payment_id: str, amount: int) -> dict:
    return stripe.refund(payment_id, amount)


if __name__ == "__main__":
    with ctrlrun.context(agent="support-agent"):
        print("€500   ->", refund(payment_id="txn_1", amount=50_000)["status"])
        try:
            refund(payment_id="txn_2", amount=500_000)
        except ctrlrun.ApprovalRequired as pending:
            print("€5,000 -> a human decides:", pending.request_id)
            with open("request_id.txt", "w") as handle:
                handle.write(pending.request_id)
        else:
            sys.exit("the €5,000 refund ran without a human; the policy is not in force")
    print("calls that reached the provider:", len(stripe.calls))
