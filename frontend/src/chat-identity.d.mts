type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
export const chatIdentityKeys: Readonly<{mode:string;guest:string;personal:string;legacySession:string;conversation:string;guestConversation:string;personalConversation:string;conversationCreatedAt:string;guestCreatedAt:string;personalCreatedAt:string}>;
export function currentChatMode(localStorage:StorageLike):"guest"|"personal";
export function changeChatMode(localStorage:StorageLike,sessionStorage:StorageLike,mode:"guest"|"personal",credential?:string):void;
export function conversationForMode(localStorage:StorageLike,mode?:"guest"|"personal"):string|null;
export function conversationCreatedAtForMode(localStorage:StorageLike,mode?:"guest"|"personal"):string|null;
export function selectConversationForMode(localStorage:StorageLike,sessionStorage:StorageLike,id:string|null,mode?:"guest"|"personal",createdAt?:string|null):void;
export function activeChatCredential(localStorage:StorageLike,sessionStorage:StorageLike,randomCredential:()=>string):string;
