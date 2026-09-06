// Does `ctrlrun demo` run in Pyodide? The honest test behind docs/try-it.mdx, run before that
// page claimed anything. It performs the same sequence the page's script performs, under Node:
//
//     npm install pyodide
//     node docs/assets/verify-browser-demo.mjs
//
// Last run 2026-09-06: Pyodide 314.0.6, Python 3.14.2, SQLite 3.39.0, ctrlrun 0.5.0 from PyPI,
// all five scenarios. `sqlite3` is bundled into Pyodide 314 and must NOT be passed to
// loadPackage, which is the one thing that failed the first time.
import { loadPyodide } from "pyodide";

const pyodide = await loadPyodide();
console.log("pyodide", pyodide.version, "python", pyodide.runPython("import sys; sys.version"));

console.log("sqlite3 bundled:", pyodide.runPython("import sqlite3; sqlite3.sqlite_version"));
await pyodide.loadPackage(["micropip", "pyyaml"]);
const micropip = pyodide.pyimport("micropip");
await micropip.install("ctrlrun");

const out = pyodide.runPython(`
import importlib.metadata, io, sys
from pathlib import Path
from contextlib import redirect_stdout
from ctrlrun.cli.demo import run_demo

version = importlib.metadata.version("ctrlrun")
buffer = io.StringIO()
with redirect_stdout(buffer):
    run_demo(Path("/tmp/demo"))
f"ctrlrun {version}\\n" + buffer.getvalue()
`);
console.log(out);
