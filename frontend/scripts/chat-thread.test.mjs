import test from "node:test";
import assert from "node:assert/strict";
import { chatTurnPreviews, plainChatPreview } from "../src/chat-thread.mjs";

test("chat turn previews pair each question with its next assistant answer", () => {
  assert.deepEqual(chatTurnPreviews([
    { role: "user", content: "First question" },
    { role: "assistant", content: "First answer" },
    { role: "user", content: "Second question" },
    { role: "assistant", content: "Second answer" },
  ]), [
    { userIndex: 0, question: "First question", answer: "First answer" },
    { userIndex: 2, question: "Second question", answer: "Second answer" },
  ]);
});

test("an unanswered question does not borrow the next question's answer", () => {
  assert.deepEqual(chatTurnPreviews([
    { role: "user", content: "Question A" },
    { role: "user", content: "Question B" },
    { role: "assistant", content: "Answer B" },
  ]), [
    { userIndex: 0, question: "Question A", answer: "" },
    { userIndex: 1, question: "Question B", answer: "Answer B" },
  ]);
});

test("preview text strips Markdown and markup and never exposes raw JSON", () => {
  assert.equal(plainChatPreview("<b>**Quarterly** [report](https://example.test)").trim(), "Quarterly report");
  assert.equal(plainChatPreview('{"private":true}'), "结构化内容");
  assert.equal(plainChatPreview('Answer: [{"private":true}] more'), "结构化内容");
  assert.ok(plainChatPreview("long ".repeat(30), 24).length <= 24);
});
