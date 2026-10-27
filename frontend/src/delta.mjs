// Up and down are never told by colour alone: every signed figure carries an arrow and a sign.
const MINUS = "−";

export function deltaDirection(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

export function deltaGlyph(value) {
  return { up: "↑", down: "↓", flat: "→" }[deltaDirection(value)];
}

/** "↑ +1.2%", "↓ −0.4pp", "→ 0.0%". ``scale`` turns a fraction into percent (100). */
export function formatDelta(value, { digits = 1, scale = 1, suffix = "" } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const scaled = value * scale;
  const shown = Math.abs(scaled).toFixed(digits);
  // A value that rounds to zero is flat, whatever its sign before rounding.
  const direction = Number(shown) === 0 ? "flat" : deltaDirection(scaled);
  const sign = direction === "up" ? "+" : direction === "down" ? MINUS : "";
  return `${{ up: "↑", down: "↓", flat: "→" }[direction]} ${sign}${shown}${suffix}`;
}
