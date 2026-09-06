// Does `ctrlrun demo` run in Pyodide? The honest test behind docs/try-it.mdx:
//
//     npm install pyodide
//     node docs/assets/verify-browser-demo.mjs
//
// It runs the page's own Python, read out of docs/try-it.js. It did not always: until
// 2026-09-06 this file carried its own copy of the program, so it verified code the page never
// ran, and the copy that shipped ended a line on a trailing `+` — a SyntaxError, which every
// reader got instead of the demo. A harness with its own copy of the artifact verifies the
// copy. `tests/test_docs_travelling.py` compiles the same string on every commit, so that
// class of break fails in CI without Node or the network; this file is what proves it *runs*.
//
// Last run 2026-09-06: Pyodide 314.0.6, Python 3.14.2, SQLite 3.39.0, ctrlrun 0.5.0 from PyPI,
// all five scenarios. `sqlite3` is bundled into Pyodide 314 and must NOT be passed to
// loadPackage, which is the one thing that failed the first time.
import { readFileSync } from "node:fs";
import { loadPyodide } from "pyodide";

// The page's PROGRAM array is JSON by construction, so this is a parse and not an eval: a
// harness that re-implemented the extraction could drift the same way the copy did.
const here = new URL(".", import.meta.url).pathname;
const script = readFileSync(`${here}../try-it.js`, "utf8");
const body = /var PROGRAM = \[(.*?)\]\.join/s.exec(script);
if (!body) throw new Error("docs/try-it.js: no PROGRAM array — the page's Python moved");
const PROGRAM = JSON.parse(`[${body[1]}]`).join("\n");
console.log("program read from docs/try-it.js:", PROGRAM.split("\n").length, "lines");

const pyodide = await loadPyodide();
console.log("pyodide", pyodide.version, "python", pyodide.runPython("import sys; sys.version"));

console.log("sqlite3 bundled:", pyodide.runPython("import sqlite3; sqlite3.sqlite_version"));
await pyodide.loadPackage(["micropip", "pyyaml"]);
const micropip = pyodide.pyimport("micropip");
await micropip.install("ctrlrun");

const out = pyodide.runPython(PROGRAM);
console.log(out);
