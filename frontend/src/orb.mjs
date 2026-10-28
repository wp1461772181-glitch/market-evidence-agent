// FinBot bot avatars.  A bot's avatar string is `bot:<style>-<mood>` (one of the 4 × 4 minimal round-face robots:
// silhouette × eye colour), an older `orb:<colour>` / ★ (mapped to the nearest face), or any emoji/text (shown as is).
// `bot:` rather than `finbot:` because the server caps avatars at 16 characters ("bot:square-focus" is exactly 16).
export const BOT_STYLES = ["kline", "pure", "bull", "square"];
export const BOT_MOODS = ["idle", "focus", "bull", "mono"];
export const MOOD_COLORS = { idle: "#34d399", focus: "#38bdf8", bull: "#fbbf24", mono: "#f4f4f5" };
export const DEFAULT_AVATAR = "bot:kline-idle";
// The old orb colours, by the mood whose eye colour is closest; `finbot` (the old brand orb) is the default face.
const LEGACY_MOOD = { finbot: "idle", green: "idle", teal: "idle", blue: "focus", purple: "focus", yellow: "bull", orange: "bull",
  brown: "bull", red: "bull", pink: "bull", black: "mono", gray: "mono" };

/** {kind:"bot", style, mood} | {kind:"text", text} for a bot's avatar. */
export function parseAvatar(avatar, name = "") {
  const value = String(avatar ?? "").trim();
  if (value === "★") return { kind: "bot", style: "kline", mood: "idle" };
  const face = value.match(/^bot:([a-z]+)-([a-z]+)$/);
  if (face && BOT_STYLES.includes(face[1]) && BOT_MOODS.includes(face[2])) return { kind: "bot", style: face[1], mood: face[2] };
  const orb = value.match(/^orb:([a-z]+)$/);
  if (orb && Object.hasOwn(LEGACY_MOOD, orb[1])) return { kind: "bot", style: "kline", mood: LEGACY_MOOD[orb[1]] };
  if (value) return { kind: "text", text: value };
  return { kind: "text", text: String(name).slice(0, 1) || "·" };
}

export function botAvatar(style, mood) {
  return BOT_STYLES.includes(style) && BOT_MOODS.includes(mood) ? `bot:${style}-${mood}` : DEFAULT_AVATAR;
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
