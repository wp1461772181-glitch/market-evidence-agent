export const chatIdentityKeys = Object.freeze({
  mode: "market-evidence-agent:chat-mode:v1",
  guest: "market-evidence-agent:chat-session:v1",
  personal: "market-evidence-agent:personal-chat-session:v1",
  legacySession: "market-evidence-agent:chat-session:v1",
  conversation: "market-evidence-agent:chat-conversation:v1",
  guestConversation: "market-evidence-agent:chat-conversation:guest:v1",
  personalConversation: "market-evidence-agent:chat-conversation:personal:v1",
  conversationCreatedAt: "market-evidence-agent:chat-conversation-created:v1",
  guestCreatedAt: "market-evidence-agent:chat-conversation-created:guest:v1",
  personalCreatedAt: "market-evidence-agent:chat-conversation-created:personal:v1",
});

export function currentChatMode(localStorage) {
  return localStorage.getItem(chatIdentityKeys.mode) === "personal" && localStorage.getItem(chatIdentityKeys.personal) ? "personal" : "guest";
}

export function changeChatMode(localStorage, sessionStorage, mode, credential) {
  const previous = currentChatMode(localStorage);
  if (previous === "guest" && !localStorage.getItem(chatIdentityKeys.guest)) {
    const legacyGuest = sessionStorage.getItem(chatIdentityKeys.legacySession);
    if (legacyGuest) localStorage.setItem(chatIdentityKeys.guest, legacyGuest);
    sessionStorage.removeItem(chatIdentityKeys.legacySession);
  }
  const previousId = sessionStorage.getItem(chatIdentityKeys.conversation);
  const previousCreated = sessionStorage.getItem(chatIdentityKeys.conversationCreatedAt);
  if (previousId) {
    localStorage.setItem(previous === "personal" ? chatIdentityKeys.personalConversation : chatIdentityKeys.guestConversation, previousId);
    if(previousCreated)localStorage.setItem(previous === "personal" ? chatIdentityKeys.personalCreatedAt : chatIdentityKeys.guestCreatedAt,previousCreated);
  }
  sessionStorage.removeItem(chatIdentityKeys.conversation);
  sessionStorage.removeItem(chatIdentityKeys.conversationCreatedAt);
  if (mode === "personal" && credential) {
    localStorage.setItem(chatIdentityKeys.personal, credential);
    localStorage.setItem(chatIdentityKeys.mode, "personal");
    sessionStorage.removeItem("market-evidence-agent-api-access-key");
  } else {
    localStorage.removeItem(chatIdentityKeys.personal);
    sessionStorage.removeItem("market-evidence-agent-api-access-key");
    localStorage.setItem(chatIdentityKeys.mode, "guest");
  }
}

export function conversationForMode(localStorage, mode = currentChatMode(localStorage)) {
  return localStorage.getItem(mode === "personal" ? chatIdentityKeys.personalConversation : chatIdentityKeys.guestConversation);
}

export function conversationCreatedAtForMode(localStorage, mode = currentChatMode(localStorage)) {
  return localStorage.getItem(mode === "personal" ? chatIdentityKeys.personalCreatedAt : chatIdentityKeys.guestCreatedAt);
}

export function selectConversationForMode(localStorage, sessionStorage, id, mode = currentChatMode(localStorage), createdAt = null) {
  const key = mode === "personal" ? chatIdentityKeys.personalConversation : chatIdentityKeys.guestConversation;
  const createdKey = mode === "personal" ? chatIdentityKeys.personalCreatedAt : chatIdentityKeys.guestCreatedAt;
  if (id) { localStorage.setItem(key, id); if(createdAt)localStorage.setItem(createdKey,createdAt);else localStorage.removeItem(createdKey); }
  else { localStorage.removeItem(key); localStorage.removeItem(createdKey); }
  if (mode === currentChatMode(localStorage)) {
    if (id) { sessionStorage.setItem(chatIdentityKeys.conversation, id);if(createdAt)sessionStorage.setItem(chatIdentityKeys.conversationCreatedAt,createdAt);else sessionStorage.removeItem(chatIdentityKeys.conversationCreatedAt); }
    else { sessionStorage.removeItem(chatIdentityKeys.conversation);sessionStorage.removeItem(chatIdentityKeys.conversationCreatedAt); }
  }
}

export function activeChatCredential(localStorage, sessionStorage, randomCredential) {
  if (currentChatMode(localStorage) === "personal") return localStorage.getItem(chatIdentityKeys.personal);
  let value = localStorage.getItem(chatIdentityKeys.guest);
  if (!value) {
    value = sessionStorage.getItem(chatIdentityKeys.legacySession) || randomCredential();
    localStorage.setItem(chatIdentityKeys.guest, value);
    sessionStorage.removeItem(chatIdentityKeys.legacySession);
  }
  return value;
}
