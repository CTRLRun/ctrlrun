// Does the Try-it page's Python run in Pyodide? The honest test behind docs/docs/try-it.mdx:
//
//     npm install pyodide
//     node docs/assets/verify-browser-demo.mjs
//
// It runs the page's own Python, read out of docs/try-it.js: the PLAYGROUND module, driven
// through the six steps the page tells the reader to try, and then PROGRAM, which is
// `ctrlrun demo`. It did not always: until 2026-09-06 this file carried its own copy of the
// program, so it verified code the page never ran, and the copy that shipped ended a line on a
// trailing `+` — a SyntaxError, which every reader got instead of the demo. A harness with its
// own copy of the artifact verifies the copy. `tests/test_docs_travelling.py` compiles the demo
// and runs the playground natively on every commit, so that class of break fails in CI without
// Node or the network; this file is what proves both *run under Pyodide*.
//
// Last run 2026-09-07: Pyodide 314.0.6, Python 3.14.2, SQLite 3.39.0, ctrlrun 0.5.0 from PyPI,
// the playground sequence and all five scenarios. `sqlite3` is bundled into Pyodide 314 and
// must NOT be passed to loadPackage, which is the one thing that failed the first time.
import { readFileSync, writeFileSync } from "node:fs";
import { loadPyodide } from "pyodide";

// The page's arrays are JSON by construction, so this is a parse and not an eval: a harness
// that re-implemented the extraction could drift the same way the copy did.
const here = new URL(".", import.meta.url).pathname;
const script = readFileSync(`${here}../try-it.js`, "utf8");

function pythonIn(name) {
  const body = new RegExp(`var ${name} = \\[(.*?)\\]\\.join`, "s").exec(script);
  if (!body) throw new Error(`docs/try-it.js: no ${name} array — the page's Python moved`);
  return JSON.parse(`[${body[1]}]`).join("\n");
}
const PLAYGROUND = pythonIn("PLAYGROUND");
const PROGRAM = pythonIn("PROGRAM");
console.log("programs read from docs/try-it.js:", PLAYGROUND.split("\n").length, "+", PROGRAM.split("\n").length, "lines");

const pyodide = await loadPyodide();
console.log("pyodide", pyodide.version, "python", pyodide.runPython("import sys; sys.version"));

console.log("sqlite3 bundled:", pyodide.runPython("import sqlite3; sqlite3.sqlite_version"));
await pyodide.loadPackage(["micropip", "pyyaml"]);
const micropip = pyodide.pyimport("micropip");
await micropip.install("ctrlrun");
const versions = {
  date: new Date().toLocaleDateString("en-CA"), // YYYY-MM-DD, local
  pyodide: pyodide.version,
  python: pyodide.runPython("import platform; platform.python_version()"),
  sqlite: pyodide.runPython("import sqlite3; sqlite3.sqlite_version"),
  ctrlrun: pyodide.runPython("import importlib.metadata; importlib.metadata.version('ctrlrun')"),
};
console.log("ctrlrun", versions.ctrlrun);

// --- the playground: the same calls the page's JavaScript makes, the same sequence the page
//     tells the reader to try, and the outcome each step must report.
pyodide.runPython(PLAYGROUND);
function step(request) {
  pyodide.globals.set("PLAYGROUND_REQUEST", JSON.stringify(request));
  return JSON.parse(pyodide.runPython("step(PLAYGROUND_REQUEST)"));
}
function expect(label, result, outcome, extra = {}) {
  const ok = result.outcome === outcome && Object.entries(extra).every(([k, v]) => result[k] === v);
  console.log(`${ok ? "ok  " : "FAIL"} ${label}: ${result.outcome}${result.reason ? " (" + result.reason + ")" : ""} remote_calls=${result.remote_calls}`);
  if (!ok) throw new Error(`${label}: expected ${outcome} ${JSON.stringify(extra)}, got ${JSON.stringify(result)}`);
  return result;
}
expect("1 €500 txn_1", step({ op: "refund", payment_id: "txn_1", amount: 50000, lose_reply: false }), "executed", { remote_calls: 1 });
const asked = expect("2 €2,000 txn_2", step({ op: "refund", payment_id: "txn_2", amount: 200000, lose_reply: false }), "approval_required", { remote_calls: 0 });
const granted = step({ op: "approve", request_id: asked.request_id });
if (granted.approval_id !== asked.request_id) throw new Error("approve did not grant the request");
console.log("ok   2 human approves", granted.approval_id);
expect("2 €5,000 on that approval", step({ op: "refund", payment_id: "txn_2", amount: 500000, approval_id: asked.request_id }), "approval_mismatch", { reason: "mismatch", remote_calls: 0 });
expect("2 €2,000 on that approval", step({ op: "refund", payment_id: "txn_2", amount: 200000, approval_id: asked.request_id }), "executed", { remote_calls: 1 });
expect("3 the approval again", step({ op: "refund", payment_id: "txn_2", amount: 200000, approval_id: asked.request_id }), "approval_mismatch", { reason: "consumed", remote_calls: 1 });
expect("4 €20,000 txn_3", step({ op: "refund", payment_id: "txn_3", amount: 2000000, lose_reply: false }), "denied", { remote_calls: 0 });
expect("5 €500 txn_4, reply lost", step({ op: "refund", payment_id: "txn_4", amount: 50000, lose_reply: true }), "reply_lost", { remote_calls: 1 });
expect("5 retry txn_4", step({ op: "refund", payment_id: "txn_4", amount: 50000, lose_reply: false }), "ambiguous_retry", { remote_calls: 1 });
expect("6 €500 txn_1 again", step({ op: "refund", payment_id: "txn_1", amount: 50000, lose_reply: false }), "duplicate", { remote_calls: 1 });

// --- the demo
const out = pyodide.runPython(PROGRAM);
console.log(out);

// What ran, for the page to quote and a test to hold it to: the transcript on the page names
// a ctrlrun version and a Python version, and this is the only place those numbers come from.
writeFileSync(`${here}browser-demo.verified.json`, JSON.stringify(versions, null, 2) + "\n");
console.log("wrote browser-demo.verified.json:", JSON.stringify(versions));
