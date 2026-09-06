/*
 * The browser playground and the browser demo, for docs/try-it.mdx.
 *
 * Mintlify includes every .js file in the content directory on every page and cannot scope one
 * to a single page, so this does nothing at all unless the page it belongs to has mounted a
 * container. Everything it touches lives inside those containers.
 *
 * What it does: load Pyodide from the jsDelivr CDN, install `ctrlrun` from PyPI with micropip,
 * and run real CTRLRun in the reader's own browser. Nothing is sent anywhere: the remote is a
 * fake in the same process, the state store is in memory, and no socket is opened. The version
 * is whatever is released on PyPI, which is the version a reader would get from
 * `pip install ctrlrun`.
 *
 * Two pieces of Python live in this file, each a JSON array of lines so that the harnesses and
 * the test suite can read them out rather than carrying a copy:
 *
 *   PLAYGROUND — a module defining `step(request_json) -> result_json` over one `Control`, one
 *                in-memory store and one fake Stripe. The page's controls build a request; the
 *                module runs the protected call and reports what CTRLRun did, as JSON. The
 *                JavaScript owns the DOM and nothing else — every outcome the reader sees was
 *                produced by `ctrlrun` in the tab. `tests/test_docs_travelling.py` runs this
 *                module natively through the whole sequence the page suggests, on every commit,
 *                with no Node and no network.
 *   PROGRAM    — `ctrlrun demo`, the five scenarios, for the button below the playground.
 *
 * Verified against Pyodide 314.0.6 (Python 3.14.2) with ctrlrun 0.5.0 by docs/assets/verify-browser-demo.mjs,
 * which reads both arrays out of this file and runs them under Node. It read its own copy
 * until 2026-09-06, and a syntax error in the copy that shipped reached the deployed page.
 */
