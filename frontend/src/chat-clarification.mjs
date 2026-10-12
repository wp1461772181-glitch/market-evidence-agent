const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function normalizeClarification(value) {
  if (!value || typeof value !== "object" || !requestIdPattern.test(value.request_id)) return null;
  const choices = Array.isArray(value.choices) ? value.choices.filter(choice =>
    choice && typeof choice.label === "string" && choice.label.length <= 120 &&
    typeof choice.value === "string" && /^[A-Za-z0-9 ._:-]{1,80}$/.test(choice.value)
  ).slice(0, 10) : [];
  return { request_id: value.request_id, reason: String(value.reason ?? "").slice(0, 80), choices };
}

export function selectedClarification(clarification, value) {
  if (!clarification?.choices.some(choice => choice.value === value)) throw Error("invalid_clarification_choice");
  return { message: value, pendingRequestId: clarification.request_id };
}
