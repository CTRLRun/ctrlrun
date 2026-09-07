/*
 * The medical affairs portal, for docs/demos/medical-affairs.mdx.
 *
 * Mintlify includes every .js file in the content directory on every page and cannot scope one
 * to a single page, so this does nothing at all unless the page it belongs to has mounted its
 * container. Everything it touches lives inside that container.
 *
 * What it does: load Pyodide from the jsDelivr CDN, install `ctrlrun` from PyPI with micropip,
 * and run real CTRLRun in the reader's own browser -- the same arrangement docs/try-it.js uses,
 * over a different policy. Nothing is sent anywhere: the medical portal and the safety database
 * are fakes in the same process, the state store is in memory, and no socket is opened.
 *
 * MODULE is a JSON array of lines so the test suite can read it out rather than carrying a
 * copy. `tests/test_docs_medical_demo.py` runs it natively through the whole sequence the page
 * tells the reader to press, on every commit, with no Node and no network.
 *
 * The product, the physician's question and the three references are invented. Nothing here is
 * medical information about a real medicine, and the page says so above the fold.
 */
(function () {
  "use strict";

  var PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.6/full/";
  var CONTAINER = "ctrlrun-medical-demo";

  // `sqlite3` is bundled into Pyodide 314 and is not a loadable package; `pyyaml` is one of
  // Pyodide's own builds; `click` arrives as ctrlrun's dependency through micropip.
  var PACKAGES = ["micropip", "pyyaml"];

  // Public API only -- `Control`, `InMemoryStateStore`, `LocalApprovalProvider`,
  // `Policy.from_yaml`, `@protect`, `context`, `with_approval`, and the exceptions -- so what a
  // reader sees here is what the same calls do in their own process. An approval is a
  // `grant_approval` on the store, the same write `ctrlrun approve` makes; there is no
  // auto-approve and no dry run. Keep it JSON-parseable: double-quoted strings, no comment
  // inside the array, no trailing comma.
  var MODULE = [
    "import json",
    "from ctrlrun import Control, InMemoryStateStore, LocalApprovalProvider, Policy",
    "from ctrlrun import ActionDenied, AmbiguousEffect, ApprovalMismatch, ApprovalRequired, DuplicateEffect",
    "from ctrlrun import context, protect, with_approval",
    "",
    "POLICY = \"\"\"",
    "schema: ctrlrun.policy/v2",
    "actions:",
    "  literature.search:",
    "    effect: \"search:{inquiry_id}:{revision}\"",
    "    decision: allow",
    "  safety.report_icsr:",
    "    effect: \"icsr:{inquiry_id}\"",
    "    decision: allow",
    "  response.send_to_hcp:",
    "    effect: \"mi_response:{inquiry_id}\"",
    "    decision: approve",
    "  response.cite_unapproved_use:",
    "    effect: \"unapproved_use:{inquiry_id}\"",
    "    decision: deny",
    "\"\"\"",
    "",
    "INQUIRY = \"MI-2026-004471\"",
    "",
    "DRAFTS = [",
    "    {",
    "        \"revision\": \"A\",",
    "        \"retrieved\": \"2026-09-01\",",
    "        \"recommendation\": \"No dose adjustment is required in moderate hepatic impairment.\",",
    "        \"references\": [\"PMID 37884120\", \"PMID 38119042\", \"PMID 38446701\"],",
    "    },",
    "    {",
    "        \"revision\": \"B\",",
    "        \"retrieved\": \"2026-09-07\",",
    "        \"recommendation\": \"A 50% dose reduction is recommended in moderate hepatic impairment.\",",
    "        \"references\": [\"PMID 37884120\", \"PMID 40219847\", \"PMID 38446701\"],",
    "    },",
    "]",
    "",
    "",
    "class Portal:",
    "    def __init__(self):",
    "        self.letters = []",
    "        self.cases = []",
    "        self.lose_reply = False",
    "",
    "    def send_letter(self, inquiry_id):",
    "        self.letters.append(inquiry_id)",
    "        return {\"document\": \"SRL-0442\", \"status\": \"sent\"}",
    "",
    "    def report_case(self, inquiry_id):",
    "        self.cases.append(inquiry_id)",
    "        if self.lose_reply:",
    "            raise TimeoutError(\"no response from the safety database after 30s\")",
    "        return {\"case\": \"AER-2026-0881\", \"status\": \"received\"}",
    "",
    "",
    "store = InMemoryStateStore()",
    "control = Control(Policy.from_yaml(POLICY, source=\"<medical-demo>\"), store, LocalApprovalProvider(store))",
    "portal = Portal()",
    "draft_index = [0]",
    "",
    "",
    "@protect(\"literature.search\", effect=\"search:{inquiry_id}:{revision}\", control=control)",
    "def search(inquiry_id, revision, query):",
    "    return {\"hits\": 3}",
    "",
    "",
    "@protect(\"response.send_to_hcp\", effect=\"mi_response:{inquiry_id}\", control=control)",
    "def send_to_hcp(inquiry_id, recommendation, references):",
    "    return portal.send_letter(inquiry_id)",
    "",
    "",
    "@protect(\"safety.report_icsr\", effect=\"icsr:{inquiry_id}\", control=control)",
    "def report_icsr(inquiry_id, term):",
    "    return portal.report_case(inquiry_id)",
    "",
    "",
    "@protect(\"response.cite_unapproved_use\", effect=\"unapproved_use:{inquiry_id}\", control=control)",
    "def cite_unapproved_use(inquiry_id, claim):",
    "    return portal.send_letter(inquiry_id)",
    "",
    "",
    "@protect(\"response.publish_unreviewed\", effect=\"unreviewed:{inquiry_id}\", control=control)",
    "def publish_unreviewed(inquiry_id):",
    "    return portal.send_letter(inquiry_id)",
    "",
    "",
    "def _draft():",
    "    return DRAFTS[draft_index[0]]",
    "",
    "",
    "def _send(approval_id):",
    "    draft = _draft()",
    "    if approval_id:",
    "        with with_approval(approval_id):",
    "            return send_to_hcp(inquiry_id=INQUIRY, recommendation=draft[\"recommendation\"], references=draft[\"references\"])",
    "    return send_to_hcp(inquiry_id=INQUIRY, recommendation=draft[\"recommendation\"], references=draft[\"references\"])",
    "",
    "",
    "def step(request_json):",
    "    request = json.loads(request_json)",
    "    op = request[\"op\"]",
    "    result = {\"op\": op, \"inquiry_id\": INQUIRY}",
    "    try:",
    "        with context(agent=\"mi-agent\", user=\"msl-de-0042\"):",
    "            if op == \"draft\":",
    "                draft = _draft()",
    "                search(inquiry_id=INQUIRY, revision=draft[\"revision\"], query=\"hepatic impairment\")",
    "                result[\"outcome\"] = \"executed\"",
    "            elif op == \"approve\":",
    "                approval = store.grant_approval(request[\"request_id\"], \"human:medical-reviewer\")",
    "                result[\"outcome\"] = \"approved\"",
    "                result[\"approval_id\"] = approval.approval_id",
    "                result[\"action_hash\"] = approval.action_hash",
    "            elif op == \"refresh\":",
    "                draft_index[0] = 1",
    "                result[\"outcome\"] = \"refreshed\"",
    "                result[\"superseded\"] = \"PMID 38119042 superseded by PMID 40219847\"",
    "            elif op == \"send\":",
    "                _send(request.get(\"approval_id\"))",
    "                result[\"outcome\"] = \"executed\"",
    "            elif op == \"event\":",
    "                portal.lose_reply = bool(request.get(\"lose_reply\"))",
    "                report_icsr(inquiry_id=INQUIRY, term=\"hepatic enzyme increased\")",
    "                result[\"outcome\"] = \"executed\"",
    "            elif op == \"unapproved\":",
    "                cite_unapproved_use(inquiry_id=INQUIRY, claim=\"use in an unapproved indication\")",
    "                result[\"outcome\"] = \"executed\"",
    "            elif op == \"unreviewed\":",
    "                publish_unreviewed(inquiry_id=INQUIRY)",
    "                result[\"outcome\"] = \"executed\"",
    "            else:",
    "                result[\"outcome\"] = \"unknown_op\"",
    "    except ApprovalRequired as pending:",
    "        record = store.get_approval(pending.request_id)",
    "        result[\"outcome\"] = \"approval_required\"",
    "        result[\"request_id\"] = pending.request_id",
    "        result[\"action_hash\"] = record.action_hash if record else \"\"",
    "    except ApprovalMismatch as refused:",
    "        result[\"outcome\"] = \"approval_mismatch\"",
    "        result[\"reason\"] = refused.reason",
    "    except ActionDenied as refused:",
    "        result[\"outcome\"] = \"denied\"",
    "        result[\"reason\"] = refused.reason",
    "    except DuplicateEffect as refused:",
    "        result[\"outcome\"] = \"duplicate\"",
    "        result[\"reason\"] = refused.state",
    "    except AmbiguousEffect as refused:",
    "        result[\"outcome\"] = \"ambiguous_retry\"",
    "        result[\"reason\"] = refused.effect_key",
    "    except TimeoutError as lost:",
    "        result[\"outcome\"] = \"reply_lost\"",
    "        result[\"reason\"] = str(lost)",
    "    draft = _draft()",
    "    result[\"revision\"] = draft[\"revision\"]",
    "    result[\"retrieved\"] = draft[\"retrieved\"]",
    "    result[\"recommendation\"] = draft[\"recommendation\"]",
    "    result[\"references\"] = draft[\"references\"]",
    "    result[\"letters\"] = len(portal.letters)",
    "    result[\"cases\"] = len(portal.cases)",
    "    return json.dumps(result)"
  ].join("\n");

  var MONO = "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;";

  //: The reference the newer study brings in. The panel marks it so a reader can see which
  //: line moved without diffing two revisions by eye.
  var NEW_REFERENCE = "PMID 40219847";

  // Built as nodes rather than markup. Nothing here is attacker-controlled -- every string
  // comes from the module in this file -- but a page whose whole subject is a boundary does
  // not hand strings to an HTML parser to make a bulleted list.
  function el(tag, style, text) {
    var node = document.createElement(tag);
    node.setAttribute("style", style);
    if (text) node.textContent = text;
    return node;
  }

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

  // What each outcome the module can return says to a reader, and which colour it is shown in.
  // Every branch of `step`'s try/except is named here; a new one with no entry would print
  // its raw outcome, which the test suite refuses.
  var OUTCOMES = {
    executed: ["ran", "ok"],
    approved: ["the reviewer signed for this exact letter", "ok"],
    refreshed: ["the draft changed under the signature", "warn"],
    approval_required: ["a human decides", "warn"],
    approval_mismatch: ["BLOCKED — the approved letter is not the letter being sent", "stop"],
    denied: ["BLOCKED — the policy refuses this", "stop"],
    duplicate: ["BLOCKED — this letter already went out", "stop"],
    ambiguous_retry: ["BLOCKED — the case may already be filed; blind retry refused", "stop"],
    reply_lost: ["the reply never came back — outcome AMBIGUOUS, not failed", "warn"],
    unknown_op: ["nothing to do", "ok"],
  };

  // The site is a single-page app and this script runs once, when the page becomes
  // interactive: on a first load that can be before React has painted the container, and on a
  // navigation from another page the script does not run again at all. Both were true of the
  // first version of this file, where every button did nothing. So wiring is attempted now and
  // again on every DOM change; `wire` is a `getElementById` and a flag check when there is
  // nothing to do, and the flag lives on the element, so a container React mounts afresh is
  // wired afresh.
  function wire() {
    var root = document.getElementById(CONTAINER);
    if (!root) return; // every other page on the site
    if (root.getAttribute("data-wired") === "yes") return;
    root.setAttribute("data-wired", "yes");

    var find = function (name) {
      return root.querySelector("[name='" + name + "']");
    };
    var buttons = {};
    ["draft", "send", "approve", "refresh", "event", "unapproved", "reset"].forEach(function (name) {
      buttons[name] = find(name);
    });
    var loseReply = find("lose_reply");
    var letter = find("letter");
    var transcript = find("transcript");
    if (!letter || !transcript || !buttons.draft) return;

    var pyodide = null;
    var pending = null;
    var approval = null;
    var busy = false;

    function say(text) {
      transcript.textContent += (transcript.textContent ? "\n" : "") + text;
      transcript.scrollTop = transcript.scrollHeight;
    }

    function stage(next) {
      Object.keys(buttons).forEach(function (name) {
        var button = buttons[name];
        if (!button) return;
        var live = name === "reset" || name === "event" || name === "unapproved" || name === next;
        button.disabled = !live;
        button.style.opacity = live ? "1" : "0.35";
        button.style.cursor = live ? "pointer" : "not-allowed";
        button.style.boxShadow = name === next ? "0 0 0 3px rgba(184,115,10,0.35)" : "none";
      });
    }

    function render(result) {
      var made = document.createDocumentFragment();
      made.appendChild(
        el("div", MONO + "font-size:12px;opacity:0.7;letter-spacing:0.04em;text-transform:uppercase",
          "Standard response letter · " + result.inquiry_id + " · revision " + result.revision +
          " · evidence retrieved " + result.retrieved)
      );
      made.appendChild(el("p", "font-size:16px;margin:12px 0 4px;font-weight:600", result.recommendation));
      made.appendChild(el("div", "font-size:12px;opacity:0.7;margin-bottom:4px", "References"));
      var list = el("ul", "margin:0;padding-left:20px", "");
      (result.references || []).forEach(function (reference, index) {
        var fresh = reference === NEW_REFERENCE;
        list.appendChild(
          el("li", MONO + "font-size:13px;" + (fresh ? "font-weight:600;" : ""),
            "[" + (index + 1) + "] " + reference + (fresh ? "  ← new" : ""))
        );
      });
      made.appendChild(list);
      letter.textContent = "";
      letter.appendChild(made);
    }

    async function run(op, extra) {
      if (busy) return;
      busy = true;
      try {
        if (!pyodide) {
          pyodide = await runtime(say);
          pyodide.runPython(MODULE);
          say("ctrlrun is running in this tab. Nothing leaves it.\n");
        }
        var request = { op: op };
        if (extra) Object.keys(extra).forEach(function (key) { request[key] = extra[key]; });
        pyodide.globals.set("MEDICAL_REQUEST", JSON.stringify(request));
        var result = JSON.parse(pyodide.runPython("step(MEDICAL_REQUEST)"));
        report(op, result);
        render(result);
      } catch (error) {
        say(
          "\nThis did not run in this browser:\n\n    " +
            (error && error.message ? error.message : String(error)) +
            "\n\nThat is this page failing, not CTRLRun. Run it locally instead:\n\n    pip install ctrlrun && ctrlrun demo"
        );
      } finally {
        busy = false;
      }
    }

    function report(op, result) {
      var known = OUTCOMES[result.outcome] || [result.outcome, "warn"];
      var mark = known[1] === "stop" ? "✗" : known[1] === "warn" ? "○" : "✓";
      say("\n$ " + op);
      say("  " + mark + " " + known[0] + (result.reason ? "  (" + result.reason + ")" : ""));

      if (result.outcome === "approval_required") {
        pending = result.request_id;
        say("    request " + result.request_id);
        say("    bound to " + (result.action_hash || "").slice(0, 27) + "…");
        stage("approve");
      } else if (result.outcome === "approved") {
        approval = result.approval_id;
        say("    approval " + result.approval_id);
        say("    action hash " + (result.action_hash || "").slice(0, 27) + "…");
        say("    the reviewer read revision " + result.revision + " and signed for that one");
        stage("refresh");
      } else if (result.outcome === "refreshed") {
        say("    " + result.superseded);
        say("    the letter now recommends the opposite, under yesterday's signature");
        stage("send");
      } else if (result.outcome === "approval_mismatch") {
        say("    the approval covers a letter with a different recommendation");
        say("    nothing but a new approval, on the new letter, moves this on");
        stage("reset");
      } else if (op === "draft") {
        stage("send");
      }
      say("  letters sent to the physician: " + result.letters + "   safety cases filed: " + result.cases);
    }

    buttons.draft.addEventListener("click", function () { run("draft"); });
    buttons.send.addEventListener("click", function () { run("send", approval ? { approval_id: approval } : null); });
    buttons.approve.addEventListener("click", function () { run("approve", { request_id: pending }); });
    buttons.refresh.addEventListener("click", function () { run("refresh"); });
    if (buttons.event) {
      buttons.event.addEventListener("click", function () {
        run("event", { lose_reply: !!(loseReply && loseReply.checked) });
      });
    }
    if (buttons.unapproved) {
      buttons.unapproved.addEventListener("click", function () { run("unapproved"); });
    }
    if (buttons.reset) {
      buttons.reset.addEventListener("click", function () {
        pending = null;
        approval = null;
        transcript.textContent = "";
        if (pyodide) pyodide.runPython(MODULE);
        stage("draft");
        letter.textContent = "";
        say("A fresh store, a fresh policy, revision A. Press Retrieve and draft.");
      });
    }

    stage("draft");
    say("Press Retrieve and draft. The first press downloads Python and the ctrlrun wheel.");
  }

  ready(wire);
  if (window.MutationObserver) {
    new MutationObserver(wire).observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
  }
})();
