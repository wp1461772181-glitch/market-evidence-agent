import { useCallback, useEffect, useRef, useState } from "react";
import { copySkill, deleteSkill, exportSkill, importSkill, listSkills, previewSkillImport, setSkillEnabled, createSkill, updateSkill, type Skill, type SkillImportPreview, type SkillTool } from "./api";
import { toolLabel } from "./agent-tool-labels.mjs";
import { translatePhrase, useLocale } from "./i18n";
import { skillFromHash, skillGroups } from "./skill-items.mjs";

// The 技能 page: recipes (steps + allowed tools), never code.  Cards grouped into drafts, built-ins and the owner's own;
// an editor for new / edited / drafted skills; Markdown import (preview first) and export.  Owner-only /agent-chat API.

const message = (error: unknown, fallback: string) => translatePhrase(error instanceof Error ? error.message : fallback);
const ORIGIN_LABELS: Record<Skill["origin"], string> = { builtin: "内置", mine: "我的", imported: "导入的" };
const WRITE_LEVELS = new Set(["reversible", "costly", "write"]);
const LEVEL_LABELS: Record<string, string> = { read: "只读", reversible: "可撤销", costly: "耗费较大", write: "写操作" };
const isWrite = (level: string) => WRITE_LEVELS.has(level);

type Editor = { id: string | null; name: string; description: string; body: string; template: string; tools: string[]; draft: boolean };
const blankEditor = (): Editor => ({ id: null, name: "", description: "", body: "", template: "", tools: [], draft: false });
const editorOf = (s: Skill): Editor => ({ id: s.id, name: s.name, description: s.description, body: s.body, template: s.output_template ?? "", tools: s.allowed_tools, draft: s.status === "draft" });

function ToolChips({ names }: { names: string[] }) {
  const { t } = useLocale();
  return <ul className="skill-tools" aria-label={t("允许用的工具")}>{names.map(n => <li key={n} className="personal-badge">{t(toolLabel(n))}</li>)}</ul>;
}

