/** Shared provider key and guarded async restore commit used by the React chat session. */
export function chatSessionKey(mode, credential) {
  return `chat-${mode}-${credential ? credential.slice(0, 12) : "pending"}`;
}

export async function restoreConversationSafely(lifecycle, token, signal, restore, commit) {
  const saved = await restore(signal);
  if (signal.aborted || !lifecycle.isCurrent(token)) return false;
  commit(saved);
  return true;
}

/** Persist the current archive snapshot only after the restore token owns the lifecycle. */
export async function restoreConversationAfterPersistence(lifecycle, token, signal, persist, restore, commit) {
  await persist();
  if (signal.aborted || !lifecycle.isCurrent(token)) return false;
  return restoreConversationSafely(lifecycle, token, signal, restore, commit);
}
