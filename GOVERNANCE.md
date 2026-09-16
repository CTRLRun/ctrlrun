# Governance

How this project makes decisions, who holds which role, and how it continues if one person
disappears. The rules for a change itself are in [CONTRIBUTING.md](CONTRIBUTING.md); the
rules for a security report are in [SECURITY.md](SECURITY.md).

## Decisions

ctrlrun is maintainer-led. The maintainer has the final say on scope, on what a
specification says, and on whether a change merges or a release ships. A decision that
changes a shipped guarantee is written into the specification and the changelog with its
reason, never settled in a review comment. Disagreement is argued in public, in the issue or
the pull request, and the written specification is the record of what was decided. Anyone may
fork under Apache-2.0; that is the check on the maintainer.

## Roles

| Role | Who | Responsibilities |
|---|---|---|
| Maintainer | Arpan Ghoshal ([@arpanghoshal](https://github.com/arpanghoshal)) | Owns the specifications and the roadmap. Final say on merges and releases. Answers security reports per SECURITY.md. Administers the `ctrlrun` GitHub organization, the `ctrlrun` project on PyPI, and ctrlrun.dev. |
| Committer | Rohan Kamath ([@rohanrkamath](https://github.com/rohanrkamath)) | Reviews and merges pull requests, the maintainer's included. Can cut a release by pushing a tag. Triages issues. Holds write access to every repository in the organization. |
| Contributor | anyone | Opens issues and pull requests under the rules in CONTRIBUTING.md, with every commit signed off under the DCO. |

A role is granted and withdrawn by the maintainer, and this file is the record of who holds
which. Nobody merges their own pull request: every change to `main` is reviewed and merged by
a person who did not write it (CONTRIBUTING.md, *Code review*).

## Continuity

If any one person is unavailable, the project continues within a week, and this is what
makes that true:

- **Two people can merge and release.** The maintainer and the committer both hold write
  access. A release is a tag: `publish.yml` publishes to PyPI through trusted publishing and
  `release.yml` signs the provenance against the workflow's own identity, so no release
  depends on a key or a password that one person holds alone.
- **Two people own the organization.** Both are owners of the `ctrlrun` GitHub organization,
  so either can grant access, change a workflow, or answer a private vulnerability report.
- **Two people own the package.** Both are owners of `ctrlrun` on PyPI, so the trusted
  publisher can be repaired by either.
- **Two people can reach the site.** Both have access to the DNS zone for ctrlrun.dev and to
  the documentation host, so the site and the docs keep publishing.

Nothing about the project lives only on one person's machine: the specifications, the
build, the locks and the release process are all in the repositories.

## Changing this document

By pull request, like anything else, reviewed by the other role holder.
