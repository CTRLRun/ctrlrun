# A pull request's head moves after the merge was approved

**Through the gateway, an approved merge lands on whatever the head is when it runs, unless the
call carries `expectedHeadSha`.** The gateway has no precondition recheck (SPEC-v0.7 §6.4), so
the whole interval between a human's approval and the agent's call is open. With
`expectedHeadSha`, GitHub refuses the stale merge and nothing lands, and because the gateway
hashes the exact arguments, the SHA is part of what the human approved.

The question came from [github/github-mcp-server#3230](https://github.com/github/github-mcp-server/discussions/3230),
where a reader ran the same race against `Control.execute` with a fake provider and asked which
row changes through the gateway. This is that run, through the real stack:

```text
agent ──▶ ctrlrun gateway ──▶ github-mcp-server (http mode) ──▶ fake GitHub REST
```

Only GitHub is fake. [`fake_github.py`](fake_github.py) keeps the merge endpoint's documented
answers: `409 Head branch was modified` on a `sha` mismatch, `405 Pull Request is not mergeable`
on a merged pull request. Approvals are granted with the real `ctrlrun approve`. Every count
below is what the fake GitHub saw, never what a layer above it reported.

**This reports behaviour, not quality.** github-mcp-server does what its tool schema says:
`expectedHeadSha` is optional and is passed through to the merge API's `sha`. Nothing here is a
finding about it except the shape of its error results, which is described as observed.

## What the run found

[`results/2026-10-05.md`](results/2026-10-05.md), from
[`results/2026-10-05.json`](results/2026-10-05.json): ctrlrun 0.12.2 from PyPI,
github-mcp-server 1.14.0 (`f10e4e1`), Python 3.14.7, macOS arm64. The same rows came back from a
second run and from `main` at `1c02d1e`.

| Path | Scenario | Provider calls | Mutations | Effect record |
| --- | --- | ---: | ---: | --- |
| direct | no `expectedHeadSha`; head moves first | 1 | 1, at the new head | — |
| direct | `expectedHeadSha` = old head; head moves first | 1 | 0 | — |
| direct | merge lands, reply lost; agent retries | 2 | 1 | — |
| gateway | no `expectedHeadSha`; head unchanged | 1 | 1 | `committed` |
| gateway | no `expectedHeadSha`; head moves after approval | 1 | **1, at the new head** | `committed` |
| gateway | `expectedHeadSha` = approved head; head moves after approval | 1 | 0 | `ambiguous` |
| gateway | after that `409`: resolve, approve the new head, merge | 2 | 1, at the new head | `committed` |
| gateway | agent changes `expectedHeadSha` after approval | 0 | 0 | none; `-41002` |
| gateway | merge lands, reply lost; agent retries | 1 | 1 | `ambiguous`; retry `-41005` |
| gateway | as above, with `mcp: {not_executed_on_error: true}` | 2 | 1 | **`failed`** |
| gateway | policy requires `expectedHeadSha` | 1 | 0 | `ambiguous`; no SHA `-41001` |

**The direct rows are the control.** They show what GitHub does on its own: the `sha` check
refuses a stale merge, and a merged pull request cannot be merged again, so a blind retry after a
lost reply is refused by GitHub (`405`) rather than merged twice. For this tool, the second
merge is prevented upstream whatever sits in front of it.

**What the gateway adds.** The SHA is bound into the approval: an agent that changes it gets a
new approval request, not a merge. A retry after a lost reply never leaves the gateway (`1`
provider call, not `2`), and the record says the outcome is unknown instead of letting the agent
read `EOF` as a failure.

**What the gateway does not add.** A recheck of the head before the call. A policy document
cannot name a precondition provider, so the gateway refuses a fingerprinted approval rather than
skipping the check, and an approval without one carries no claim about the world. Without
`expectedHeadSha`, the head-moves row merges at a head nobody approved.

## Four things this run turned up

1. **A GitHub `409` leaves the effect `ambiguous`.** github-mcp-server returns both GitHub's
   `409` and a dropped connection as a tool result with `isError: true` and one text item:
   `failed to merge pull request: PUT …: 409 Head branch was modified. …` for the first,
   `failed to merge pull request: Put "…": EOF` for the second, after which the pull request was
   merged. The gateway cannot tell a definitive refusal from a lost reply, so it
   records both as unknown, and the identical call or a retarget to the new head is refused
   `-41005` until someone runs `ctrlrun resolve merge:acme/checkout:4471 --failed`. After that,
   a merge at the new head is a new approval request, and once granted it lands (the
   after-`409` row). That is the fail-closed reading, and it costs a human step after every
   stale merge.

2. **`mcp: {not_executed_on_error: true}` is false for this server.** It asserts that a tool
   error means nothing happened. Here a merge landed, the reply was lost, the server reported
   `isError: EOF`, and the gateway recorded `failed`. The retry asked a human again and, once
   approved, reached GitHub. Only GitHub's own `405` stopped a second merge. Leave it unset for
   github-mcp-server.

3. **Requiring `expectedHeadSha` works through a fallback.** A rule
   `when: { expectedHeadSha_neq: "" }` → `approve`, above a catch-all `deny`, refuses a call
   without the SHA (`-41001`, no provider call). It works because a condition on an absent
   argument is false, and the gateway logs it as a probable typo:
   `condition expectedHeadSha_neq ignored: the action has no argument 'expectedHeadSha'`.
   SPEC-v0.1 §3.2 reads an absent argument as a typo because a decorated call is bound to its
   signature with defaults applied. A `tools/call` is not: an MCP client omits an optional
   argument, and the gateway sees it absent.

4. **The `-41005` after the `409` leaves no receipt and no event.** With the approval consumed,
   the identical call takes the gateway's pre-check (`_before_asking_a_human`), which refuses on
   the effect record before `Control.execute` is reached, and nothing is written. In the
   stale-merge row, four tool calls left two proposed actions and one receipt; in the
   lost-reply row, three calls left two and one (the results file's last column). The gateway
   pages say every call that reaches a decision leaves a receipt, denied ones included; these
   refusals leave neither a receipt nor an event.

## What this does not establish

- **GitHub is a fake.** It models one endpoint and the three answers above. The real API's
  behaviour under a concurrent push, branch protection or a merge queue is not exercised.
- **The agent is scripted.** It re-sends the identical call, as the gateway documentation says
  to. No model is in the loop, so nothing here says what an agent chooses to do after a `409`.
- **One tool, one transport.** `merge_pull_request` over github-mcp-server's `http` mode, with
  the client on MCP revision `2025-06-18`. Other write tools and the `stdio` transport were not
  run.
- **Each scenario ran once per run.** The stack is deterministic here; two runs and a run on
  `main` agreed, which is the extent of the repetition.

## Running it

```console
$ gh release download v1.14.0 -R github/github-mcp-server -p 'github-mcp-server_Darwin_arm64.tar.gz'
$ tar xzf github-mcp-server_Darwin_arm64.tar.gz
$ pip install "ctrlrun[gateway]"
$ python research/github-merge-head-race/run.py --github-mcp-server ./github-mcp-server \
      --out research/github-merge-head-race/results/$(date -u +%F).json \
      --markdown research/github-merge-head-race/results/$(date -u +%F).md
```

The gateway and `ctrlrun approve` are the ones installed beside the interpreter that runs the
script. Everything listens on loopback, on ports chosen at startup. Nothing reaches GitHub, and
the token the agent sends is an invented string shaped like a PAT, because github-mcp-server
refuses one that is not.

Like the other directories under `research/`, this is not part of the `ctrlrun` package and is
not versioned with it.
