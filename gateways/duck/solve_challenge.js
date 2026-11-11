/*
 * solve_challenge.js — duck.ai x-vqd-hash-1 challenge WITHOUT a browser.
 *
 * Node's `vm` gives the script a real global object (so `(function(){return
 * this;}()) === window` and `Object.prototype.toString.call(window)` behave
 * like a browser). window/document/elements are calibrated mocks that answer
 * duck.ai's rotating DOM probes the way a genuine Chrome page does.
 *
 * Usage: node solve_challenge.js <decoded-js-file> [userAgent]
 * Output: {ok, res, userAgent}
 */
'use strict';
const fs = require('fs');
const vm = require('vm');

const ua = process.argv[3] ||
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';
const src = fs.readFileSync(process.argv[2], 'utf8');

function unescape(s) {
  return s.replace(/\\x20/g, ' ').replace(/\\x27/g, "'").replace(/\\x22/g, '"');
}
function findString(re, fallback) {
  const m = src.match(re);
  return m ? unescape(m[1]) : fallback;
}
const CSP = findString(/'([^']*default-src[^']*)'/, "default-src 'none'; script-src 'unsafe-inline';");
const SANDBOX = findString(/'([^']*allow-scripts[^']*)'/, 'allow-scripts allow-same-origin');

// Chrome's HTML parser normalizes truncated innerHTML strings (measured on a
// real Chrome page). Unknown strings fall back to a closing-tag heuristic.
const NORM = {
  '<div><div></div><div></div': {html: '<div><div></div><div></div></div>', count: 3},
  '<li><div></li><li></div': {html: '<li><div></div></li><li></li>', count: 3},
  '<p><div></p><p></div': {html: '<p></p><div><p></p><p></p></div>', count: 4},
  '<br><div></br><br></div': {html: '<br><div><br><br></div>', count: 4},
};
function normalizeHtml(s) {
  s = unescape(String(s || ''));
  if (NORM[s]) return NORM[s];
  const opens = (s.match(/<([A-Za-z][A-Za-z0-9]*)[^>]*>/g) || []).length;
  const closes = (s.match(/<\/[A-Za-z][A-Za-z0-9]*>/g) || []).length;
  let html = s.replace(/<\/?[A-Za-z][^>]*$/, '');
  for (let i = 0; i < Math.max(0, opens - closes); i++) html += '</div>';
  return {html, count: opens};
}

// --- browser-like constructors ------------------------------------------
function HTMLElement() {}
function HTMLDivElement() {}
function HTMLIFrameElement() {}
function Element() {}
function Node() {}
function NodeList(n) { this.length = n || 0; }
function HTMLCollection(n) { this.length = n || 0; }
function Window() {}
function MutationObserver() {}
function DOMParser() {}
function Event() {}
function CustomEvent() {}
for (const C of [NodeList, HTMLCollection]) {
  C.prototype.map = C.prototype.filter = C.prototype.forEach = function () { return []; };
  C.prototype[Symbol.iterator] = function* () {};
}
// Build the chain bottom-up so each `instanceof` sees the final prototype.
Element.prototype = Object.create(Node.prototype);
HTMLElement.prototype = Object.create(Element.prototype);
HTMLDivElement.prototype = Object.create(HTMLElement.prototype);
HTMLIFrameElement.prototype = Object.create(HTMLElement.prototype);

// --- universal fallback for unknown property reads -----------------------
const UNIV = new Proxy(function () {}, {
  get(t, p) {
    if (p === Symbol.toPrimitive) return (hint) => hint === 'number' ? 1 : '';
    if (p === 'toString') return () => '';
    if (p === 'valueOf') return () => 1;
    if (p === Symbol.iterator) return function* () {};
    if (p === 'length') return 0;
    if (p === 'name') return 'universal';
    if (p === 'prototype') return UNIV;
    return UNIV;
  },
  apply() { return UNIV; },
  construct() { return UNIV; },
  set() { return true; },
  has() { return false; },
});
function withFallback(target) {
  return new Proxy(target, {
    get(t, p) { return p in t ? t[p] : UNIV; },
    set(t, p, v) { t[p] = v; return true; },
    has(t, p) { return p in t; },
  });
}

const cwArray = {from() { return []; }};
const cwDocument = withFallback({
  Array: cwArray, querySelector() { return null; },
  querySelectorAll() { return new NodeList(0); },
  getElementById() { return null; },
  hasOwnProperty(k) { return Object.prototype.hasOwnProperty.call(this, k); },
});
const contentWindow = withFallback({
  document: cwDocument, self: null, Array: cwArray, Object, JSON, Promise,
  top: null, parent: null, window: null, frames: null,
  length: 0, name: '', closed: false, innerWidth: 1080, innerHeight: 2400,
  location: withFallback({href: 'about:srcdoc', origin: 'null'}),
});
contentWindow.self = contentWindow;
contentWindow.top = contentWindow;
contentWindow.window = contentWindow;
contentWindow.parent = contentWindow;
contentWindow.frames = contentWindow;

