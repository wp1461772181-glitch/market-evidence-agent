// FinBot orb avatars.  A bot's avatar string is either `orb:<colour>` (the round face with white `$` eyes in that
// colour), `orb:finbot` (the same face in black: the brand mark and every new bot's default), or any older emoji/text (shown as is).
export const ORB_COLORS = ["black", "brown", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "gray"];
export const BRAND = "finbot";

/** {kind:"orb", color, brand} | {kind:"text", text} for a bot's avatar.  The default bot's old ★ is the brand orb. */
export function parseAvatar(avatar, name = "") {
  const value = String(avatar ?? "").trim();
  if (value === "★" || value === `orb:${BRAND}`) return { kind: "orb", color: BRAND, brand: true };
  const match = value.match(/^orb:([a-z]+)$/);
  if (match && ORB_COLORS.includes(match[1])) return { kind: "orb", color: match[1], brand: false };
  if (value) return { kind: "text", text: value };
  return { kind: "text", text: String(name).slice(0, 1) || "·" };
}

export function orbAvatar(color) {
  return color === BRAND || ORB_COLORS.includes(color) ? `orb:${color}` : `orb:${BRAND}`;
}

/** Pupil offset (px, in the orb's own units) toward the pointer: angle kept, length min(distance/k, max). */
export function gazeOffset(center, pointer, { max = 3, k = 60 } = {}) {
  if (!pointer) return { x: 0, y: 0 };
  const dx = pointer.x - center.x, dy = pointer.y - center.y;
  const distance = Math.hypot(dx, dy);
  if (distance < 0.5) return { x: 0, y: 0 };
  const length = Math.min(distance / k, max);
  return { x: (dx / distance) * length, y: (dy / distance) * length };
}

/**
 * One pointer listener for every tracking orb on the page.  `register(element, apply)` returns an unregister
 * function; `apply({x,y})` receives the pupil offset each animation frame the pointer moved.  After `idleMs`
 * without movement every orb looks back to the centre.  Reduced motion: nothing moves.  Hidden page: nothing runs.
 */
export function createGazeHub(env = globalThis, { idleMs = 3000, max = 3, k = 60 } = {}) {
  const orbs = new Map();
  let pointer = null, frame = 0, idleTimer = 0, listening = false;
  const reduced = () => Boolean(env.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  const hidden = () => env.document?.visibilityState === "hidden";
  const paint = () => {
    frame = 0;
    if (hidden()) return;
    for (const [element, apply] of orbs) {
      if (!element.isConnected && element.isConnected !== undefined) continue;
      const box = element.getBoundingClientRect();
      if (box.width === 0 && box.height === 0) continue;
      const center = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
      apply(reduced() ? { x: 0, y: 0 } : gazeOffset(center, pointer, { max, k }));
    }
  };
  const schedule = () => { if (frame) return; frame = 1; (env.requestAnimationFrame ?? ((cb) => env.setTimeout(cb, 16)))(paint); };
  const onMove = (event) => {
    if (reduced()) return;
    pointer = { x: event.clientX, y: event.clientY };
    schedule();
    env.clearTimeout?.(idleTimer);
    idleTimer = env.setTimeout?.(() => { pointer = null; schedule(); }, idleMs);
  };
  const start = () => { if (listening) return; listening = true; env.addEventListener?.("pointermove", onMove, { passive: true }); env.addEventListener?.("pointerdown", onMove, { passive: true }); };
  const stop = () => { if (!listening) return; listening = false; env.removeEventListener?.("pointermove", onMove); env.removeEventListener?.("pointerdown", onMove); };
  return {
    register(element, apply) {
      orbs.set(element, apply); start();
      return () => { orbs.delete(element); apply({ x: 0, y: 0 }); if (!orbs.size) stop(); };
    },
    size: () => orbs.size,
    _move: onMove,
    _paint: paint,
  };
}
