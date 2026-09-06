<!-- generated from docs/capabilities.yaml (readme) — edit the YAML, never this table -->
| Guarantee | `@protect` | Gateway | Adapter |
|---|---|---|---|
| **Approval binding** — An approval is bound to the exact action; a mutated or replayed one is refused. | yes | yes | prevention or attribution, per adapter |
| **One effect, once** — One logical effect executes once, across threads, processes and hosts. | yes | yes | yes |
| **Unknown is not failed** — An unknown outcome is AMBIGUOUS, never FAILED, and blocks a blind retry. | yes | yes | yes |
| **Fail closed** — An unknown action, a missing policy or a missing principal is denied. | yes | yes | yes |
| **Authority and delegation** — With authority on, every principal needs a grant, and delegation cannot widen one. | yes | yes | yes |
| **Receipts** — Every executed action leaves a portable JSON receipt of who, what and outcome. | yes | yes | yes |
<!-- end generated -->
