export const QUESTION_RAIL_MIN_TURNS = 4;
const BOOKMARK_STORAGE_KEY = "market-evidence-agent:bookmarked-turns:v1";
// A marker's length by distance from the focused question: the focused one is drawn whole, its neighbours fall off.
const PROGRESS_BY_DISTANCE = [1, 0.7, 0.4, 0.2];

export function railShouldShow(turnCount) {
  return turnCount >= QUESTION_RAIL_MIN_TURNS;
}

export function markerProgress(index, focusIndex) {
  if (focusIndex == null) return 0;
  return PROGRESS_BY_DISTANCE[Math.abs(index - focusIndex)] ?? 0;
}

// Keyboard steps move between questions relative to the top of the transcript. A question within `tolerance`
// pixels of that top already counts as reached, so stepping back from it goes to the question before.
export function adjacentTurnIndex(tops, viewportTop, direction, tolerance = 24) {
  if (direction === "next") {
    const index = tops.findIndex(top => top > viewportTop + tolerance);
    return index < 0 ? null : index;
  }
  for (let index = tops.length - 1; index >= 0; index -= 1) {
    if (Math.abs(tops[index] - viewportTop) <= tolerance) return index > 0 ? index - 1 : null;
    if (tops[index] < viewportTop) return index;
  }
  return tops.length > 0 ? 0 : null;
}

export function readBookmarkedTurns(storage, conversationId) {
  if (!conversationId) return new Set();
  try {
    const saved = JSON.parse(storage.getItem(BOOKMARK_STORAGE_KEY) ?? "{}");
    return new Set(Array.isArray(saved[conversationId]) ? saved[conversationId] : []);
  } catch {
    return new Set();
  }
}

export function writeBookmarkedTurns(storage, conversationId, turnIds) {
  if (!conversationId) return;
  try {
    const saved = JSON.parse(storage.getItem(BOOKMARK_STORAGE_KEY) ?? "{}");
    storage.setItem(BOOKMARK_STORAGE_KEY, JSON.stringify({ ...saved, [conversationId]: [...turnIds] }));
  } catch {
    // Bookmarks are a per-browser convenience: blocked or full storage only means they do not survive a reload.
  }
}
