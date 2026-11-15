import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { storeFor, missingFormats, promptOnStart, isPlaybackInfo, gatePlayback, layout, texts, APPLE, GOOGLE, FORMATS } = createRequire(import.meta.url)("./app-banner.js");

const UA = {
  iphoneSafari: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1",
  iphoneChrome: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/142.0.0.0 Mobile/15E148 Safari/604.1",
  iphoneFirefox: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/145.0 Mobile/15E148 Safari/605.1.15",
  ipad: "Mozilla/5.0 (iPad; CPU OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1",
  mac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
  androidChrome: "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36",
  androidFirefox: "Mozilla/5.0 (Android 15; Mobile; rv:145.0) Gecko/145.0 Firefox/145.0",
  androidOpera: "Mozilla/5.0 (Linux; Android 15; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36 OPR/92.0.0.0",
  androidTablet: "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
  windows: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
  tizen: "Mozilla/5.0 (SMART-TV; LINUX; Tizen 7.0) AppleWebKit/537.36 (KHTML, like Gecko) 94.0.4606.31/7.0 TV Safari/537.36",
  webos: "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.5359.211 Safari/537.36 WebAppManager",
  fireTv: "Mozilla/5.0 (Linux; Android 11; AFTKRT) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
  googleTv: "Mozilla/5.0 (Linux; Android 12; Chromecast HD) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 GoogleTV",
};

test("every iPhone/iPad browser gets the App Store", () => {
  for (const ua of [UA.iphoneSafari, UA.iphoneChrome, UA.iphoneFirefox, UA.ipad]) assert.equal(storeFor(ua, 5, false), APPLE);
  assert.equal(storeFor(UA.mac, 5, false), APPLE, "iPad in desktop mode");
});

test("every Android phone/tablet browser gets Google Play", () => {
  for (const ua of [UA.androidChrome, UA.androidFirefox, UA.androidOpera, UA.androidTablet]) assert.equal(storeFor(ua, 5, false), GOOGLE);
});

test("desktops, TVs and Jellyfin's own apps get nothing", () => {
  assert.equal(storeFor(UA.mac, 0, false), null);
  assert.equal(storeFor(UA.windows, 0, false), null);
  for (const ua of [UA.tizen, UA.webos, UA.fireTv, UA.googleTv]) assert.equal(storeFor(ua, 0, false), null);
  assert.equal(storeFor(UA.iphoneSafari, 5, true), null);
  assert.equal(storeFor(UA.androidChrome, 5, true), null);
});

test("store links open the Moonfin page", () => {
  assert.equal(APPLE.url, "https://apps.apple.com/app/id6761283970");
  assert.equal(GOOGLE.url, "https://play.google.com/store/apps/details?id=org.moonfin.androidtv");
});

test("missing formats: what the browser cannot play of our library", () => {
  assert.deepEqual(missingFormats(() => true), []);
  assert.deepEqual(missingFormats(() => false), FORMATS.map((f) => f.name));
  assert.deepEqual(missingFormats((type) => !type.includes("matroska")), ["MKV"]);
});

test("prompt on every start: phone browser missing a format", () => {
  assert.equal(promptOnStart(APPLE, ["MKV"]), true);
  assert.equal(promptOnStart(GOOGLE, ["Dolby Digital Plus"]), true);
  assert.equal(promptOnStart(GOOGLE, []), false, "browser plays everything");
  assert.equal(promptOnStart(null, ["MKV"]), false, "not a phone");
});

test("play is caught at the PlaybackInfo request", () => {
  assert.equal(isPlaybackInfo("https://jellyfin.holy-grail.ch/Items/abc123/PlaybackInfo?UserId=u", "POST"), true);
  assert.equal(isPlaybackInfo("/Items/abc123/PlaybackInfo", "post"), true);
  assert.equal(isPlaybackInfo("/Items/abc123/PlaybackInfo", "GET"), false);
  assert.equal(isPlaybackInfo("/Items/abc123", "POST"), false);
  assert.equal(isPlaybackInfo("/Sessions/Playing", "POST"), false);
});

