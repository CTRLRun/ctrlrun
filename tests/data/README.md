# `tests/data/`

## `junit-10.xsd`

**JUnit XML has no normative schema** (SPEC-v0.4 §1.4). There is no OASIS or IEEE document
behind it; every CI parser reads a dialect of what Ant emitted. This file is the widely-used
de-facto schema, checked in so T115 has something to validate against, and the real
requirement — *the file is accepted by the parsers people actually run* — is asserted
structurally in the same test rather than dressed up as conformance to a standard that does
not exist.

| | |
|---|---|
| **Source** | <https://github.com/windyroad/JUnit-Schema/blob/master/JUnit.xsd> |
| **Upstream name** | `JUnit.xsd` — "JUnit test result schema for the Apache Ant JUnit and JUnitReport tasks" |
| **Copyright** | © 2011, Windy Road Technology Pty. Limited |
| **Licence** | Apache License 2.0, as the schema's own `xs:documentation` states |
| **Retrieved** | 2026-09-04 |
| **Modified** | No. Byte-for-byte as retrieved. |

It is renamed `junit-10.xsd` here because that is the name the file circulates under; the
content is unchanged, and the copyright and licence notice inside it is intact.

Used by `tests/test_verify_report.py` (T115) and by nothing else. `xmlschema` is in the `dev`
extra for that test and for nothing else; `ctrlrun` does not import it, and it is not a
runtime dependency.
