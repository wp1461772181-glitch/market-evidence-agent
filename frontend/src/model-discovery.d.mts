import type { AgentChatProvider } from "./api";
export function createModelDiscovery(fetcher:(payload:{provider:AgentChatProvider;base_url:string;api_key?:string},options:{signal:AbortSignal})=>Promise<{status?:string;ordering?:string;models?:Array<{id:string;name:string}>}>):{fetch(input:{provider:AgentChatProvider;base_url:string;api_key:string}):Promise<{status?:string;ordering?:string;models:Array<{id:string;name:string}>}|null>;cancel():void};
export function createModelDiscoveryScheduler():{setContext(context:string):void;schedule(callback:()=>void,delay?:number):void;cancel():void};
