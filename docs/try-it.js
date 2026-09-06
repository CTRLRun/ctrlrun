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
 * Verified against Pyodide 314.0.6 (Python 3.14.2) with ctrlrun 0.5.0 on 2026-09-06 by
 * docs/assets/verify-browser-demo.mjs, which runs this same sequence under Node.
 */
(function () {
  "use strict";

  var PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.6/full/";
  var CONTAINER = "ctrlrun-browser-demo";

  // `sqlite3` is bundled into Pyodide 314 and is not a loadable package; `pyyaml` is one of
  // Pyodide's own builds; `click` arrives as ctrlrun's dependency through micropip.
  var PACKAGES = ["micropip", "pyyaml"];

  var PROGRAM = [
    "import importlib.metadata, io",
    "from contextlib import redirect_stdout",
    "from pathlib import Path",
    "from ctrlrun.cli.demo import run_demo",
    "",
    "buffer = io.StringIO()",
    "with redirect_stdout(buffer):",
    "    run_demo(Path('/tmp/ctrlrun-demo'))",
    "'ctrlrun ' + importlib.metadata.version('ctrlrun') + ' on Python ' +",
    "  __import__('platform').python_version() + '\\n\\n' + buffer.getvalue()",
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

  ready(function () {
    var container = document.getElementById(CONTAINER);
    if (!container || container.dataset.wired === "yes") return;
    container.dataset.wired = "yes";

    var button = container.querySelector("button");
    var output = container.querySelector("pre");
    if (!button || !output) return;

    var pyodide = null;

    function say(text) {
      output.textContent = text;
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
        say(pyodide.runPython(PROGRAM));
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
  });
})();
