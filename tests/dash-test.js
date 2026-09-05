/* tests/dash-test.js — runs dash.js against the page the generator has
 * just written, under jsdom. No browser, no Pi.
 *
 * Why (2026-09-06): dash.js is a whole language in this repo that no test
 * executed — `node --check` proved it parses, nothing proved it behaves.
 * The empty detail box (2026-09-01), the missing copy button (2026-09-03)
 * and the chronicle reload (2026-09-05) all lived there and were found by
 * looking at a browser. Everything below reads from tests/ausgabe: the
 * page, the script, status.json, log.txt and chronik.json are the
 * generator's own output, so script and generator cannot drift apart.
 *
 * Needs jsdom:  cd tests && npm install --no-save jsdom
 * Run:          node tests/dash-test.js           (probelauf.py does, if it can)
 */
"use strict";
const fs = require("fs");
const path = require("path");

const OUT = path.join(__dirname, "ausgabe");
let JSDOM;
try {
  ({ JSDOM } = require(path.join(__dirname, "node_modules", "jsdom")));
} catch (e) {
  try { ({ JSDOM } = require("jsdom")); }
  catch (e2) { console.log("  [ -- ] jsdom not installed, skipped (cd tests && npm install --no-save jsdom)"); process.exit(0); }
}

let failures = 0;
function check(ok, text, detail) {
  console.log(`  [${ok ? "ok  " : "FEHL"}] ${text}${ok || !detail ? "" : "  — " + detail}`);
  if (!ok) { failures += 1; }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const read = (name) => fs.readFileSync(path.join(OUT, name), "utf8");
const html = read("index.html");
const script = read("dash.js");
const status = JSON.parse(read("status.json"));
const chronik = JSON.parse(read("chronik.json"));
const logText = read("log.txt");

/* The page ticks every 21 s on the Pi; the typing loop scales with it
   (60 % of the takt for one entry). Four seconds keep the run short
   without touching the script. */
const TAKT = 4;
const page = html.replace(/data-interval="\d+"/, `data-interval="${TAKT}"`)
                 .replace(/data-logintervall="\d+"/, 'data-logintervall="1"');
check(page !== html, "the page carries data-interval and data-logintervall (the takt dash.js reads)");

const dom = new JSDOM(page, { runScripts: "outside-only", pretendToBeVisual: true, url: "http://192.0.2.1/" });
const { window } = dom;
const { document } = window;

/* fetch: the same files, served the way nginx would. One switch lets a
   fetch fail, to see the page mark itself stale. */
let failNext = false;
const files = { "status.json": read("status.json"), "log.txt": logText, "chronik.json": read("chronik.json") };
window.fetch = (p) => {
  if (failNext && p === "status.json") { failNext = false; return Promise.resolve({ ok: false, status: 503 }); }
  const body = files[p];
  if (body === undefined) { return Promise.resolve({ ok: false, status: 404 }); }
  return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(body), json: () => Promise.resolve(JSON.parse(body)) });
};
/* The copy button's fallback path. */
document.execCommand = () => true;

/* The chronicle picks its entry from Date.now(); frozen, the target stays
   put and the typing can finish. Advancing it by one takt is the window
   change the reconciler has to survive. */
const t0 = Date.now();
const setClock = (t) => window.eval(`Date.now = function () { return ${t}; };`);
setClock(t0);

