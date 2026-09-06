/*
 * The browser demo, for docs/try-it.mdx.
 *
 * Mintlify includes every .js file in the content directory on every page and cannot scope one
 * to a single page, so this does nothing at all unless the page it belongs to has mounted its
 * container. Everything it touches lives inside that container.
 *
 * What it does: load Pyodide from the jsDelivr CDN, install `ctrlrun` from PyPI with micropip,
 * and run `ctrlrun demo` in the reader's own browser. Nothing is sent anywhere: the demo is
 * in-process, writes its evidence to Pyodide's in-memory filesystem, and opens no socket. The
 * version it prints is whatever is released on PyPI, which is the version a reader would get
 * from `pip install ctrlrun`.
 *
 * Verified against Pyodide 314.0.6 (Python 3.14.2) with ctrlrun 0.5.0 by
 * docs/assets/verify-browser-demo.mjs, which reads PROGRAM out of this file and runs it under
 * Node. It read its own copy until 2026-09-06, and a syntax error in the copy that shipped
 * reached the deployed page.
 */
(function () {
  "use strict";

  var PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.6/full/";
  var CONTAINER = "ctrlrun-browser-demo";

  // `sqlite3` is bundled into Pyodide 314 and is not a loadable package; `pyyaml` is one of
  // Pyodide's own builds; `click` arrives as ctrlrun's dependency through micropip.
  var PACKAGES = ["micropip", "pyyaml"];

  // The Python this page runs. `runPython` returns the value of the last expression, so the
  // last line is an expression rather than an assignment, and it is a single line: a trailing
  // `+` outside brackets is a SyntaxError, which is exactly what shipped here and put a
  // traceback in front of every reader. Two harnesses claimed to have verified this page and
  // neither ran this string — `verify-browser-wiring.mjs` loads this script with Pyodide
  // stubbed, and `verify-browser-demo.mjs` carried its own copy of the program. The demo
  // harness now reads this array, and `tests/test_docs_travelling.py` compiles it on every
  // commit, which needs no network and no Node. Keep it JSON-parseable: double-quoted strings,
  // no comment inside the array, no trailing comma.
  var PROGRAM = [
    "import importlib.metadata, io, platform",
    "from contextlib import redirect_stdout",
    "from pathlib import Path",
    "from ctrlrun.cli.demo import run_demo",
    "",
    "buffer = io.StringIO()",
    "with redirect_stdout(buffer):",
    "    run_demo(Path('/tmp/ctrlrun-demo'))",
    "version = importlib.metadata.version('ctrlrun')",
    "f'ctrlrun {version} on Python {platform.python_version()}\\n\\n' + buffer.getvalue()"
  ].join("\n");

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  function load(src) {
    return new Promise(function (resolve, reject) {
      var tag = document.createElement("script");
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = function () {
        reject(new Error("could not load " + src));
      };
      document.head.appendChild(tag);
    });
  }

  function wire() {
    var container = document.getElementById(CONTAINER);
    if (!container || container.dataset.wired === "yes") return;

    var button = container.querySelector("button");
    var output = container.querySelector("pre");
    if (!button || !output) return;
    container.dataset.wired = "yes";

    var pyodide = null;

    function say(text) {
      output.textContent = text;
    }

    // The demo prints all five scenarios at once. Released a line at a time, as the README's
    // recording does, a reader can follow each one. The text is what the demo printed and
    // nothing else; the only thing added is time, and the colour on each refusal.
    var LINE_MS = 380;
    var REFUSED = "#f28b82";

    function reveal(text) {
      output.textContent = "";
      var lines = text.split("\n");
      return new Promise(function (resolve) {
        var index = 0;
        function next() {
          if (index === lines.length) return resolve();
          var line = lines[index++];
          var node;
          if (line.indexOf("BLOCKED") !== -1) {
            node = document.createElement("span");
            node.style.color = REFUSED;
            node.style.fontWeight = "700";
            node.textContent = line;
          } else {
            node = document.createTextNode(line);
          }
          // Follow the newest line, but only for a reader who is already at the bottom:
          // yanking somebody back who has scrolled up to re-read scenario 1 is worse than
          // not following at all. The box has a max height, so this is a real scroll — it
          // was dead code while the box could only grow, and the reader got a horizontal
          // scrollbar and a page that got taller instead of a terminal that scrolled.
          var following = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
          output.appendChild(node);
          output.appendChild(document.createTextNode("\n"));
          if (following) output.scrollTop = output.scrollHeight;
          window.setTimeout(next, line.trim() === "" ? 0 : LINE_MS);
        }
        next();
      });
    }

    async function boot() {
      say("Loading Python in this tab. The first run downloads about 10 MB from the Pyodide\nCDN and the ctrlrun wheel from PyPI; after that the browser caches them.");
      if (!window.loadPyodide) await load(PYODIDE + "pyodide.js");
      var runtime = await window.loadPyodide({ indexURL: PYODIDE });
      say("Installing ctrlrun from PyPI…");
      await runtime.loadPackage(PACKAGES);
      var micropip = runtime.pyimport("micropip");
      await micropip.install("ctrlrun");
      return runtime;
    }

    async function run() {
      button.disabled = true;
      var label = button.textContent;
      button.textContent = "Running…";
      try {
        if (!pyodide) pyodide = await boot();
        say("Running the five scenarios…");
        await reveal(pyodide.runPython(PROGRAM));
        button.textContent = "Run it again";
      } catch (error) {
        say(
          "The demo did not run in this browser:\n\n    " +
            (error && error.message ? error.message : String(error)) +
            "\n\nThat is this page failing, not CTRLRun. Run it locally instead:\n\n" +
            "    pip install ctrlrun && ctrlrun demo\n"
        );
        button.textContent = label;
      } finally {
        button.disabled = false;
      }
    }

    button.addEventListener("click", run);
  }

  // The site is a single-page app and this script runs once, when the page becomes
  // interactive: on a first load that can be before React has painted the container, and on a
  // navigation from another page the script does not run again at all. Both were true on the
  // deployed site, where the button did nothing. So wiring is attempted now and again on every
  // DOM change; `wire` is a `getElementById` and a flag check when there is nothing to do, and
  // the flag lives on the element, so a container React mounts afresh is wired afresh.
  ready(wire);
  if (window.MutationObserver) {
    new MutationObserver(wire).observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
  }
})();
