export function plainChatPreview(value: unknown, limit?: number): string;
export function chatTurnPreviews(messages: Array<{role:string;content:string}>): Array<{userIndex:number;question:string;answer:string}>;
