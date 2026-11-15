import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { load, tick, SIDEBAR } from "./fixture.mjs";

// RequestList's filter select as Seerr renders it (src/components/RequestList), labels in the UI language
const FILTERS = ["all", "pending", "approved", "completed", "processing", "failed", "available", "unavailable", "deleted"];
const LABELS = { en: ["All", "Pending", "Approved", "Completed", "Processing", "Failed", "Available", "Unavailable", "Deleted"], de: ["Alle", "Ausstehend", "Genehmigt", "Abgeschlossen", "In Bearbeitung", "Fehlgeschlagen", "Verfügbar", "Nicht verfügbar", "Gelöscht"] };
const filterSelect = (value = "all", l = "en") => `<select id="filter" name="filter" class="rounded-r-only">${FILTERS.map((f, i) => `<option value="${f}"${f === value ? " selected" : ""}>${LABELS[l][i]}</option>`).join("")}</select>`;
const page = (value, l = "en") => `${l === "de" ? SIDEBAR.replace(">Series<", ">Serien<") : SIDEBAR}
<div class="mt-2 flex flex-grow flex-col sm:flex-row lg:flex-grow-0">
  <div class="mb-2 flex"><span class="inline-flex"><svg></svg></span><select id="mediaType" name="mediaType"><option value="all">All</option><option value="movie">Movies</option></select></div>
  <div class="mb-2 flex" id="wrap"><span class="inline-flex"><svg></svg></span>${filterSelect(value, l)}</div>
  <div class="mb-2 flex"><span class="inline-flex"><svg></svg></span><select id="sort" name="sort"><option value="added">Most Recent</option><option value="modified">Last Modified</option></select></div>
</div>`;

const open = [];
afterEach(() => { while (open.length) open.pop().close(); });
const load2 = (body) => { const w = load("request-filter.js", body, null, "https://requests.example.com/requests"); open.push(w); return w; };
const opts = (w) => [...w.document.getElementById("filter").options].map((o) => [o.value, o.textContent]);
const shownLabel = (w) => { const s = w.document.getElementById("filter"); return s.options[s.selectedIndex].textContent; };

test("Finished and Not finished yet come right after All, as Seerr's available and processing filters", async () => {
  const w = load2(page("all"));
  await tick();
  assert.deepEqual(opts(w).slice(0, 3), [["all", "All"], ["available", "✅ Finished"], ["processing", "⏳ Not finished yet"]]);
  assert.deepEqual(opts(w).slice(3).map((o) => o[0]), FILTERS.slice(1), "Seerr's own entries stay as they are");
  assert.equal(w.document.getElementById("filter").value, "all");
  assert.equal(w.document.getElementById("sort").options.length, 2);
  assert.equal(w.document.getElementById("mediaType").options.length, 2);
});

test("German UI: ✅ Fertig and ⏳ Noch nicht fertig", async () => {
  const w = load2(page("all", "de"));
  await tick();
  assert.deepEqual(opts(w).slice(1, 3), [["available", "✅ Fertig"], ["processing", "⏳ Noch nicht fertig"]]);
});

test("the current filter processing / available shows our entry as the selected one", async () => {
  let w = load2(page("processing"));
  await tick();
  assert.equal(shownLabel(w), "⏳ Not finished yet");
  assert.equal(w.document.getElementById("filter").value, "processing");
  w = load2(page("available"));
  await tick();
  assert.equal(shownLabel(w), "✅ Finished");
});

test("picking Finished hands Seerr's own change handler the value available", async () => {
  const w = load2(page("all"));
  await tick();
  const s = w.document.getElementById("filter");
  let got = null;
  s.addEventListener("change", (e) => { got = e.target.value; });
  s.selectedIndex = 1;
  s.dispatchEvent(new w.Event("change", { bubbles: true }));
  assert.equal(got, "available");
});

test("React re-render (new select, relabelled options, language switch): entries come back, never twice", async () => {
  const w = load2(page("all"));
  await tick();
  const wrap = w.document.getElementById("wrap");
  wrap.querySelector("select").remove();
  wrap.insertAdjacentHTML("beforeend", filterSelect("processing", "de"));
  w.document.querySelector('a[href="/discover/tv"]').lastChild.textContent = "Serien";
  await tick();
  assert.deepEqual(opts(w).slice(0, 3), [["all", "Alle"], ["available", "✅ Fertig"], ["processing", "⏳ Noch nicht fertig"]]);
  assert.equal(shownLabel(w), "⏳ Noch nicht fertig");
  w.document.getElementById("filter").options[0].textContent = "Alle ";
  await tick();
  await tick();
  assert.equal(opts(w).filter((o) => o[1].includes("Fertig")).length, 1);
  assert.equal(opts(w).length, FILTERS.length + 2);
});

test("a select without Seerr's processing / available filters is left alone", async () => {
  const w = load2('<select id="filter"><option value="all">All</option><option value="open">Open</option></select>');
  await tick();
  assert.equal(w.document.getElementById("filter").options.length, 2);
});