test("German for German browsers, English otherwise; start and play wording differ", () => {
  const de = texts("de-CH", "start"), en = texts("en-GB", "start");
  assert.notEqual(de.title, en.title);
  assert.deepEqual(texts(undefined, "start"), en);
  assert.match(en.yes, /Moonfin/);
  assert.match(en.no, /browsing/i);
  assert.match(texts("en", "play").no, /browser/i);
  assert.notEqual(texts("en", "play").no, en.no);
});

function fakeWindow() {
  const sent = [];
  class XHR {
    open(method, url) { this.method = method; this.url = url; }
    send(body) { sent.push(["xhr", this.method, this.url]); }
    abort() { sent.push(["abort", this.method, this.url]); }
  }
  const win = { XMLHttpRequest: XHR, fetch: (url, init) => { sent.push(["fetch", (init && init.method) || "GET", url]); return Promise.resolve("ok"); } };
  return { win, sent };
}
const PI = "/Items/abc/PlaybackInfo?UserId=u";
const tick = () => new Promise((r) => setTimeout(r, 0));

test("XHR play request waits for the answer, then goes out on 'browser'", async () => {
  const { win, sent } = fakeWindow();
  let answer;
  gatePlayback(win, () => new Promise((r) => (answer = r)));
  const x = new win.XMLHttpRequest();
  x.open("POST", PI);
  x.send("{}");
  assert.deepEqual(sent, []);
  answer("browser");
  await tick();
  assert.deepEqual(sent, [["xhr", "POST", PI]]);
});

test("XHR play request is cancelled on 'app'", async () => {
  const { win, sent } = fakeWindow();
  gatePlayback(win, () => Promise.resolve("app"));
  const x = new win.XMLHttpRequest();
  x.open("POST", PI);
  x.send("{}");
  await tick();
  assert.deepEqual(sent, [["abort", "POST", PI]]);
});

test("every play asks again, nothing is remembered", async () => {
  const { win, sent } = fakeWindow();
  let asked = 0;
  gatePlayback(win, () => { asked++; return Promise.resolve("browser"); });
  for (let i = 0; i < 2; i++) { const x = new win.XMLHttpRequest(); x.open("POST", PI); x.send("{}"); await tick(); }
  await win.fetch(PI, { method: "POST" });
  assert.equal(asked, 3);
  assert.equal(sent.length, 3);
});

test("start shows both answers; play hides 'browser anyway' behind More options", () => {
  assert.deepEqual(layout("start"), { back: false, shown: ["yes", "no"], more: [] });
  assert.deepEqual(layout("play"), { back: true, shown: ["yes"], more: ["no"] });
  assert.match(texts("en", "play").back, /back/i);
  assert.match(texts("de", "play").back, /zurück/i);
  assert.match(texts("en", "play").more, /more options/i);
  assert.match(texts("de", "play").more, /weitere/i);
});

test("fetch play request is gated too; other requests are untouched", async () => {
  const { win, sent } = fakeWindow();
  let asked = 0;
  gatePlayback(win, () => { asked++; return Promise.resolve("app"); });
  await win.fetch("/Users/Me");
  const x = new win.XMLHttpRequest(); x.open("GET", "/Items/abc"); x.send();
  await assert.rejects(win.fetch(PI, { method: "POST" }));
  assert.equal(asked, 1);
  assert.deepEqual(sent, [["fetch", "GET", "/Users/Me"], ["xhr", "GET", "/Items/abc"]]);
});

test("'back' cancels the play without opening anything", async () => {
  const { win, sent } = fakeWindow();
  gatePlayback(win, () => Promise.resolve("back"));
  const x = new win.XMLHttpRequest();
  x.open("POST", PI);
  x.send("{}");
  await tick();
  assert.deepEqual(sent, [["abort", "POST", PI]]);
  await assert.rejects(win.fetch(PI, { method: "POST" }));
});
