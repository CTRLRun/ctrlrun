// Does docs/try-it.js wire the button when the container appears *after* the script has run?
// That is the single-page-app case the deployed site hit: Mintlify runs a custom script once,
// when the page becomes interactive, which on this site is before React paints the page and
// never again on a client-side navigation. The button did nothing.
//
//     npm install jsdom
//     node docs/assets/verify-browser-wiring.mjs
//
// Last run 2026-09-06, all five checks true. The first is the one that keeps the script
// harmless: Mintlify injects it on every page of the site, so a page without the container
// must see nothing happen at all.
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

function mount(dom) {
  dom.window.document.querySelector("main").innerHTML =
    '<div id="ctrlrun-browser-demo"><button type="button"><span>Run ctrlrun demo</span></button><pre>idle</pre></div>';
}

async function settle(dom) {
  await new Promise((resolve) => dom.window.setTimeout(resolve, 50));
}

// 1. No container: the script must touch nothing at all.
{
  const dom = fresh();
  await settle(dom);
  const body = dom.window.document.body.innerHTML;
  console.log("no container, body untouched:", body === "<main></main>");
}

// 2. Container mounted after the script ran: the button must end up wired, and clicking it
//    must reach the loader rather than doing nothing.
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
  console.log("wired after late mount:", container.dataset.wired === "yes");

  container.querySelector("button").dispatchEvent(new dom.window.MouseEvent("click"));
  await settle(dom);
  console.log("click reached the loader:", asked);
  console.log("failure told the reader what to do:",
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
  console.log("re-wired after navigation:",
    dom.window.document.getElementById("ctrlrun-browser-demo").dataset.wired === "yes");
}
