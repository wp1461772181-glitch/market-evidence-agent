// The FinBot orb: a round face whose two eyes are white `$` signs that follow the pointer.  Drawn here, not
// copied from any product.  `orb:finbot` is the brand face (logo.svg, black); `orb:<colour>` is the same face
// in that colour.  Older emoji or text avatars are shown as they were.
import { useEffect, useMemo, useRef } from "react";
import { createGazeHub, parseAvatar, type GazeHub } from "./orb.mjs";

let hub: GazeHub | null = null;
const gazeHub = () => (hub ??= createGazeHub(window));

function blinkDelay(seed: string) {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `${(h % 4000) / 1000}s`;
}

export function OrbMark({ color, pupils }: { color: string; pupils?: React.Ref<SVGGElement> }) {
  const id = useMemo(() => `orb-${Math.random().toString(36).slice(2, 9)}`, []);
  return <svg viewBox="0 0 64 64" aria-hidden="true" focusable="false" style={{ ["--orb" as string]: `var(--orb-${color})` }}>
    <defs>
      <radialGradient id={`${id}-shine`} cx="34%" cy="28%" r="70%">
        <stop offset="0" stopColor="#fff" stopOpacity=".22" />
        <stop offset=".55" stopColor="#fff" stopOpacity="0" />
        <stop offset="1" stopColor="#000" stopOpacity=".16" />
      </radialGradient>
    </defs>
    <circle cx="32" cy="32" r="30" fill="var(--orb)" />
    <circle cx="32" cy="32" r="30" fill={`url(#${id}-shine)`} />
    <g className="orb-eyes">
      <g ref={pupils} className="orb-pupils" fill="#fff" fontFamily="'Helvetica Neue',Arial,sans-serif" fontSize="23" fontWeight="700" textAnchor="middle">
        <text x="22" y="37">$</text>
        <text x="42" y="37">$</text>
      </g>
    </g>
  </svg>;
}

export function BotOrb({ avatar, name, size = 28, track = false, className = "" }: { avatar: string | null | undefined; name: string; size?: number; track?: boolean; className?: string }) {
  const parsed = parseAvatar(avatar, name);
  const holder = useRef<HTMLSpanElement>(null), pupils = useRef<SVGGElement>(null);
  useEffect(() => {
    if (!track || parsed.kind !== "orb" || !holder.current) return;
    return gazeHub().register(holder.current, ({ x, y }) => { if (pupils.current) pupils.current.style.transform = `translate(${x.toFixed(2)}px, ${y.toFixed(2)}px)`; });
  }, [track, parsed.kind]);
  if (parsed.kind === "text") return <span className={`bot-avatar ${className}`.trim()} aria-hidden="true">{parsed.text}</span>;
  return <span ref={holder} className={`bot-orb ${className}`.trim()} aria-hidden="true" style={{ width: size, height: size, ["--blink-delay" as string]: blinkDelay(name) }}>
    <OrbMark color={parsed.color} pupils={pupils} />
  </span>;
}