(async () => {
  console.log("\n  dash.js under jsdom");
  window.eval(script);
  await sleep(50);

  /* ----- first fetch: zones, title, freshness ----- */
  const root = document.documentElement;
  check(root.dataset.frisch === "ja", "after the first fetch the page is marked fresh", root.dataset.frisch);
  check(document.title === status.titel, "document.title follows status.json", document.title);
  for (const [name, markup] of Object.entries(status.zonen)) {
    const el = document.getElementById("z-" + name);
    check(el !== null, `zone z-${name} exists on the page`);
    /* Compared as parsed trees: innerHTML serialises `class=x` as
       `class="x"`, so the raw strings never match. */
    if (el && markup) {
      const t = document.createElement("div"); t.innerHTML = markup;
      /* The copy buttons gain 'bereit' once wired — that is dash.js's own doing. */
      const strip = (h) => h.replace(/ bereit\b/g, "");
      check(strip(el.innerHTML) === strip(t.innerHTML), `zone z-${name} carries the markup from status.json`);
    }
  }

  /* ----- the detail box: never empty while not pointing (2026-09-01) ----- */
  const box = document.getElementById("peerdetail");
  check(box && box.textContent.trim().length > 0, "the detail box has text while nothing is pointed at");
  check(box && box.querySelector(".leer") !== null, "the detail box shows the hint");
  check(box && box.querySelector(".blockweg") !== null && box.querySelector(".blockweg").textContent === status.blockweg,
        "the block-path sentence in the box is the one from status.json");

  /* ----- pointing at a peer: foreign text via textContent only ----- */
  const peers = document.querySelectorAll("#netzkarte .peer");
  check(peers.length === status.peers.length, "one dot per peer in status.json", `${peers.length} vs ${status.peers.length}`);
  const evil = status.peers.findIndex((p) => /<b>/.test(p.version || ""));
  check(evil >= 0, "the mock includes a peer whose identifier contains markup");
  if (evil >= 0) {
    peers[evil].dispatchEvent(new window.Event("mouseenter"));
    const shown = box.querySelector(".padresse");
    check(shown && shown.textContent === status.peers[evil].adresse, "the peer's address is shown");
    check(box.querySelector("b:not(.hervor)") === null, "the identifier <b>…</b> did not become an element");
    check(box.textContent.includes("<b>"), "…it is shown as text");
    check(box.querySelector("dl dt") !== null, "the box lists rows for the peer");
  }
  const nabe = document.querySelector("#netzkarte .nabe");
  check(nabe !== null, "the hub is on the map");
  if (nabe) {
    nabe.dispatchEvent(new window.Event("mouseenter"));
    check(box.querySelector(".pkopf") !== null && box.textContent.includes(status.eigen.version),
          "pointing at the hub shows our own node", box.textContent.slice(0, 60));
  }
  document.getElementById("netzkarte").dispatchEvent(new window.Event("mouseleave"));
  check(box.querySelector(".leer") !== null, "leaving the map restores the hint");

  /* ----- copy buttons are wired after the fetch replaced the card (2026-09-03) ----- */
  const buttons = document.querySelectorAll(".kopierknopf");
  check(buttons.length > 0, "the page has copy buttons");
  check([...buttons].every((b) => b.classList.contains("bereit")), "every copy button is wired after the first fetch");
  if (buttons.length) {
    buttons[0].click();
    await sleep(20);
    check(buttons[0].classList.contains("fertig"), "a click marks the button as done");
  }

  /* ----- the log: one span per line, classes from the table, no markup ----- */
  const logBox = document.getElementById("logtext");
  const lines = logText.split("\n");
  const spans = logBox ? logBox.querySelectorAll("span.lz") : [];
  check(spans.length === lines.length, "one span per log line", `${spans.length} vs ${lines.length}`);
  const tip = [...spans].find((s) => s.classList.contains("spitze"));
  check(tip !== undefined && tip.querySelector("b.hervor") !== null && /height=\d+/.test(tip.querySelector("b.hervor").textContent),
        "an UpdateTip line is tinted and its height stands out");
  check(logBox.querySelector("b:not(.hervor)") === null && logBox.textContent === logText,
        "the log text is reproduced verbatim, nothing became markup");

  /* ----- a failed fetch marks the page stale, the next good one clears it ----- */
  failNext = true;
  await sleep(TAKT * 1000 + 100);
  check(root.dataset.frisch === "alt", "a failed status fetch marks the page stale", root.dataset.frisch);
  await sleep(TAKT * 1000 + 100);
  check(root.dataset.frisch === "ja", "the next successful fetch clears the mark", root.dataset.frisch);

  /* ----- the chronicle: reconciler, text only ----- */
  const fields = [1, 2, 3].map((n) => document.querySelector(".term.zitat .tz" + n));
  check(fields.every((f) => f !== null), "the chronicle has its three fields");
  const typed = () => fields.map((f) => f.textContent).join("");
  const entryFor = (now) => {
    const n = Math.max(0, Math.floor((now - (chronik.start || 0) * 1000) / (TAKT * 1000)));
    return chronik.zitate[n % chronik.zitate.length].teile.slice(0, 3).map((x) => x || "").join("");
  };
  const all = chronik.zitate.map((z) => z.teile.slice(0, 3).map((x) => x || "").join(""));
  const waitFor = async (pred, ms) => {
    for (let i = 0; i < ms / 25; i++) { await sleep(25); if (pred()) { return true; } }
    return pred();
  };
  let offTrack = null;
  const onTrack = () => { const now = typed(); if (now.length && offTrack === null && !all.some((z) => z.startsWith(now))) { offTrack = now; } };
  const first = entryFor(t0);
  check(await waitFor(() => { onTrack(); return typed() === first; }, 20000),
        "with the clock frozen the entry of the current window is typed to completion", typed().slice(0, 50));
  check(document.querySelector(".term.zitat").classList.contains("tippt") === false, "…and the cursor blinks (class 'tippt' gone) once complete");

  /* Next window: the line must go all the way down before the next entry. */
  setClock(t0 + TAKT * 1000);
  const second = entryFor(t0 + TAKT * 1000);
  check(second !== first, "the mock's next window is a different entry");
  let sawEmpty = false;
  check(await waitFor(() => { onTrack(); if (typed() === "") { sawEmpty = true; } return sawEmpty && typed() === second; }, 25000),
        "after the window changes the line is deleted to empty and the next entry typed", typed().slice(0, 50));
  check(offTrack === null, "the line is always a prefix of an entry while typing or deleting", offTrack);
  check(!/<\/|<!|<[a-z]/.test(typed()), "no markup in the typed chronicle");
  check(document.querySelector(".term.zitat .cursor") !== null, "the cursor element is present");

  console.log(failures ? `\n  === ${failures} dash.js check(s) failed ===` : "\n  === dash.js: all checks passed ===");
  window.close();
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.log("  [FEHL] dash-test crashed: " + (e && e.stack || e)); process.exit(1); });
