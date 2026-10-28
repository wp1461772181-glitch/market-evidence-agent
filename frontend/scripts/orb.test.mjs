import assert from "node:assert/strict";
import test from "node:test";
import { BOT_MOODS, BOT_STYLES, botAvatar, createGazeHub, DEFAULT_AVATAR, gazeOffset, MOOD_COLORS, parseAvatar } from "../src/orb.mjs";

test("avatars: the 16 faces round-trip and fit the server's 16-character cap", () => {
  assert.equal(BOT_STYLES.length * BOT_MOODS.length, 16);
  const all = BOT_STYLES.flatMap((style) => BOT_MOODS.map((mood) => botAvatar(style, mood)));
  assert.equal(new Set(all).size, 16);
  for (const avatar of all) {
    assert.ok(avatar.length <= 16, avatar);
    const [style, mood] = avatar.slice(4).split("-");
    assert.deepEqual(parseAvatar(avatar), { kind: "bot", style, mood });
  }
  assert.equal(DEFAULT_AVATAR, "bot:kline-idle");
  assert.equal(botAvatar("neon", "idle"), DEFAULT_AVATAR);
  assert.equal(MOOD_COLORS.idle, "#34d399");
  assert.deepEqual(parseAvatar("bot:kline-neon", "Apple"), { kind: "text", text: "bot:kline-neon" });
});

test("avatars: older orb colours, the old ★, emoji and blanks", () => {
  assert.deepEqual(parseAvatar("orb:finbot"), { kind: "bot", style: "kline", mood: "idle" });
  assert.deepEqual(parseAvatar("★"), { kind: "bot", style: "kline", mood: "idle" });
  assert.deepEqual(parseAvatar("orb:blue"), { kind: "bot", style: "kline", mood: "focus" });
  assert.deepEqual(parseAvatar("orb:pink"), { kind: "bot", style: "kline", mood: "bull" });
  assert.deepEqual(parseAvatar("orb:gray"), { kind: "bot", style: "kline", mood: "mono" });
  assert.deepEqual(parseAvatar("orb:neon", "Apple"), { kind: "text", text: "orb:neon" });
  assert.deepEqual(parseAvatar("orb:constructor", "Apple"), { kind: "text", text: "orb:constructor" });
  assert.deepEqual(parseAvatar("📈"), { kind: "text", text: "📈" });
  assert.deepEqual(parseAvatar("", "Apple"), { kind: "text", text: "A" });
});

test("gaze points toward the pointer and is capped", () => {
  assert.deepEqual(gazeOffset({ x: 0, y: 0 }, null), { x: 0, y: 0 });
  const near = gazeOffset({ x: 0, y: 0 }, { x: 30, y: 0 }, { max: 3, k: 60 });
  assert.deepEqual(near, { x: 0.5, y: 0 });
  const far = gazeOffset({ x: 0, y: 0 }, { x: 0, y: -1000 }, { max: 3, k: 60 });
  assert.equal(Math.round(far.y * 1000) / 1000, -3);
  assert.equal(Math.abs(far.x), 0);
});

function fakeEnv({ reduced = false } = {}) {
  const listeners = new Map(), timers = [];
  return {
    listeners, timers,
    matchMedia: () => ({ matches: reduced }),
    document: { visibilityState: "visible" },
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name) => listeners.delete(name),
    requestAnimationFrame: (cb) => { cb(); return 1; },
    setTimeout: (cb) => { timers.push(cb); return timers.length; },
    clearTimeout: () => {},
  };
}
const element = (x, y) => ({ isConnected: true, getBoundingClientRect: () => ({ left: x - 10, top: y - 10, width: 20, height: 20 }) });

test("one listener drives every orb; idle looks back to centre; the last orb removes the listener", () => {
  const env = fakeEnv(), hub = createGazeHub(env), seen = [];
  const off = hub.register(element(100, 100), (o) => seen.push(o));
  hub.register(element(0, 0), () => {});
  assert.equal(env.listeners.size, 2);
  env.listeners.get("pointermove")({ clientX: 400, clientY: 100 });
  assert.equal(seen.at(-1).x, 3);
  env.timers.at(-1)();
  assert.deepEqual(seen.at(-1), { x: 0, y: 0 });
  off();
  assert.equal(hub.size(), 1);
});

test("reduced motion keeps the pupils centred", () => {
  const env = fakeEnv({ reduced: true }), hub = createGazeHub(env), seen = [];
  hub.register(element(0, 0), (o) => seen.push(o));
  env.listeners.get("pointermove")({ clientX: 500, clientY: 500 });
  hub._paint();
  assert.ok(seen.every((o) => o.x === 0 && o.y === 0));
});
