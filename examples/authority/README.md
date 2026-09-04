# Two authority configurations

`payments.yaml` and `devops.yaml` are complete `ctrlrun.policy/v3` documents. They are
starting points, not drop-in configuration: **every principal named in them is invented**, and
a grant names a real person or service in a real organization. Adopting somebody else's is how
a template becomes an incident.

Load one the way any policy is loaded:

```console
$ CTRLRUN_CONFIG=examples/authority/payments.yaml ctrlrun stats
```

## What to read them for

**A grant carries no `decision:`.** How much autonomy `stripe.refund` has is the same for the
head of support and for the newest agent. What differs is whether they may propose it at all.
The two axes are evaluated separately, authority first, and combine as the stricter of the
pair — so neither can loosen the other.

**Omission is not "unlimited".** Every grant states every dimension it means to constrain,
because a delegation that drops one its parent constrains is *rejected* rather than treated as
unconstrained. Writing the parents that way makes the children obvious.

**`*` cannot cross a separator.** `k8s.*` matches `k8s.scale` and not `k8s.namespace.delete`.
There is no `?`, no character class, and no short spelling for "everything": the whole surface
of a system is `**`, one token, greppable, and impossible to write by accident.

**`delegable: true` requires `expires_at`.** Authority that can mint more authority and never
lapses is the one shape the model refuses to write down. `devops.yaml` puts the near expiry on
the grant that can delegate, which is the right way round.

**The environment belongs to the deployment.** `devops.yaml` scopes grants to `staging`, and
that word never comes off the call — it is set once on the `Control`. An authorization
dimension the subject can set is not one.

For the delegation chain these documents anticipate, run
[`examples/authority-escalation/`](../authority-escalation/), and read
[`docs/authority.md`](../../docs/authority.md) for the model in plain language.
