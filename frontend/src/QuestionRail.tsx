import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type RefObject } from "react";
import { useLocale } from "./i18n";
import { plainChatPreview } from "./chat-thread.mjs";
import { adjacentTurnIndex, markerProgress, railShouldShow, readBookmarkedTurns, writeBookmarkedTurns } from "./question-rail.mjs";

export type RailTurn = { userIndex: number; turnId?: string; question: string; answer: string };

// One short marker per question, a preview card beside the pointer, and keyboard or drag travel through the thread.
export function QuestionRail({ turns, activeTurn, transcriptRef, conversationId }: { turns: RailTurn[]; activeTurn: number; transcriptRef: RefObject<HTMLElement | null>; conversationId: string | null }) {
  const { t } = useLocale();
  const show = railShouldShow(turns.length);
  const [card, setCard] = useState<{ index: number; left: number; top: number } | null>(null);
  const [bookmarks, setBookmarks] = useState(() => readBookmarkedTurns(window.localStorage, conversationId));
  const hideTimer = useRef<number | undefined>(undefined), scrubbing = useRef(false), skipClick = useRef(false);

  useEffect(() => { setBookmarks(readBookmarkedTurns(window.localStorage, conversationId)); setCard(null); }, [conversationId]);
  useEffect(() => () => window.clearTimeout(hideTimer.current), []);

  useEffect(() => {
    if (!show) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !event.altKey || event.shiftKey || event.ctrlKey || event.metaKey) return;
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      // Option+arrow keeps its caret meaning while the composer or another field has focus.
      if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable]")) return;
      const transcript = transcriptRef.current;
      if (!transcript) return;
      const tops = turns.map(turn => document.getElementById(`chat-message-${turn.userIndex}`)?.getBoundingClientRect().top ?? Number.NaN);
      const index = adjacentTurnIndex(tops, transcript.getBoundingClientRect().top, event.key === "ArrowUp" ? "previous" : "next");
      if (index == null) return;
      event.preventDefault();
      revealTurn(index);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [show, turns, transcriptRef]);

  function jumpTo(index: number, behavior: ScrollBehavior) {
    const message = document.getElementById(`chat-message-${turns[index].userIndex}`);
    message?.scrollIntoView({ behavior, block: "start" });
    return message;
  }

  function revealTurn(index: number) {
    const message = jumpTo(index, "smooth");
    if (!message) return;
    message.dataset.flash = "";
    window.setTimeout(() => message.removeAttribute("data-flash"), 900);
    // The smooth scroll aims at the layout it saw; if markdown or images settle afterwards, pull the question back to the top.
    window.setTimeout(() => {
      const transcript = transcriptRef.current;
      if (transcript && Math.abs(message.getBoundingClientRect().top - transcript.getBoundingClientRect().top) > 24) message.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 350);
  }

  function pointAt(index: number, button: HTMLElement) {
    window.clearTimeout(hideTimer.current);
    const rect = button.getBoundingClientRect();
    setCard({ index, left: rect.right + 10, top: rect.top });
  }

  function hideSoon() {
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setCard(null), 140);
  }

  function onMarkerPointerDown(event: PointerEvent<HTMLButtonElement>, index: number) {
    if (event.button !== 0) return;
    scrubbing.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    pointAt(index, event.currentTarget);
  }

  // Dragging along the rail scrubs the thread: each marker the pointer crosses is scrolled to at once.
  function onRailPointerMove(event: PointerEvent<HTMLElement>) {
    if (!scrubbing.current) return;
    const rail = event.currentTarget.getBoundingClientRect();
    const button = document.elementFromPoint(rail.left + rail.width / 2, Math.min(Math.max(event.clientY, rail.top), rail.bottom - 1))?.closest<HTMLElement>("[data-rail-index]");
    const index = Number(button?.dataset.railIndex);
    if (!button || index === card?.index) return;
    skipClick.current = true;
    pointAt(index, button);
    jumpTo(index, "instant");
  }

  function onMarkerClick(index: number) {
    if (skipClick.current) { skipClick.current = false; return; }
    revealTurn(index);
  }

  function toggleBookmark(turnId: string) {
    const next = new Set(bookmarks);
    if (next.has(turnId)) next.delete(turnId);
    else next.add(turnId);
    setBookmarks(next);
    writeBookmarkedTurns(window.localStorage, conversationId, next);
  }

  if (!show) return <div className="agent-question-rail" aria-hidden="true" />;
  const focusIndex = card?.index ?? null, cardTurn = card ? turns[card.index] : undefined;
  const cardSaved = Boolean(cardTurn?.turnId && bookmarks.has(cardTurn.turnId)), bookmarkable = Boolean(conversationId && cardTurn?.turnId);
  return (
    <>
      <nav className="agent-question-rail" aria-label={t("问题导航")} onPointerMove={onRailPointerMove} onPointerUp={() => { scrubbing.current = false; }} onPointerCancel={() => { scrubbing.current = false; }}>
        {turns.map((turn, index) => {
          const saved = Boolean(turn.turnId && bookmarks.has(turn.turnId));
          return <button key={turn.userIndex} type="button" data-rail-index={index} className={saved ? "is-bookmarked" : undefined} aria-label={`${t("问题导航")}: ${turn.question}`} aria-current={index === activeTurn ? "location" : undefined} style={{ "--marker-progress": markerProgress(index, focusIndex) } as CSSProperties} onPointerDown={event => onMarkerPointerDown(event, index)} onClick={() => onMarkerClick(index)} onFocus={event => pointAt(index, event.currentTarget)} onBlur={hideSoon} onPointerEnter={event => pointAt(index, event.currentTarget)} onPointerLeave={hideSoon}><span className="agent-rail-line" aria-hidden="true" />{saved && <span className="agent-rail-dot" aria-hidden="true" />}</button>;
        })}
      </nav>
      {card && cardTurn && <div className="agent-turn-preview" style={{ left: Math.min(card.left, window.innerWidth - 330), top: Math.min(card.top, window.innerHeight - 170) }} onPointerEnter={() => window.clearTimeout(hideTimer.current)} onPointerLeave={hideSoon}>
        <div className="agent-turn-preview-head">
          <strong>{plainChatPreview(cardTurn.question, 100)}</strong>
          <button type="button" className="agent-turn-bookmark" aria-pressed={cardSaved} disabled={!bookmarkable && !cardSaved} aria-label={cardSaved ? t("取消收藏") : t("收藏这一轮")} title={cardSaved ? t("取消收藏") : bookmarkable ? t("收藏这一轮") : t("这一轮还没保存，暂时不能收藏")} onClick={() => { if (cardTurn.turnId) toggleBookmark(cardTurn.turnId); }}><BookmarkIcon filled={cardSaved} /></button>
        </div>
        <p>{plainChatPreview(cardTurn.answer, 240) || t("尚无回答")}</p>
      </div>}
    </>
  );
}

function BookmarkIcon({ filled }: { filled: boolean }) {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true"><path d="M6.5 4.5h11v15L12 15.8l-5.5 3.7z"/></svg>;
}
