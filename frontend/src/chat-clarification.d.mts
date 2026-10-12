export function normalizeClarification(value: unknown): {request_id:string;reason:string;choices:Array<{label:string;value:string}>}|null;
export function selectedClarification(value: {request_id:string;choices:Array<{value:string}>}, choice:string): {message:string;pendingRequestId:string};
