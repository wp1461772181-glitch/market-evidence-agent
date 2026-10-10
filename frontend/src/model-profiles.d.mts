import type { AgentChatProvider } from "./api";
export const CATALOG: readonly {provider:AgentChatProvider;label:string;baseUrl:string;suggestion:string}[];
export type ModelProfile={id:string;display_name:string;provider:AgentChatProvider;base_url:string;model:string;api_key:string;tested_at:string|null};
export type ProfileDocument={version:1;profiles:ModelProfile[];selected_id:string|null};
export function loadProfiles(env?:unknown):Promise<{data:ProfileDocument;backend:"OPFS"|"IndexedDB"|"unavailable"}>;
export function saveProfiles(data:ProfileDocument,env?:unknown,preferredBackend?:string):Promise<string>;
