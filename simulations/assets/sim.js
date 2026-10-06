/* Small shared helpers for the simulations. No framework, just plumbing. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const fmt = (n, d = 2) => Number(n).toFixed(d);
export const money = (n) => (n < 1 ? "$" + n.toFixed(3) : "$" + n.toLocaleString(undefined, { maximumFractionDigits: 0 }));

/** Deterministic PRNG so every visitor sees the same run. */
export function rng(seed = 42) {
  let s = seed >>> 0;
  return () => (((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296));
}

/** A plausible probability that leans toward `target`, for demo purposes only. */
export function around(r, target, spread = 0.12) {
  return clamp(target + (r() - 0.5) * 2 * spread, 0.01, 0.99);
}

/** Play / pause / step loop driven by requestAnimationFrame. */
export class Ticker {
  constructor(onTick, intervalMs = 900) {
    this.onTick = onTick; this.interval = intervalMs;
    this.speed = 1; this.playing = false; this._acc = 0; this._last = 0;
    this._frame = this._frame.bind(this);
  }
  _frame(t) {
    if (!this.playing) return;
    if (!this._last) this._last = t;
    this._acc += t - this._last; this._last = t;
    while (this._acc >= this.interval / this.speed) {
      this._acc -= this.interval / this.speed;
      this.onTick();
    }
    requestAnimationFrame(this._frame);
  }
  play() { if (this.playing) return; this.playing = true; this._last = 0; requestAnimationFrame(this._frame); }
  pause() { this.playing = false; this._last = 0; }
  toggle() { this.playing ? this.pause() : this.play(); }
  step() { this.pause(); this.onTick(); }
  reset() { this.pause(); this._acc = 0; }
}

/** Wires up a standard play / step / reset / speed control bar. */
export function wireControls({ ticker, playBtn, stepBtn, resetBtn, speedInput, onReset }) {
  const paint = () => { playBtn.textContent = ticker.playing ? "❚❚  Pause" : "▶  Play"; };
  playBtn.addEventListener("click", () => { ticker.toggle(); paint(); });
  stepBtn?.addEventListener("click", () => { ticker.step(); paint(); });
  resetBtn?.addEventListener("click", () => { ticker.reset(); onReset?.(); paint(); });
  speedInput?.addEventListener("input", () => { ticker.speed = Number(speedInput.value); });
  paint();
  return paint;
}

/** Reads a CSS custom property, so SVG colours follow the light/dark theme. */
export const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Sets .v text on a [data-stat] tile. */
export function setStat(key, value, note) {
  const tile = document.querySelector(`[data-stat="${key}"]`);
  if (!tile) return;
  tile.querySelector(".v").textContent = value;
  if (note !== undefined) { const n = tile.querySelector(".n"); if (n) n.textContent = note; }
}

/** Respect reduced-motion for the decorative transitions. */
export const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
export const dur = (ms) => (reduced ? 0 : ms);

/* ---- agentic pages: code panel and travelling dots ---- */

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function highlightPy(s) {
  const re = /(#.*$)|("[^"]*"|'[^']*')|(\b\d+(?:\.\d+)?\b)|(\b(?:def|return|if|elif|else|for|in|and|or|not|while|break|continue|None|True|False|with|as)\b)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(s))) {
    const cls = m[1] ? "tc" : m[2] ? "ts" : m[3] ? "tn" : "tk";
    out += esc(s.slice(last, m.index)) + `<span class="${cls}">${esc(m[0])}</span>`;
    last = re.lastIndex;
  }
  return out + esc(s.slice(last));
}

/**
 * Renders Python into a <pre class="code">. A line ending in `#@tag` (or `#@a,b`) gets
 * those tags, which markCode() lights up when the simulation takes that branch. The
 * tag itself is stripped from what the reader sees.
 */
export function renderCode(el, src) {
  const lines = src.replace(/^\s*\n/, "").replace(/\s+$/, "").split("\n");
  const indent = Math.min(...lines.filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length));
  el.innerHTML = lines.map((raw) => {
    const m = raw.match(/\s*#@([\w,-]+)\s*$/);
    const line = (m ? raw.slice(0, m.index) : raw).slice(indent);
    return `<span class="ln" data-tags="${m ? m[1] : ""}">${highlightPy(line) || " "}</span>`;
  }).join("");
}

/** Highlights every line carrying any of `tags`; call with no tags to clear. */
export function markCode(el, ...tags) {
  el.querySelectorAll(".ln").forEach((ln) => {
    const own = ln.dataset.tags.split(",");
    ln.classList.toggle("on", tags.some((t) => own.includes(t)));
  });
}

/** Sends a dot along an SVG path. Decorative only: state never waits on it. */
export function travel(svg, path, colour, { ms = 700, delay = 0, r = 6.5 } = {}) {
  if (!path) return;
  const len = path.getTotalLength();
  const at = (u) => { const p = path.getPointAtLength(u * len); return `translate(${p.x},${p.y})`; };
  const dot = svg.append("circle").attr("r", r).attr("fill", colour).attr("opacity", 0).attr("transform", at(0));
  dot.transition().delay(dur(delay)).duration(0).attr("opacity", 1)
    .transition().duration(dur(ms)).ease(d3.easeCubicInOut).attrTween("transform", () => at)
    .transition().duration(dur(170)).attr("r", r * 1.8).attr("opacity", 0).remove();
}
