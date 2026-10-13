export function normalizeClarification(value: unknown): {request_id:string;reason:string;choices:Array<{label:string;value:string}>}|null;
export function selectedClarification(value: {request_id:string;choices:Array<{value:string}>}, choice:string): {message:string;pendingRequestId:string};
export function manualClarification(value: {request_id:string}, reply:string): {message:string;pendingRequestId:string};
export function clarificationTitle(value: {reason:string}): string;
export function isActiveClarification(messages:Array<{role:string;status?:string;clarification?:unknown}>,index:number,readOnly:boolean): boolean;
