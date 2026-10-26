import { test } from "node:test";
import assert from "node:assert/strict";
import { load, modal, tick } from "./fixture.mjs";

// Records what the page sends; GETs to the service API answer from `routes`.
function fakeXHR(routes, sent) {
  return function (w) {
    w.XMLHttpRequest = class {
      open(method, url) { this.method = method; this.url = url; }
      send(body) {
        if (this.method === "GET") {
          const r = routes[this.url];
          this.status = r ? 200 : 404;
          this.responseText = JSON.stringify(r ?? {});
          setTimeout(() => this.onload && this.onload(), 0);
          return;
        }
        sent.push({ method: this.method, url: this.url, body });
      }
    };
  };
}
const ROUTES = {
  "/api/v1/service/radarr": [{ id: 0, isDefault: true, is4k: false, activeTags: [] }, { id: 1, isDefault: true, is4k: true, activeTags: [5] }],
  "/api/v1/service/radarr/0": { tags: [{ id: 13, label: "audio-german" }, { id: 64, label: "download-now" }] },
  "/api/v1/service/radarr/1": { tags: [{ id: 99, label: "download-now" }] },
  "/api/v1/service/sonarr": [{ id: 0, isDefault: true, is4k: false, activeTags: [] }],
  "/api/v1/service/sonarr/0": { tags: [{ id: 61, label: "download-now" }] },
};
const settle = async () => { for (let i = 0; i < 10; i++) await tick(); };
function post(w, url, body) {
  const x = new w.XMLHttpRequest();
  x.open("POST", url);
  x.send(JSON.stringify(body));
}

test("request dialog gets the box, ticked by default, above the buttons", async () => {
  const w = load("can-wait.js", modal("Request Movie"));
  const box = w.document.querySelector("#hg-can-wait");
  assert.ok(box);
  assert.equal(box.querySelector("input").checked, true);
  assert.match(box.textContent, /Can wait — download tonight \(cheaper power\)/);
  assert.equal(box.nextElementSibling.querySelector('[data-testid="modal-ok-button"]').textContent, "Request");
});

test("series, collection, 4K and German titles count; other dialogs do not", async () => {
  for (const t of ["Request Series", "Request Collection in 4K", "Film anfragen", "Serie in 4K anfragen"]) {
    assert.ok(load("can-wait.js", modal(t)).document.querySelector("#hg-can-wait"), t);
  }
  for (const t of ["Edit Request", "Pending Request", "Delete User", "Report an Issue"]) {
    assert.equal(load("can-wait.js", modal(t)).document.querySelector("#hg-can-wait"), null, t);
  }
});

test("a dialog that opens later gets the box once (MutationObserver)", async () => {
  const w = load("can-wait.js", "<main></main>");
  w.document.body.insertAdjacentHTML("beforeend", modal("Request Movie"));
  await settle();
  w.document.body.insertAdjacentHTML("beforeend", "<p>re-render</p>");
  await settle();
  assert.equal(w.document.querySelectorAll("#hg-can-wait").length, 1);
});

test("ticked: the request goes out unchanged", async () => {
  const sent = [];
  const w = load("can-wait.js", modal("Request Movie"), fakeXHR(ROUTES, sent));
  post(w, "/api/v1/request", { mediaId: 1, mediaType: "movie", is4k: false });
  await settle();
  assert.deepEqual(JSON.parse(sent[0].body), { mediaId: 1, mediaType: "movie", is4k: false });
});

test("unticked: download-now of the default Radarr server is added", async () => {
  const sent = [];
  const w = load("can-wait.js", modal("Request Movie"), fakeXHR(ROUTES, sent));
  w.document.querySelector("#hg-can-wait input").checked = false;
  post(w, "/api/v1/request", { mediaId: 1, mediaType: "movie", is4k: false });
  await settle();
  assert.deepEqual(JSON.parse(sent[0].body).tags, [64]);
});

test("unticked: tags picked under Advanced are kept, server and 4K are respected", async () => {
  const sent = [];
  const w = load("can-wait.js", modal("Request Movie in 4K"), fakeXHR(ROUTES, sent));
  w.document.querySelector("#hg-can-wait input").checked = false;
  post(w, "/api/v1/request", { mediaId: 1, mediaType: "movie", is4k: true, tags: [13] });
  post(w, "/api/v1/request", { mediaId: 2, mediaType: "movie", is4k: true });
  await settle();
  assert.deepEqual(JSON.parse(sent[0].body).tags, [13, 99]);
  assert.deepEqual(JSON.parse(sent[1].body).tags, [5, 99], "server default tags stay when the body had none");
});

test("unticked series request: Sonarr's download-now", async () => {
  const sent = [];
  const w = load("can-wait.js", modal("Request Series"), fakeXHR(ROUTES, sent));
  w.document.querySelector("#hg-can-wait input").checked = false;
  post(w, "/api/v1/request", { mediaId: 3, mediaType: "tv", tvdbId: 7, is4k: false, seasons: [1] });
  await settle();
  assert.deepEqual(JSON.parse(sent[0].body).tags, [61]);
});

test("tag missing or service API failing: the request goes out unchanged", async () => {
  const sent = [];
  const w = load("can-wait.js", modal("Request Series"), fakeXHR({ "/api/v1/service/sonarr": ROUTES["/api/v1/service/sonarr"] }, sent));
  w.document.querySelector("#hg-can-wait input").checked = false;
  post(w, "/api/v1/request", { mediaId: 3, mediaType: "tv", is4k: false });
  await settle();
  assert.equal(sent.length, 1);
  assert.equal(JSON.parse(sent[0].body).tags, undefined);
});

test("no box on the page (Seerr changed its dialog): nothing is added", async () => {
  const sent = [];
  const w = load("can-wait.js", modal("Something new"), fakeXHR(ROUTES, sent));
  post(w, "/api/v1/request", { mediaId: 1, mediaType: "movie" });
  await settle();
  assert.equal(JSON.parse(sent[0].body).tags, undefined);
});

test("other requests are never touched", async () => {
  const sent = [];
  const w = load("can-wait.js", modal("Request Movie"), fakeXHR(ROUTES, sent));
  w.document.querySelector("#hg-can-wait input").checked = false;
  post(w, "/api/v1/request/5/approve", {});
  post(w, "/api/v1/user/1/settings", { a: 1 });
  const x = new w.XMLHttpRequest(); x.open("PUT", "/api/v1/request/5"); x.send(JSON.stringify({ mediaType: "movie" }));
  await settle();
  assert.deepEqual(sent.map((s) => s.body), ["{}", '{"a":1}', '{"mediaType":"movie"}']);
});
