# Extracted by tools/docs_audit/render_cookbook.py from
# docs/docs/cookbook/outbound-email-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

sent: list[str] = []


def smtp_send(message_id: str, to: str) -> str:
    sent.append(to)
    return "sent"


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect("email.send", effect="email:{message_id}", control=control)
def send(message_id: str, to: str, to_domain: str, subject: str) -> str:
    return smtp_send(message_id, to)


with ctrlrun.context(agent="assistant"):
    print(
        "to a colleague:",
        send(message_id="m_1", to="li@example.com", to_domain="example.com", subject="Q3 numbers"),
    )

    try:
        send(message_id="m_2", to="press@partner.co", to_domain="partner.co", subject="Q3 numbers")
    except ctrlrun.ApprovalRequired as pending:
        print("to partner.co: a human decides:", pending.request_id)
        request_id = pending.request_id
    else:
        raise SystemExit("external mail went out without a human")

    store.grant_approval(request_id, "human:li@example.com")
    with ctrlrun.with_approval(request_id):
        # The agent re-plans the recipient after the yes.
        try:
            send(
                message_id="m_2",
                to="tips@journalist.example",
                to_domain="journalist.example",
                subject="Q3 numbers",
            )
        except ctrlrun.ApprovalMismatch:
            print("to a different address with the same approval: refused")
        else:
            raise SystemExit("an approval for one recipient sent mail to another")
        print(
            "to partner.co, as approved:",
            send(
                message_id="m_2",
                to="press@partner.co",
                to_domain="partner.co",
                subject="Q3 numbers",
            ),
        )

print("messages sent:", sent)
