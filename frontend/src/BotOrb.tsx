// FinBot faces.  A bot's avatar is one of 16 minimal round-face robots (4 silhouettes × 4 eye colours, from the
// FinBot 极简版 design): dark shell, visor, two pill eyes with a glint, a thin monocle ring; the eyes follow the
// pointer.  BrandLogo is the sidebar mark: the 捧报偷瞄笑 pose (public/logo-peek.svg).  Older emoji or text avatars
// are shown as they were.
import { useEffect, useRef } from "react";
import { createGazeHub, MOOD_COLORS, parseAvatar, type BotMood, type BotStyle, type GazeHub } from "./orb.mjs";

let hub: GazeHub | null = null;
const gazeHub = () => (hub ??= createGazeHub(window));

function blinkDelay(seed: string) {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `${(h % 4000) / 1000}s`;
}

const EYE_HEIGHT: Record<BotMood, number> = { idle: 24, focus: 16, bull: 26, mono: 24 };
const SHELL_RX: Record<BotStyle, [number, number]> = { kline: [38, 26], pure: [44, 30], bull: [38, 26], square: [20, 14] };

export function BotFace({ style, mood, pupils }: { style: BotStyle; mood: BotMood; pupils?: React.Ref<SVGGElement> }) {
  const color = MOOD_COLORS[mood], h = EYE_HEIGHT[mood], [shellRx, visorRx] = SHELL_RX[style];
  // The antenna and horns sit outside the dark shell, on the page itself: 极客白's white would vanish on a light page,
  // so they take the page's ink instead (near-black in the light theme, near-white in the dark one).
  const accent = mood === "mono" ? "var(--me-ink, #18181b)" : color;
  const eye = (x: number) => <g transform={`translate(${x} 80)`}>
    <rect x="-7" y={-h / 2} width="14" height={h} rx="7" fill={color} />
    <circle cx="-2" cy="-5" r="2.2" fill="#fff" opacity=".9" />
  </g>;
  return <svg viewBox="24 18 112 112" aria-hidden="true" focusable="false">
    {style === "kline" && <g style={{ fill: accent }}><line x1="80" y1="22" x2="80" y2="38" style={{ stroke: accent }} strokeWidth="2.5" strokeLinecap="round" /><rect x="76" y="24" width="8" height="11" rx="4" /></g>}
    {style === "bull" && <g style={{ fill: accent }}><path d="M46 44 C36 36, 35 25, 44 22 C43 29, 49 35, 56 39 Z" /><path d="M114 44 C124 36, 125 25, 116 22 C117 29, 111 35, 104 39 Z" /></g>}
    <rect x="28" y="36" width="104" height="92" rx={shellRx} fill="#18181b" stroke="#27272a" strokeWidth="2.5" />
    <rect x="38" y="48" width="84" height="64" rx={visorRx} fill="#09090b" />
    <g ref={pupils} className="orb-pupils">
      <g className="orb-eyes">{eye(62)}{eye(98)}</g>
      <circle cx="98" cy="80" r="16" fill="none" stroke={color} strokeWidth="1.8" opacity=".55" />
    </g>
  </svg>;
}

/** The brand mark: FinBot peeking over its newspaper with a smile. */
export function BrandLogo() {
  return <img className="brand-logo" src={`${import.meta.env.BASE_URL}logo-peek.svg`} alt="" aria-hidden="true" />;
}

export function BotOrb({ avatar, name, size = 28, track = false, className = "" }: { avatar: string | null | undefined; name: string; size?: number; track?: boolean; className?: string }) {
  const parsed = parseAvatar(avatar, name);
  const holder = useRef<HTMLSpanElement>(null), pupils = useRef<SVGGElement>(null);
  useEffect(() => {
    if (!track || parsed.kind !== "bot" || !holder.current) return;
    // The hub's offset is sized for a 64-unit face; this one is 112 units across.
    return gazeHub().register(holder.current, ({ x, y }) => { if (pupils.current) pupils.current.style.transform = `translate(${(x * 2).toFixed(2)}px, ${(y * 2).toFixed(2)}px)`; });
  }, [track, parsed.kind]);
  if (parsed.kind === "text") return <span className={`bot-avatar ${className}`.trim()} aria-hidden="true">{parsed.text}</span>;
  return <span ref={holder} className={`bot-orb ${className}`.trim()} aria-hidden="true" style={{ width: size, height: size, ["--blink-delay" as string]: blinkDelay(name) }}>
    <BotFace style={parsed.style} mood={parsed.mood} pupils={pupils} />
  </span>;
}
