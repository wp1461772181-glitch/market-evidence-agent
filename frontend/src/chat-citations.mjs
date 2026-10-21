export function citationOrdinal(citation, index) {
  return citation?.ordinal ?? index + 1;
}

// Turn the model's inline citation markers into separate links. ReactMarkdown
// renders adjacent links without a separator, so "[5][9]" used to look like
// "59"; keeping one link per ordinal lets the stylesheet draw distinct chips.
// A marker that names no known citation is left as written.
export function linkCitationMarkers(text, citations) {
  const source = String(text ?? "");
  const list = Array.isArray(citations) ? citations : [];
  const known = new Set(list.map((citation, index) => citationOrdinal(citation, index)));
  if (known.size === 0) return source;
  return source.replace(/\[(\d+(?:\s*[,，、;；]\s*\d+)*)\]/g, (marker, group) => {
    const numbers = group.split(/\s*[,，、;；]\s*/).map(Number);
    const linked = numbers.map((number) => known.has(number) ? `[${number}](#citation-${number})` : null);
    if (linked.every((value) => value === null)) return marker;
    return numbers.map((number, index) => linked[index] ?? `[${number}]`).join("");
  });
}