export function SkillsPage() {
  const { t } = useLocale();
  const [skills, setSkills] = useState<Skill[] | null>(null), [tools, setTools] = useState<SkillTool[]>([]);
  const [error, setError] = useState(""), [status, setStatus] = useState(""), [busy, setBusy] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null), [editorError, setEditorError] = useState("");
  const [importOpen, setImportOpen] = useState(false), [markdown, setMarkdown] = useState(""), [preview, setPreview] = useState<SkillImportPreview | null>(null), [importError, setImportError] = useState("");
  const [hashId, setHashId] = useState(() => skillFromHash(window.location.hash));
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback((signal?: AbortSignal) => listSkills(signal).then(data => { setSkills(data.skills); setTools(data.tools); setError(""); })
    .catch(e => { if (!signal?.aborted) setError(message(e, "技能暂时无法读取。")); }), []);
  useEffect(() => { const ctrl = new AbortController(); void load(ctrl.signal); return () => ctrl.abort(); }, [load]);
  useEffect(() => { const read = () => setHashId(skillFromHash(window.location.hash)); window.addEventListener("hashchange", read); return () => window.removeEventListener("hashchange", read); }, []);
  // A #skills/<id> link (drafts link here) opens that skill's editor once the list is in.
  useEffect(() => {
    if (!hashId || !skills) return;
    const target = skills.find(s => s.id === hashId);
    if (target && !target.builtin) { setEditor(editorOf(target)); setEditorError(""); setImportOpen(false); }
    else if (!target) setStatus(t("这个技能不存在。"));
  }, [hashId, skills]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = skillGroups(skills ?? []);
  function closeEditor() { setEditor(null); setEditorError(""); if (skillFromHash(window.location.hash)) window.location.hash = "skills"; }
  function openEditor(next: Editor) { setEditor(next); setEditorError(""); setImportOpen(false); }

  async function act(id: string, work: () => Promise<unknown>, done?: string) {
    setBusy(id); setStatus(""); setError("");
    try { await work(); await load(); if (done) setStatus(t(done)); } catch (e) { setError(message(e, "操作失败，请重试。")); } finally { setBusy(null); }
  }
  async function remove(skill: Skill) {
    if (!window.confirm(`${t("删除")} ${skill.name}？`)) return;
    await act(skill.id, () => deleteSkill(skill.id), "已删除。");
  }
  async function download(skill: Skill) {
    await act(skill.id, async () => {
      const text = await exportSkill(skill.id);
      const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = `${skill.name}.md`; document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    });
  }
  async function saveEditor() {
    if (!editor) return;
    setBusy("editor"); setEditorError("");
    const input = { name: editor.name.trim(), description: editor.description.trim(), body: editor.body, allowed_tools: editor.tools, output_template: editor.template.trim() || null };
    try {
      if (editor.id) await updateSkill(editor.id, editor.draft ? { ...input, save_draft: true } : input); else await createSkill({ ...input, enabled: true });
      await load(); setStatus(t("已保存。")); closeEditor();
    } catch (e) { setEditorError(message(e, "保存失败，请重试。")); } finally { setBusy(null); }
  }
  async function runPreview() {
    setBusy("import"); setImportError(""); setPreview(null);
    try { setPreview(await previewSkillImport(markdown)); } catch (e) { setImportError(message(e, "文件无法预览。")); } finally { setBusy(null); }
  }
  async function runImport() {
    setBusy("import"); setImportError("");
    try { await importSkill(markdown); await load(); setStatus(t("已导入。")); setImportOpen(false); setMarkdown(""); setPreview(null); } catch (e) { setImportError(message(e, "导入失败，请重试。")); } finally { setBusy(null); }
  }
  async function pickFile(file: File | undefined) {
    if (!file) return;
    try { setMarkdown(await file.text()); setPreview(null); setImportError(""); } catch { setImportError(t("文件无法读取。")); }
    if (fileInput.current) fileInput.current.value = "";
  }

  function card(skill: Skill) {
    const editable = !skill.builtin, off = busy === skill.id;
    return <article className="personal-card skill-card" key={skill.id}>
      <div className="memory-line"><h2>{skill.name}</h2><span className="personal-badge">{t(skill.status === "draft" ? "草稿" : ORIGIN_LABELS[skill.origin])}</span><span className="fine-print">v{skill.version}</span></div>
      <p>{skill.description}</p>
      <ToolChips names={skill.allowed_tools} />
      {skill.status === "active" && <label className="bot-check"><input type="checkbox" checked={skill.enabled} disabled={off} onChange={e => void act(skill.id, () => setSkillEnabled(skill.id, e.target.checked))} />{t("启用")}</label>}
      <div className="memory-actions">
        {editable && <button type="button" className="secondary-action" disabled={off} onClick={() => openEditor(editorOf(skill))}>{t(skill.status === "draft" ? "核对并保存" : "编辑")}</button>}
        <button type="button" className="secondary-action" disabled={off} onClick={() => void act(skill.id, () => copySkill(skill.id), "已复制，可以在「我的」里编辑。")}>{t("复制")}</button>
        <button type="button" className="secondary-action" disabled={off} onClick={() => void download(skill)}>{t("导出")}</button>
        {editable && <button type="button" className="secondary-action" disabled={off} onClick={() => void remove(skill)}>{t("删除")}</button>}
      </div>
    </article>;
  }
  const section = (title: string, list: Skill[], note?: string) => list.length > 0 && <section className="skill-group" aria-label={t(title)}>
    <h2>{t(title)}</h2>{note && <p className="fine-print">{t(note)}</p>}
    <div className="personal-grid">{list.map(card)}</div>
  </section>;

  return <main className="personal-page" aria-labelledby="skills-title">
    <h1 id="skills-title">{t("技能")}</h1>
    <p className="fine-print">{t("技能是做事的步骤，不是代码。在 bot 对话里输入「/技能名」使用；技能只会减少可用工具，写操作照样要你确认。")}</p>
    <div className="memory-actions">
      <button type="button" className="primary-action" onClick={() => openEditor(blankEditor())}>{t("新建技能")}</button>
      <button type="button" className="secondary-action" onClick={() => { setImportOpen(open => !open); setEditor(null); }}>{t("导入")}</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}

    {importOpen && <section className="task-form skill-import" aria-label={t("导入技能")}>
      <label>{t("技能文件（Markdown）")}<textarea value={markdown} rows={8} maxLength={20000} onChange={e => { setMarkdown(e.target.value); setPreview(null); }} /></label>
      <div className="memory-actions">
        <input ref={fileInput} type="file" accept=".md,text/markdown,text/plain" hidden onChange={e => void pickFile(e.target.files?.[0])} />
        <button type="button" className="secondary-action" onClick={() => fileInput.current?.click()}>{t("选择 .md 文件")}</button>
        <button type="button" className="secondary-action" disabled={busy === "import" || !markdown.trim()} onClick={() => void runPreview()}>{t("预览")}</button>
      </div>
      {preview && <div className="skill-preview">
        <p><strong>{preview.skill.name}</strong> · {preview.skill.description}</p>
        <p>{t("这个技能能用这些工具：")}</p>
        <ul className="skill-tools">{preview.tools.map(tool => <li key={tool.name} className="personal-badge">{t(toolLabel(tool.name))}{tool.write ? ` · ${t("写操作")}` : ""}</li>)}</ul>
        {preview.tools.some(tool => tool.write) && <p className="fine-print">{t("写操作：仍需确认卡")}</p>}
        <div className="memory-actions"><button type="button" className="primary-action" disabled={busy === "import"} onClick={() => void runImport()}>{t("导入")}</button></div>
      </div>}
      {importError && <p role="alert">{importError}</p>}
    </section>}

    {editor && <form className="task-form skill-editor" aria-label={t(editor.id ? "编辑技能" : "新建技能")} onSubmit={e => { e.preventDefault(); void saveEditor(); }}>
      {editor.draft && <p className="fine-print">{t("Agent 起草的草稿：核对后保存才会生效。")}</p>}
      <label>{t("名字")}<input type="text" value={editor.name} maxLength={40} onChange={e => setEditor({ ...editor, name: e.target.value })} /></label>
      <label>{t("一句话说明什么时候用")}<input type="text" value={editor.description} maxLength={120} onChange={e => setEditor({ ...editor, description: e.target.value })} /></label>
      <label>{t("步骤")}<textarea value={editor.body} rows={10} maxLength={6000} onChange={e => setEditor({ ...editor, body: e.target.value })} /></label>
      <label>{t("输出模板（可选）")}<textarea value={editor.template} rows={4} maxLength={2000} onChange={e => setEditor({ ...editor, template: e.target.value })} /></label>
      <fieldset className="skill-tool-list"><legend>{t("允许用的工具")}</legend>
        {tools.map(tool => <label className="bot-tool" key={tool.name}>
          <input type="checkbox" checked={editor.tools.includes(tool.name)} onChange={e => setEditor({ ...editor, tools: e.target.checked ? [...editor.tools, tool.name] : editor.tools.filter(n => n !== tool.name) })} />
          {t(toolLabel(tool.name))} <span className="fine-print">{t(LEVEL_LABELS[tool.level] ?? tool.level)}{isWrite(tool.level) ? ` · ${t("写操作：仍需确认卡")}` : ""}</span>
        </label>)}
      </fieldset>
      {editorError && <p role="alert">{editorError}</p>}
      <div className="memory-actions">
        <button type="submit" className="primary-action" disabled={busy === "editor"}>{busy === "editor" ? t("正在保存…") : t(editor.draft ? "保存为技能" : "保存")}</button>
        <button type="button" className="secondary-action" onClick={closeEditor}>{t("取消")}</button>
      </div>
    </form>}

    {skills === null && !error ? <p>{t("正在加载…")}</p> : <>
      {section("草稿", groups.drafts, "Agent 起草的草稿：核对后保存才会生效。")}
      {section("内置", groups.builtin)}
      {section("我的 / 导入的", groups.mine)}
      {skills !== null && skills.length === 0 && <p>{t("还没有技能。")}</p>}
    </>}
  </main>;
}