(function () {
  "use strict";

  var PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.6/full/";
  var CONTAINER = "ctrlrun-browser-demo";
  var PLAYGROUND_ID = "ctrlrun-playground";

  // `sqlite3` is bundled into Pyodide 314 and is not a loadable package; `pyyaml` is one of
  // Pyodide's own builds; `click` arrives as ctrlrun's dependency through micropip.
  var PACKAGES = ["micropip", "pyyaml"];

  // The playground module. Public API only — `Control`, `InMemoryStateStore`,
  // `LocalApprovalProvider`, `Policy.from_yaml`, `@protect`, `context`, `with_approval`, and the
  // exceptions — so what a reader sees here is what the same calls do in their own process. An
  // approval is a `grant_approval` on the store, the same write `ctrlrun approve` makes; there
  // is no auto-approve and no dry run. Keep it JSON-parseable: double-quoted strings, no comment
  // inside the array, no trailing comma. The policy in it is the one the page shows, and a test
  // holds the two equal.
  var PLAYGROUND = [
    "import json",
    "from ctrlrun import Control, InMemoryStateStore, LocalApprovalProvider, Policy",
    "from ctrlrun import ActionDenied, AmbiguousEffect, ApprovalMismatch, ApprovalRequired, DuplicateEffect",
    "from ctrlrun import context, protect, with_approval",
    "",
    "POLICY = \"\"\"",
    "schema: ctrlrun.policy/v2",
    "actions:",
    "  stripe.refund:",
    "    effect: \"refund:{payment_id}\"",
    "    rules:",
    "      - when: { amount_gte: 0, amount_lte: 100000 }",
    "        decision: allow",
    "      - when: { amount_gte: 0, amount_lte: 1000000 }",
    "        decision: approve",
    "      - decision: deny",
    "\"\"\"",
    "",
    "",
    "class FakeStripe:",
    "    def __init__(self):",
    "        self.calls = []",
    "        self.lose_reply = False",
    "",
    "    def refund(self, payment_id, amount):",
    "        self.calls.append(payment_id)",
    "        if self.lose_reply:",
    "            raise TimeoutError(\"no response from api.stripe.com after 30s\")",
    "        return {\"id\": \"re_\" + payment_id, \"amount\": amount, \"status\": \"succeeded\"}",
    "",
    "",
    "store = InMemoryStateStore()",
    "control = Control(Policy.from_yaml(POLICY, source=\"<playground>\"), store, LocalApprovalProvider(store))",
    "remote = FakeStripe()",
    "",
    "",
    "@protect(\"stripe.refund\", effect=\"refund:{payment_id}\", control=control)",
    "def refund(payment_id, amount):",
    "    return remote.refund(payment_id, amount)",
    "",
    "",
    "def _run(payment_id, amount, approval_id):",
    "    if approval_id:",
    "        with with_approval(approval_id):",
    "            return refund(payment_id=payment_id, amount=amount)",
    "    return refund(payment_id=payment_id, amount=amount)",
    "",
    "",
    "def step(request_json):",
    "    request = json.loads(request_json)",
    "    if request[\"op\"] == \"approve\":",
    "        approval = store.grant_approval(request[\"request_id\"], \"human:you\")",
    "        return json.dumps({\"op\": \"approve\", \"approval_id\": approval.approval_id, \"action_hash\": approval.action_hash})",
    "    payment_id = str(request[\"payment_id\"]).strip() or \"txn_1\"",
    "    amount = int(request[\"amount\"])",
    "    remote.lose_reply = bool(request.get(\"lose_reply\"))",
    "    result = {\"op\": \"refund\", \"payment_id\": payment_id, \"amount\": amount}",
    "    try:",
    "        with context(agent=\"refund-agent\"):",
    "            _run(payment_id, amount, request.get(\"approval_id\"))",
    "        result[\"outcome\"] = \"executed\"",
    "    except ApprovalRequired as pending:",
    "        result[\"outcome\"] = \"approval_required\"",
    "        result[\"request_id\"] = pending.request_id",
    "    except ApprovalMismatch as refused:",
    "        result[\"outcome\"] = \"approval_mismatch\"",
    "        result[\"reason\"] = refused.reason",
    "    except ActionDenied as refused:",
    "        result[\"outcome\"] = \"denied\"",
    "        result[\"reason\"] = refused.reason",
    "    except DuplicateEffect as refused:",
    "        result[\"outcome\"] = \"duplicate\"",
    "        result[\"reason\"] = str(refused)",
    "    except AmbiguousEffect as refused:",
    "        result[\"outcome\"] = \"ambiguous_retry\"",
    "        result[\"reason\"] = str(refused)",
    "    except TimeoutError as lost:",
    "        result[\"outcome\"] = \"reply_lost\"",
    "        result[\"reason\"] = str(lost)",
    "    result[\"remote_calls\"] = remote.calls.count(payment_id)",
    "    receipts = [r for r in store.receipts() if r.arguments.get(\"payment_id\") == payment_id]",
    "    if receipts:",
    "        last = receipts[-1]",
    "        result[\"receipt\"] = {\"decision\": str(last.decision), \"result\": str(last.result), \"effect_key\": last.effect_key, \"approval_id\": last.approval_id}",
    "    return json.dumps(result)"
  ].join("\n");

  // The Python the demo button runs. `runPython` returns the value of the last expression, so
  // the last line is an expression rather than an assignment, and it is a single line: a
  // trailing `+` outside brackets is a SyntaxError, which is exactly what shipped here and put a
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

  // One runtime for both containers: the second one to ask waits on the first one's download.
  var runtimePromise = null;

  function runtime(say) {
    if (runtimePromise) return runtimePromise;
    runtimePromise = (async function () {
      say("Loading Python in this tab. The first run downloads about 10 MB from the Pyodide\nCDN and the ctrlrun wheel from PyPI; after that the browser caches them.");
      if (!window.loadPyodide) await load(PYODIDE + "pyodide.js");
      var pyodide = await window.loadPyodide({ indexURL: PYODIDE });
      say("Installing ctrlrun from PyPI…");
      await pyodide.loadPackage(PACKAGES);
      var micropip = pyodide.pyimport("micropip");
      await micropip.install("ctrlrun");
      return pyodide;
    })();
    runtimePromise.catch(function () {
      runtimePromise = null; // a failed download is retried on the next click
    });
    return runtimePromise;
  }

  function failure(error, command) {
    return (
      "This did not run in this browser:\n\n    " +
      (error && error.message ? error.message : String(error)) +
      "\n\nThat is this page failing, not CTRLRun. Run it locally instead:\n\n    " +
      command +
      "\n"
    );
  }

  var REFUSED = "#f28b82";
  var GRANTED = "#8bd5a0";

  // The transcript goes inside one block child rather than straight into the <pre>.
  // Mintlify's theme lays this <pre> out as `display: flex`, and in a flex container every
  // appended child is a flex item in a *row*: the lines ran off to the right instead of
  // down, and a coloured refusal landed beside its neighbours rather than under them.
  // `say` never hit it because it sets a single text node, which is one item however the
  // box is laid out. The page also asks for `display: block` — belt to this brace, since
  // an inline style loses to an `!important` rule the theme does not have today.
  function blockIn(output) {
    output.textContent = "";
    var body = document.createElement("code");
    body.style.display = "block";
    body.style.whiteSpace = "pre";
    body.style.fontFamily = "inherit";
    body.style.fontSize = "inherit";
    output.appendChild(body);
    return body;
  }

  function lineNode(line) {
    var node;
    if (line.indexOf("BLOCKED") !== -1 || line.indexOf("refused") !== -1 || line.indexOf("denied") !== -1) {
      node = document.createElement("span");
      node.style.color = REFUSED;
      node.style.fontWeight = "700";
      node.textContent = line;
    } else if (line.indexOf("approves") !== -1) {
      node = document.createElement("span");
      node.style.color = GRANTED;
      node.textContent = line;
    } else {
      node = document.createTextNode(line);
    }
    return node;
  }

  // ---- the playground -------------------------------------------------------------------

  function euros(cents) {
    var whole = Math.floor(Math.abs(cents) / 100);
    var rest = Math.abs(cents) % 100;
    var text = "€" + whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    if (rest) text += "." + (rest < 10 ? "0" : "") + rest;
    return cents < 0 ? "-" + text : text;
  }

  function wirePlayground() {
    var panel = document.getElementById(PLAYGROUND_ID);
    if (!panel || panel.dataset.wired === "yes") return;

    var amount = panel.querySelector('input[name="amount"]');
    var payment = panel.querySelector('input[name="payment_id"]');
    var lose = panel.querySelector('input[name="lose_reply"]');
    var run = panel.querySelector('button[name="run"]');
    var approve = panel.querySelector('button[name="approve"]');
    var log = panel.querySelector("pre");
    if (!amount || !payment || !lose || !run || !approve || !log) return;
    panel.dataset.wired = "yes";

    var pyodide = null;
    var lines = null; // the block child the transcript lives in
    var pending = null; // the request a human has not answered yet
    var granted = null; // the approval the next run of that payment presents

    function say(text) {
      lines = null;
      log.textContent = text;
    }

    function write(line) {
      if (!lines) lines = blockIn(log);
      var following = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      lines.appendChild(lineNode(line));
      lines.appendChild(document.createTextNode("\n"));
      if (following) log.scrollTop = log.scrollHeight;
    }

    function step(request) {
      pyodide.globals.set("PLAYGROUND_REQUEST", JSON.stringify(request));
      return JSON.parse(pyodide.runPython("step(PLAYGROUND_REQUEST)"));
    }

    async function boot() {
      var ready = await runtime(say);
      ready.runPython(PLAYGROUND);
      say("");
      return ready;
    }

    function busy(on) {
      run.disabled = on;
      approve.disabled = on;
    }

    async function refund() {
      var cents = Math.round(Number(amount.value) * 100);
      // A number input hands back "" for anything it could not parse, and Number("") is 0:
      // a €0 refund is not what somebody who typed letters asked for.
      if (amount.value.trim() === "" || !isFinite(cents)) {
        write("amount: enter a number of euros");
        return;
      }
      var id = payment.value.trim() || "txn_1";
      var request = { op: "refund", payment_id: id, amount: cents, lose_reply: lose.checked };
      if (granted && granted.payment_id === id) request.approval_id = granted.approval_id;
      var result = step(request);
      var head = "refund " + id + " " + euros(cents) + (request.approval_id ? "  (presenting " + request.approval_id + ")" : "");
      var receipt = result.receipt || {};
      switch (result.outcome) {
        case "executed":
          write(head + "  →  " + receipt.decision + "  →  executed; receipt " + receipt.result + "; remote refund calls: " + result.remote_calls);
          // The approval stays on the panel so the next run presents it again and the reader
          // sees `consumed`: single-use is something to watch, not something to be told.
          break;
        case "approval_required":
          write(head + "  →  approve  →  ApprovalRequired: a human decides " + result.request_id);
          write("   press Approve, then run again — or change the amount after approving and watch it refused");
          pending = { request_id: result.request_id, payment_id: id, amount: cents };
          approve.hidden = false;
          break;
        case "approval_mismatch":
          if (result.reason === "consumed") {
            write(head + "  →  refused: ApprovalMismatch (consumed) — a single-use approval, already spent");
            granted = null;
          } else {
            write(head + "  →  refused: ApprovalMismatch (" + result.reason + ") — the approval is bound to the action the human saw; remote refund calls: " + result.remote_calls);
            write("   the approval is still unspent: run the approved amount and it executes");
          }
          break;
        case "denied":
          write(head + "  →  deny  →  refused: ActionDenied (" + result.reason + "); no request created; remote refund calls: " + result.remote_calls);
          break;
        case "reply_lost":
          write(head + "  →  allow  →  remote commits  →  reply lost  →  effect: AMBIGUOUS; receipt " + receipt.result + "; remote refund calls: " + result.remote_calls);
          write("   run the same payment again: a blind retry is refused until a human or a reconcile hook says what happened");
          break;
        case "ambiguous_retry":
          write(head + "  →  refused: AmbiguousEffect — the remote may already have committed; remote refund calls: " + result.remote_calls);
          write("   only a human moves it on:  ctrlrun resolve refund:" + id + " --committed|--failed");
          break;
        case "duplicate":
          write(head + "  →  refused: DuplicateEffect — refund:" + id + " already happened; remote refund calls: " + result.remote_calls);
          break;
        default:
          write(head + "  →  " + JSON.stringify(result));
      }
      write("");
    }

    async function grant() {
      if (!pending) return;
      var result = step({ op: "approve", request_id: pending.request_id });
      write("human approves " + result.approval_id + "  (bound to " + result.action_hash.slice(0, 19) + "…)");
      write("   run " + euros(pending.amount) + " for " + pending.payment_id + " and it executes; run any other amount on it and it is refused");
      write("");
      granted = { approval_id: result.approval_id, payment_id: pending.payment_id };
      pending = null;
      approve.hidden = true;
    }

    async function click(action) {
      busy(true);
      try {
        if (!pyodide) pyodide = await boot();
        await action();
      } catch (error) {
        say(failure(error, "pip install ctrlrun && ctrlrun demo"));
      } finally {
        busy(false);
      }
    }

    approve.hidden = true;
    run.addEventListener("click", function () {
      click(refund);
    });
    approve.addEventListener("click", function () {
      click(grant);
    });
    payment.addEventListener("change", function () {
      // A new payment id is a new effect; a pending answer belongs to the old one.
      approve.hidden = !pending || pending.payment_id !== payment.value.trim();
    });
  }

  // ---- the demo ---------------------------------------------------------------------------

  function wire() {
    wirePlayground();

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

    function reveal(text) {
      var body = blockIn(output);
      var lines = text.split("\n");
      return new Promise(function (resolve) {
        var index = 0;
        function next() {
          if (index === lines.length) return resolve();
          var line = lines[index++];
          var node = lineNode(line);
          // Follow the newest line, but only for a reader who is already at the bottom:
          // yanking somebody back who has scrolled up to re-read scenario 1 is worse than
          // not following at all. The box has a max height, so this is a real scroll — it
          // was dead code while the box could only grow, and the reader got a horizontal
          // scrollbar and a page that got taller instead of a terminal that scrolled.
          var following = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
          body.appendChild(node);
          body.appendChild(document.createTextNode("\n"));
          if (following) output.scrollTop = output.scrollHeight;
          window.setTimeout(next, line.trim() === "" ? 0 : LINE_MS);
        }
        next();
      });
    }

    async function run() {
      button.disabled = true;
      var label = button.textContent;
      button.textContent = "Running…";
      try {
        if (!pyodide) pyodide = await runtime(say);
        say("Running the five scenarios…");
        await reveal(pyodide.runPython(PROGRAM));
        button.textContent = "Run it again";
      } catch (error) {
        say(failure(error, "pip install ctrlrun && ctrlrun demo"));
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