function mkElement(attrs) {
  let rawHtml = '';
  const el = {
    style: {cssText: '', getPropertyValue() { return 'block'; },
            setProperty() {}, removeProperty() {}},
    contentDocument: cwDocument, contentWindow: contentWindow,
    offsetWidth: 1, offsetHeight: 1, clientWidth: 1, clientHeight: 1,
    width: 1, height: 1, scrollWidth: 1, scrollHeight: 1,
    nodeType: 1, tagName: 'DIV', textContent: '', outerHTML: '',
    id: '', className: '', children: [], childNodes: [], attributes: [],
    dataset: {}, classList: {add() {}, remove() {}, contains() { return true; }},
    getBoundingClientRect() {
      return {width: 1, height: 1, top: 0, left: 0, right: 1, bottom: 1};
    },
    getAttribute(name) { return (attrs && attrs[name]) || ''; },
    setAttribute() {}, removeAttribute() {}, hasAttribute() { return true; },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    appendChild() {}, removeChild() {}, insertBefore() {}, cloneNode() { return this; },
    contains() { return true; }, matches() { return true; }, closest() { return this; },
    focus() {}, click() {}, toString() { return '[object HTMLDivElement]'; },
    querySelector() { return null; },
    querySelectorAll() { return new NodeList(normalizeHtml(rawHtml).count); },
    getElementsByTagName() { return this.querySelectorAll('*'); },
    hasOwnProperty(k) { return Object.prototype.hasOwnProperty.call(this, k); },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return normalizeHtml(rawHtml).html; },
    set(v) { rawHtml = String(v); },
    enumerable: true, configurable: true,
  });
  Object.setPrototypeOf(el, HTMLDivElement.prototype);
  return withFallback(el);
}

const cspMeta = mkElement({'content': CSP});
const jsaDoc = withFallback({querySelector() { return cspMeta; }});
const jsaElement = mkElement({'sandbox': SANDBOX});
jsaElement.contentDocument = jsaDoc;

const bodyChildren = [];
const body = withFallback({
  children: bodyChildren, childNodes: bodyChildren,
  appendChild(el) { bodyChildren.push(el); return el; },
  removeChild(el) {
    const i = bodyChildren.indexOf(el);
    if (i >= 0) bodyChildren.splice(i, 1);
    return el;
  },
  querySelector() { return null; },
  querySelectorAll() { return new NodeList(0); },
  hasOwnProperty(k) { return Object.prototype.hasOwnProperty.call(this, k); },
});

const document = withFallback({
  createElement() { return mkElement({}); },
  querySelector(sel) { return String(sel).indexOf('#jsa') >= 0 ? jsaElement : null; },
  querySelectorAll(sel) {
    return new NodeList(String(sel).indexOf('#jsa') >= 0 ? 1 : 0);
  },
  getElementById() { return null; },
  getElementsByTagName() { return new NodeList(0); },
  body, documentElement: mkElement({}), head: mkElement({}),
  hasOwnProperty(k) { return Object.prototype.hasOwnProperty.call(this, k); },
});

const navigator = withFallback({
  userAgent: ua, webdriver: false, platform: 'Linux armv8l',
  language: 'en-US', languages: ['en-US', 'en'], vendor: 'Google Inc.',
  hardwareConcurrency: 8, deviceMemory: 8, maxTouchPoints: 5,
  plugins: [], mimeTypes: [],
  userAgentData: withFallback({brands: [], mobile: true, platform: 'Android',
                               getHighEntropyValues() { return Promise.resolve({}); }}),
});

const getComputedStyle = () => ({getPropertyValue() { return 'block'; }});

// The vm sandbox IS the global object / window.
const sandbox = {
  navigator, document, getComputedStyle,
  Window, NodeList, HTMLCollection, HTMLElement, HTMLDivElement,
  HTMLIFrameElement, Element, Node, MutationObserver, DOMParser, Event, CustomEvent,
  __DDG_BE_VERSION__: 'serp_20261105_082022_ET',
  __DDG_FE_CHAT_HASH__: '1',
  innerWidth: 1080, innerHeight: 2400, outerWidth: 1080, outerHeight: 2400,
  devicePixelRatio: 3, screenX: 0, screenY: 0, length: 0, name: '', closed: false,
  location: withFallback({origin: 'https://duck.ai', href: 'https://duck.ai/',
                          hostname: 'duck.ai', protocol: 'https:'}),
  screen: withFallback({width: 1080, height: 2400, availWidth: 1080, availHeight: 2400}),
  history: withFallback({length: 1, pushState() {}, replaceState() {}}),
  localStorage: withFallback({getItem() { return null; }, setItem() {}, removeItem() {}}),
  sessionStorage: withFallback({getItem() { return null; }, setItem() {}, removeItem() {}}),
  performance: withFallback({now() { return 1; }}),
  crypto: withFallback({getRandomValues(a) { return a; }}),
  matchMedia() { return {matches: false, addListener() {}}; },
  requestAnimationFrame() { return 1; },
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
  fetch() { return Promise.reject(new Error('no network in solver')); },
  atob, btoa, TextEncoder, TextDecoder, setTimeout, clearTimeout,
  setInterval, clearInterval, console,
};
Object.defineProperty(sandbox, Symbol.toStringTag, {value: 'Window'});
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.top = sandbox;
sandbox.parent = sandbox;
sandbox.frames = sandbox;
sandbox.globalThis = sandbox;

(async () => {
  try {
    const ctx = vm.createContext(sandbox);
    if (process.env.DUCK_DEBUG) {
      vm.runInContext(
        "Array.prototype.map=(function(o){return function(f){" +
        "if(f===Number){(globalThis.__dbgs=globalThis.__dbgs||[]).push(Array.from(this));}" +
        "return o.apply(this,arguments);};})(Array.prototype.map);", ctx);
    }
    const res = await vm.runInContext(src, ctx);
    process.stdout.write(JSON.stringify({ok: true, res, userAgent: ua,
                                         dbg: sandbox.__dbg || null,
                                         dbgs: sandbox.__dbgs || null}));
  } catch (e) {
    process.stdout.write(JSON.stringify({ok: false, error: String(e && e.stack || e)}));
  }
})();
