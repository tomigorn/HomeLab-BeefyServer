import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const WWW = new URL("../../indexer-bar/www/", import.meta.url);
// bar.js (shared with AudioBookRequest) is converted on its own
const LATER = { "bar.js": "bar.js still uses plain title tooltips" };

// Plain browser tooltips (delay, other look, nothing on phones): every hint goes through tip.js. document.title is fine.
function titleSets(src) {
  const found = [];
  for (const m of src.matchAll(/setAttribute\(\s*["']title["']/g)) found.push(m[0]);
  for (const m of src.matchAll(/([\w$]+)\.title\s*=(?!=)/g)) if (!["doc", "document"].includes(m[1])) found.push(m[0]);
  for (const m of src.matchAll(/["']title["']\s*:/g)) found.push(m[0]);
  return found;
}

for (const file of readdirSync(WWW).filter((f) => f.endsWith(".js")).sort()) {
  test(`${file}: no title tooltips`, { todo: LATER[file] }, () => {
    assert.deepEqual(titleSets(readFileSync(new URL(file, WWW), "utf8")), []);
  });
}

test("the scan finds the ways a title gets set", () => {
  assert.equal(titleSets('a.title = "x"; b.setAttribute("title", y); el(doc, "li", { "title": why });').length, 3);
  assert.deepEqual(titleSets('doc.title = t; document.title = t; if (x.title === y) {}'), []);
});
