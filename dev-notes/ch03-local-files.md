# Chapter 3 — Local chat files

## Phase: bounded implementation correction

User request: “然后模型的对话的话，我感觉不应该只存在于浏览器中，应该保存在用户的电脑的文件里面进行持久化”

The primary independent review found three implementation risks and one visual regression in the first result: archive controls consumed the transcript's implicit grid row; `showOpenFilePicker` was preferred over the save picker; and queued writes consulted the current handle when they ran. It also found that local grouping IDs were shared across tabs and updates replaced conversation creation time. These findings drove this correction.

The transcript now uses a flex column and owns remaining height after intrinsic archive controls and the optional read-only banner. Connection uses the save picker, reads and validates the selected file (including byte limit) before replacing connection state, and leaves the prior connection untouched on picker cancellation or invalid content. Each queued write captures its selected handle, merge state is advanced before enqueueing, and disconnect/reconnect epochs prevent stale completions from changing visible state. Failed streams are aborted where supported. Local archive IDs now live in sessionStorage, creation times survive updates, read-only imports are isolated from the connected archive, and New Chat immediately queues completed prior messages before clearing; an in-flight incomplete turn is excluded. Successful writes show their local timestamp; failures show an error without claiming a save.

The import input remains present and resets after selection; imported files are size checked before text loading. The import review reported that the browser chooser returned a File but the page did not change, without console errors; that observation is not established as an application defect, so no speculative input workaround was added.

Official API reference check: Context7 was not available in this environment. MDN documents `showSaveFilePicker()` as limited availability and secure-context-only; Chrome documentation confirms user gesture requirements and write permission checks. The UI retains export/import as the compatibility path.

## Verification results

- `npm run test:i18n`: passed, 28 tests, 0 failures (including archive target-switch, immediate new-chat persistence semantics, created_at stability, size-before-read and stream-abort cases).
- `npm run build`: passed (`tsc -b` and Vite production bundle; 34 modules transformed).
- `git diff --check`: passed with no output.

Limitations: this worker did not run browser visual checks at desktop/mobile sizes, did not reproduce the native save picker, and did not verify actual filesystem writes in Chrome. Therefore layout overflow/transcript visibility and native picker behavior are code-level corrections, not browser-verified results. File handles are intentionally not remembered across reloads; reconnect the target file in each page session. Unsupported browsers use explicit JSON download/import controls.
