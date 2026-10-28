// The FinBot orb (UX batch 2, P2): a round orb with two eyes whose pupils follow the pointer.  Drawn here, not
// copied from any product.  `orb:finbot` is the brand orb, which adds a small rising-bars mouth.  Older emoji or
// text avatars are shown as they were.
import { useEffect, useMemo, useRef } from "react";
import { createGazeHub, parseAvatar, type GazeHub } from "./orb.mjs";

let hub: GazeHub | null = null;
const gazeHub = () => (hub ??= createGazeHub(window));

function blinkDelay(seed: string) {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `${(h % 4000) / 1000}s`;
}

export function OrbMark({ color, brand, pupils }: { color: string; brand: boolean; pupils?: React.Ref<SVGGElement> }) {
  const id = useMemo(() => `orb-${Math.random().toString(36).slice(2, 9)}`, []);
  return <svg viewBox="0 0 64 64" aria-hidden="true" focusable="false" style={{ ["--orb" as string]: `var(--orb-${color})` }}>
    <defs>
      <radialGradient id={`${id}-shine`} cx="34%" cy="28%" r="70%">
        <stop offset="0" stopColor="#fff" stopOpacity=".38" />
        <stop offset=".55" stopColor="#fff" stopOpacity="0" />
        <stop offset="1" stopColor="#000" stopOpacity=".16" />
      </radialGradient>
    </defs>
    <circle cx="32" cy="32" r="30" fill="var(--orb)" />
    <circle cx="32" cy="32" r="30" fill={`url(#${id}-shine)`} />
    <g className="orb-eyes">
      <ellipse cx="23.5" cy="27" rx="5.6" ry="8.2" fill="#fff" />
      <ellipse cx="40.5" cy="27" rx="5.6" ry="8.2" fill="#fff" />
      <g ref={pupils} className="orb-pupils">
        <circle cx="23.5" cy="28" r="3" fill="#111" />
        <circle cx="40.5" cy="28" r="3" fill="#111" />
      </g>
    </g>
    {brand && <g className="orb-bars" fill="#fff" fillOpacity=".92">
      <rect x="25" y="44" width="3.6" height="5" rx="1" />
      <rect x="30.2" y="41" width="3.6" height="8" rx="1" />
      <rect x="35.4" y="38" width="3.6" height="11" rx="1" />
    </g>}
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
    <OrbMark color={parsed.color} brand={parsed.brand} pupils={pupils} />
  </span>;
}
