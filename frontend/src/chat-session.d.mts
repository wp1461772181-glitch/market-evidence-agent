import type { AgentChatLifecycle } from "./agentChatLifecycle.js";
export function chatSessionKey(mode:"guest"|"personal",credential?:string|null):string;
export function restoreConversationSafely<T>(lifecycle:AgentChatLifecycle,token:number,signal:AbortSignal,restore:(signal:AbortSignal)=>Promise<T>,commit:(value:T)=>void):Promise<boolean>;
export function restoreConversationAfterPersistence<T>(lifecycle:AgentChatLifecycle,token:number,signal:AbortSignal,persist:()=>Promise<unknown>,restore:(signal:AbortSignal)=>Promise<T>,commit:(value:T)=>void):Promise<boolean>;
