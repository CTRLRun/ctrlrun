// Does docs/try-it.js wire the page's controls when the containers appear *after* the script
// has run? That is the single-page-app case the deployed site hit: Mintlify runs a custom
// script once, when the page becomes interactive, which on this site is before React paints
// the page and never again on a client-side navigation. The button did nothing.
//
//     npm install jsdom
//     node docs/assets/verify-browser-wiring.mjs
//
// Last run 2026-09-07, every check true. The first is the one that keeps the script harmless:
// Mintlify injects it on every page of the site, so a page without a container must see
// nothing happen at all. The playground checks stub Pyodide with a `step` that answers like the
// module does, so what is verified here is the JavaScript's half — which control builds which
// request, which outcome shows the Approve button, which line the reader gets.
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const here = new URL(".", import.meta.url).pathname;
const script = readFileSync(`${here}../try-it.js`, "utf8");

function fresh() {
  const dom = new JSDOM("<!doctype html><html><body><main></main></body></html>", {
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  // The page is "interactive" before the container exists, which is the whole point.
  dom.window.eval(script);
  return dom;
}

const DEMO =
  '<div id="ctrlrun-browser-demo"><button type="button"><span>Run ctrlrun demo</span></button><pre>idle</pre></div>';
const PLAYGROUND =
  '<div id="ctrlrun-playground">' +
  '<input type="number" name="amount" value="500">' +
  '<input type="text" name="payment_id" value="txn_1">' +
  '<input type="checkbox" name="lose_reply">' +
  '<button type="button" name="run">Refund</button>' +
  '<button type="button" name="approve">Approve</button>' +
  "<pre>idle</pre></div>";

function mount(dom, html = DEMO) {
  dom.window.document.querySelector("main").innerHTML = html;
}

async function settle(dom) {
  await new Promise((resolve) => dom.window.setTimeout(resolve, 50));
}

let failures = 0;
function check(label, ok) {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) failures += 1;
}

// 1. No container: the script must touch nothing at all.
{
  const dom = fresh();
  await settle(dom);
  check("no container, body untouched", dom.window.document.body.innerHTML === "<main></main>");
}

// 2. Demo container mounted after the script ran: the button must end up wired, and clicking
//    it must reach the loader rather than doing nothing.
{
  const dom = fresh();
  let asked = false;
  dom.window.loadPyodide = async () => {
    asked = true;
    throw new Error("stub: not loading a real runtime here");
  };
  mount(dom);
  await settle(dom);
  const container = dom.window.document.getElementById("ctrlrun-browser-demo");
  check("demo wired after late mount", container.dataset.wired === "yes");

  container.querySelector("button").dispatchEvent(new dom.window.MouseEvent("click"));
  await settle(dom);
  check("demo click reached the loader", asked);
  check("demo failure told the reader what to do",
    container.querySelector("pre").textContent.includes("pip install ctrlrun && ctrlrun demo"));
}

// 3. A second mount, as a single-page navigation would produce: wired again.
{
  const dom = fresh();
  mount(dom);
  await settle(dom);
  dom.window.document.querySelector("main").innerHTML = "";
  await settle(dom);
  mount(dom);
  await settle(dom);
  check("demo re-wired after navigation",
    dom.window.document.getElementById("ctrlrun-browser-demo").dataset.wired === "yes");
}

// 4. The playground: every control builds the request the module expects, and every outcome
//    the module can return has a line. Pyodide is a stub whose `step` answers by shape.
{
  const dom = fresh();
  const requests = [];
  const stub = {
    loadPackage: async () => undefined,
    pyimport: () => ({ install: async () => undefined }),
    globals: { set: (name, value) => { stub.request = JSON.parse(value); } },
    runPython(code) {
      if (code !== "step(PLAYGROUND_REQUEST)") return undefined; // the module load
      const r = stub.request;
      requests.push(r);
      if (r.op === "approve") return JSON.stringify({ op: "approve", approval_id: r.request_id, action_hash: "sha256:abc" });
      let outcome = "executed";
      if (r.approval_id && r.amount !== 200000) outcome = "approval_mismatch";
      else if (r.amount > 1000000 || r.amount < 0) outcome = "denied";
      else if (r.amount > 100000 && !r.approval_id) outcome = "approval_required";
      else if (r.lose_reply) outcome = "reply_lost";
      else if (r.payment_id === "dup") outcome = "duplicate";
      else if (r.payment_id === "amb") outcome = "ambiguous_retry";
      return JSON.stringify({ op: "refund", outcome, reason: outcome === "approval_mismatch" ? "mismatch" : "rule[2]", request_id: "apr_1", remote_calls: 0, receipt: { decision: "allow", result: "committed" } });
    },
  };
  dom.window.loadPyodide = async () => stub;
  mount(dom, PLAYGROUND);
  await settle(dom);
  const panel = dom.window.document.getElementById("ctrlrun-playground");
  check("playground wired after late mount", panel.dataset.wired === "yes");
  const amount = panel.querySelector('input[name="amount"]');
  const payment = panel.querySelector('input[name="payment_id"]');
  const lose = panel.querySelector('input[name="lose_reply"]');
  const run = panel.querySelector('button[name="run"]');
  const approve = panel.querySelector('button[name="approve"]');
  const log = () => panel.querySelector("pre").textContent;
  check("approve hidden until something is pending", approve.hidden === true);

  const click = async (button) => {
    button.dispatchEvent(new dom.window.MouseEvent("click"));
    await settle(dom);
  };
  await click(run);
  check("€500 builds a 50000-cent request", requests[0].amount === 50000 && requests[0].payment_id === "txn_1" && requests[0].lose_reply === false);
  check("executed line names the receipt", log().includes("executed; receipt committed"));

  amount.value = "2000";
  payment.value = "txn_2";
  await click(run);
  check("€2,000 asks a human and shows Approve", log().includes("ApprovalRequired") && approve.hidden === false);
  await click(approve);
  check("Approve sends op=approve with the request id", requests.at(-1).op === "approve" && requests.at(-1).request_id === "apr_1");
  check("Approve hides itself and names the grant", approve.hidden === true && log().includes("human approves apr_1"));
  amount.value = "5000";
  await click(run);
  check("€5,000 presents the approval and is refused", requests.at(-1).approval_id === "apr_1" && log().includes("ApprovalMismatch"));
  amount.value = "2000";
  await click(run);
  check("€2,000 presents the same approval and executes", requests.at(-1).approval_id === "apr_1" && log().includes("executed"));

  payment.value = "txn_3";
  amount.value = "20000";
  await click(run);
  check("€20,000 does not present txn_2's approval", requests.at(-1).approval_id === undefined);
  check("denied line", log().includes("ActionDenied"));

  payment.value = "txn_4";
  amount.value = "500";
  lose.checked = true;
  await click(run);
  check("lose the reply is sent", requests.at(-1).lose_reply === true && log().includes("AMBIGUOUS"));
  payment.value = "amb";
  lose.checked = false;
  await click(run);
  check("ambiguous retry line", log().includes("AmbiguousEffect"));
  payment.value = "dup";
  await click(run);
  check("duplicate line", log().includes("DuplicateEffect"));
  amount.value = "abc";
  await click(run);
  check("a non-number is refused by the page, not sent", log().includes("enter a number") && requests.at(-1).payment_id === "dup");
}

// 5. A playground whose runtime fails to load tells the reader what to do.
{
  const dom = fresh();
  dom.window.loadPyodide = async () => { throw new Error("stub: no runtime"); };
  mount(dom, PLAYGROUND);
  await settle(dom);
  const panel = dom.window.document.getElementById("ctrlrun-playground");
  panel.querySelector('button[name="run"]').dispatchEvent(new dom.window.MouseEvent("click"));
  await settle(dom);
  check("playground failure told the reader what to do",
    panel.querySelector("pre").textContent.includes("pip install ctrlrun && ctrlrun demo"));
}

if (failures) {
  console.log(`${failures} check(s) failed`);
  process.exit(1);
}
