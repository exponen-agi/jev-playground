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
