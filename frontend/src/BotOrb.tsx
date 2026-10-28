// The FinBot orb (UX batch 2, P2): a round orb with two eyes whose pupils follow the pointer.  Drawn here, not
// copied from any product.  `orb:finbot` is the brand orb (logo.svg: brows, a sideways smile, a rising-arrow tuft).  Older emoji or
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
  if (brand) return <BrandMark id={id} pupils={pupils} />;
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
  </svg>;
}

// The brand orb (UX batch 3): the logo.svg drawing — raised brows, a sideways smile and a rising-arrow tuft
// with a gold sparkle — with the pupils still following the pointer.
function BrandMark({ id, pupils }: { id: string; pupils?: React.Ref<SVGGElement> }) {
  const ink = "#0A4D37";
  return <svg viewBox="0 0 64 64" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id={`${id}-body`} x1="18%" y1="8%" x2="82%" y2="96%">
        <stop offset="0" stopColor="#2BD39A" />
        <stop offset=".55" stopColor="#13A877" />
        <stop offset="1" stopColor="#0B7C59" />
      </linearGradient>
    </defs>
    <g className="orb-tuft" fill="none" stroke="#17B07D" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M26.5 12 L30.4 6.8 L33.8 9.8 L39.4 4.2" />
      <path d="M35.2 3.2 L40.6 2.9 L40.3 8.3" />
    </g>
    <path className="orb-sparkle" d="M54 9 Q54.6 13.4 59 14 Q54.6 14.6 54 19 Q53.4 14.6 49 14 Q53.4 13.4 54 9 Z" fill="#FFC53D" />
    <circle cx="32" cy="36" r="27" fill={`url(#${id}-body)`} />
    <ellipse cx="23" cy="22" rx="8" ry="4.2" transform="rotate(-28 23 22)" fill="#fff" fillOpacity=".28" />
    <g fill="none" stroke={ink} strokeWidth="2.4" strokeLinecap="round">
      <path d="M16.5 26.6 Q22 23.6 27.5 25.6" />
      <path d="M36.5 24.4 Q42 19.8 47.5 23.4" />
    </g>
    <g className="orb-eyes">
      <ellipse cx="22.5" cy="35" rx="5.8" ry="7" fill="#fff" />
      <ellipse cx="41.5" cy="34.4" rx="5.8" ry="7.4" fill="#fff" />
      <g ref={pupils} className="orb-pupils">
        <circle cx="24.4" cy="32.6" r="3.3" fill="#0F1F19" />
        <circle cx="43.4" cy="31.9" r="3.3" fill="#0F1F19" />
        <circle cx="25.5" cy="31.4" r="1.05" fill="#fff" />
        <circle cx="44.5" cy="30.7" r="1.05" fill="#fff" />
      </g>
    </g>
    <path d="M26 47.2 Q32.5 52.6 39.6 46" fill="none" stroke={ink} strokeWidth="2.6" strokeLinecap="round" />
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
