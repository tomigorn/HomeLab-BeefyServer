import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const WWW = new URL("../../indexer-bar/www/", import.meta.url);

// Markup as Seerr renders it (src/components/Layout/Sidebar, MobileMenu, Common/Modal), reduced to what the scripts look at.
export const SIDEBAR = `
<div class="fixed bottom-0 left-0 top-0 z-30 hidden lg:flex lg:flex-shrink-0"><div class="sidebar flex w-64 flex-col">
<nav class="mt-8 flex-1 space-y-4 px-4">
  <a href="/" class="group flex items-center rounded-md px-2 py-2 text-lg font-medium leading-6 text-white transition duration-150 ease-in-out focus:outline-none hover:bg-gray-700 focus:bg-gray-700"><svg class="mr-3 h-6 w-6"></svg>Discover</a>
  <a href="/discover/movies" class="group flex items-center rounded-md px-2 py-2 text-lg font-medium leading-6 text-white transition duration-150 ease-in-out focus:outline-none bg-gradient-to-br from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500"><svg class="mr-3 h-6 w-6"></svg>Movies</a>
  <a href="/discover/tv" class="group flex items-center rounded-md px-2 py-2 text-lg font-medium leading-6 text-white transition duration-150 ease-in-out focus:outline-none hover:bg-gray-700 focus:bg-gray-700"><svg class="mr-3 h-6 w-6"></svg>Series</a>
  <a href="/requests" class="group flex items-center rounded-md px-2 py-2 text-lg"><svg class="mr-3 h-6 w-6"></svg>Requests<div class="ml-auto flex"><span>3</span></div></a>
</nav></div></div>`;

export const MOBILE = `
<div class="fixed bottom-0 left-0 right-0 z-50">
  <div class="absolute left-0 right-0 top-0 flex w-full -translate-y-full flex-col space-y-6 border-t">
    <a href="/" class="flex items-center " role="button" tabindex="0"><svg class="h-5 w-5"></svg><span class="ml-2">Discover</span></a>
    <a href="/discover/movies" class="flex items-center " role="button" tabindex="0"><svg class="h-5 w-5"></svg><span class="ml-2">Movies</span></a>
    <a href="/discover/tv" class="flex items-center text-indigo-500" role="button" tabindex="0"><svg class="h-5 w-5"></svg><span class="ml-2">Series</span></a>
    <a href="/requests" class="flex items-center " role="button" tabindex="0"><svg class="h-5 w-5"></svg><span class="ml-2">Requests</span></a>
  </div>
  <div class="padding-bottom-safe border-t"><div class="flex h-full items-center justify-between px-6 py-4 text-gray-100">
    <a href="/" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a>
    <a href="/discover/tv" class="relative flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg></a>
    <button class="flex flex-col items-center space-y-1 "><svg class="h-6 w-6"></svg><span class="text-sm">More</span></button>
  </div></div>
</div>`;

export function modal(title, ok = "Request") {
  return `<div class="fixed z-50"><div role="dialog" aria-modal="true" class="hide-scrollbar relative inline-block">
  <div class="relative -mx-4"><div class="mt-3 truncate"><div class="flex flex-col space-y-1">
    <span class="text-overseerr truncate" id="modal-headline" data-testid="modal-title">${title}</span>
    <span class="truncate text-lg" id="modal-headline" data-testid="modal-title">Some Film</span>
  </div></div></div>
  <div class="relative mt-4 text-sm"><p>body</p></div>
  <div class="relative mt-5 flex flex-row-reverse justify-center">
    <button data-testid="modal-ok-button">${ok}</button><button data-testid="modal-cancel-button">Cancel</button>
  </div></div></div>`;
}

// A DOM with the given body; `before` runs in the window before the script (stubs), then the script itself.
export function load(script, body, before) {
  const dom = new JSDOM(`<!doctype html><html><head></head><body>${body}</body></html>`, { runScripts: "outside-only", url: "https://requests.example.com/movie/1" });
  if (before) before(dom.window);
  dom.window.eval(readFileSync(new URL(script, WWW), "utf8"));
  return dom.window;
}

export const tick = () => new Promise((r) => setTimeout(r, 0));
