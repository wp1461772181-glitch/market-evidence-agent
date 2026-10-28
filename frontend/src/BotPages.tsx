// Bots (personal agent, second edition): the sidebar list, the new-bot dialog, the bot page's right tabs and the
// server model catalog card in settings.  A bot never holds a key: it names a catalog model, and the catalog says
// only configured / missing.
import { useCallback, useEffect, useRef, useState } from "react";
import { addCatalogModel, createBot, deleteBot, deleteCatalogModel, getBotTemplates, getChatMode, getModelCatalog, listBots, setDefaultCatalogModel, updateBot, updateCatalogModel,
  type Bot, type BotFields, type BotTemplate, type BotTool, type CatalogEntryInput, type CatalogModel, type ModelCatalog } from "./api";
import { translatePhrase, useLocale } from "./i18n";
import { botDraft, botModelLabel, botSections, focusText, keyStatusLabel, parseFocusSymbols, toggleTool, toolEnabled } from "./bot-items.mjs";
import { toolLabel } from "./agent-tool-labels.mjs";
import { TaskManager } from "./BotRoutines";
import { CATALOG } from "./model-profiles.mjs";

const message = (error: unknown, fallback: string) => translatePhrase(error instanceof Error ? error.message : fallback);

/** The owner's bots, read once and again after each change.  Guests have none. */
export function useBots(enabled: boolean) {
  const [bots, setBots] = useState<Bot[]>([]), [loaded, setLoaded] = useState(!enabled), [error, setError] = useState("");
  const reload = useCallback(async () => {
    if (!enabled) return;
    try { const value = await listBots(); setBots(value.bots); setError(""); }
    catch (err) { setError(message(err, "bot 列表暂时无法读取。")); }
    finally { setLoaded(true); }
  }, [enabled]);
  useEffect(() => { void reload(); }, [reload]);
  return { bots, loaded, error, reload, defaultBot: bots.find(b => b.is_default) ?? null };
}

/** The catalog, the tool list and the templates a bot form needs. */
function useBotOptions() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null), [tools, setTools] = useState<BotTool[]>([]), [templates, setTemplates] = useState<BotTemplate[]>([]), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getModelCatalog(controller.signal), getBotTemplates(controller.signal)])
      .then(([c, tpl]) => { setCatalog(c); setTools(tpl.tools); setTemplates(tpl.templates); })
      .catch(err => { if (!controller.signal.aborted) setError(message(err, "模板暂时无法读取。")); });
    return () => controller.abort();
  }, []);
  return { catalog, tools, templates, error };
}

function BotAvatar({ bot }: { bot: Pick<Bot, "avatar" | "name"> }) { return <span className="bot-avatar" aria-hidden="true">{bot.avatar || bot.name.slice(0, 1) || "·"}</span>; }

export function BotRailList({ bots, error, activeBotId, confirmActive, onConfirm, onOpen, onNew }: { bots: Bot[]; error: string; activeBotId: string | null; confirmActive: boolean; onConfirm: () => void; onOpen: (bot: Bot) => void; onNew: () => void }) {
  const { t } = useLocale();
  const { top, groups } = botSections(bots);
  const pending = bots.reduce((sum, b) => sum + (b.pending || 0), 0);
  const row = (bot: Bot) => <li key={bot.id}><button type="button" className={`bot-row${bot.id === activeBotId ? " is-active" : ""}`} aria-current={bot.id === activeBotId ? "page" : undefined} onClick={() => onOpen(bot)}>
    <BotAvatar bot={bot} /><span className="bot-row-text"><strong>{bot.name}{bot.is_default && bot.avatar !== "★" && <span className="bot-star" aria-label={t("默认")}> ★</span>}</strong><small>{bot.last_message || bot.title || t("还没有消息")}</small></span>
    {bot.pending > 0 && <span className="bot-pending-dot" role="img" aria-label={t("有待你确认的建议")} title={t("有待你确认的建议")} />}
    {bot.unread > 0 && <span className="bot-unread" aria-label={`${bot.unread} ${t("条未读")}`}>{bot.unread > 99 ? "99+" : bot.unread}</span>}
  </button></li>;
  return <section className="bot-rail" aria-label={t("Bot 列表")}>
    <button type="button" className={`bot-row bot-confirm${confirmActive ? " is-active" : ""}`} aria-current={confirmActive ? "page" : undefined} onClick={onConfirm}>
      <span className="bot-avatar" aria-hidden="true">✉</span><span className="bot-row-text"><strong>{t("待你确认")}</strong></span>
      {pending > 0 && <span className="bot-pending-badge" aria-label={`${pending} ${t("条待确认")}`}>{pending}</span>}
    </button>
    <div className="bot-rail-head"><span>{t("Bot")}</span><button type="button" className="bot-new" onClick={onNew} aria-label={t("新建 bot")} title={t("新建 bot")}>＋</button></div>
    {error && <p className="bot-rail-error" role="alert">{error}</p>}
    <ul>{top && row(top)}</ul>
    {groups.map(group => <div key={group.name || "_"}>{group.name && <p className="bot-group">{group.name}</p>}<ul>{group.bots.map(row)}</ul></div>)}
  </section>;
}

type Draft = ReturnType<typeof botDraft> & { avatar?: string; group?: string; pinned?: boolean; hidden?: boolean; daily_notify_cap?: number };

/** The profile fields of a bot: shared by the new-bot dialog and the 档案 tab. */
function BotForm({ draft, setDraft, tools, catalog, isDefault, disabled }: { draft: Draft; setDraft: (next: Draft) => void; tools: BotTool[]; catalog: ModelCatalog | null; isDefault: boolean; disabled: boolean }) {
  const { t } = useLocale();
  const [focusInput, setFocusInput] = useState(() => focusText(draft.focus));
  const parsed = parseFocusSymbols(focusInput);
  const names = tools.map(x => x.name);
  const update = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  return <div className="bot-form">
    <label>{t("名字")}<input value={draft.name} maxLength={40} disabled={disabled} onChange={e => update({ name: e.target.value })} placeholder={t("例如：读财报")} /></label>
    <label>{t("头衔")}<input value={draft.title} maxLength={60} disabled={disabled} onChange={e => update({ title: e.target.value })} placeholder={t("例如：财报解读")} /></label>
    <label>{t("说明")}<textarea rows={5} value={draft.description} maxLength={4000} disabled={disabled} onChange={e => update({ description: e.target.value })} placeholder={t("它的工作是什么、怎么做、什么时候该通知你。")} /></label>
    <label>{t("关注公司（可空）")}<input value={focusInput} disabled={disabled} maxLength={240} placeholder="AAPL, MSFT"
      onChange={e => { setFocusInput(e.target.value); const next = parseFocusSymbols(e.target.value); update({ focus: { ...draft.focus, companies: next.companies } }); }} />
      <small>{parsed.invalid.length ? `${t("不是股票代码")}：${parsed.invalid.join(", ")}` : parsed.companies.length === 1 ? `${t("没点名公司的问题默认问")} ${parsed.companies[0].symbol}` : parsed.companies.length > 1 ? t("关注多家公司时，没有默认公司。") : t("不填就没有默认公司。")}{parsed.truncated ? ` ${t("最多 20 家。")}` : ""}</small></label>
    <label>{t("关注主题（可空）")}<input value={draft.focus.theme ?? ""} maxLength={200} disabled={disabled} onChange={e => update({ focus: { ...draft.focus, theme: e.target.value || null } })} placeholder={t("例如：AI 资本开支")} /></label>
    <label>{t("模型")}<select value={draft.model ?? ""} disabled={disabled || !catalog} onChange={e => update({ model: e.target.value || null })}>
      <option value="">{t("服务器默认模型")}{catalog?.default ? ` (${catalog.default})` : ""}</option>
      {catalog?.models.map(m => <option key={m.name} value={m.name}>{m.display_name || m.name} · {t(keyStatusLabel(m))}</option>)}
    </select><small>{t("换模型后下一轮生效。key 只在服务器上。")}</small></label>
    <fieldset className="bot-tools" disabled={disabled}><legend>{t("工具范围")} · {draft.tool_scope === null ? t("全部工具") : `${draft.tool_scope.length} / ${names.length}`}</legend>
      <div className="bot-tools-actions"><button type="button" className="text-button" onClick={() => update({ tool_scope: null })}>{t("全部打开")}</button></div>
      <div className="bot-tools-grid">{tools.map(tool => <label key={tool.name} className="bot-tool"><input type="checkbox" checked={toolEnabled(draft.tool_scope, tool.name)} onChange={() => update({ tool_scope: toggleTool(draft.tool_scope, tool.name, names) })} /><span>{t(toolLabel(tool.name))}</span>{tool.level !== "read" && <small>{t("写入需确认")}</small>}</label>)}</div>
      <small>{t("提问、引用、计算等基础工具总是开着。范围外的工具这个 bot 调不到。")}</small>
    </fieldset>
    <div className="bot-form-row">
      <label>{t("头像")}<input value={draft.avatar ?? ""} maxLength={16} disabled={disabled} onChange={e => update({ avatar: e.target.value })} placeholder="📈" /></label>
      <label>{t("分组")}<input value={draft.group ?? ""} maxLength={40} disabled={disabled} onChange={e => update({ group: e.target.value })} placeholder={t("例如：公司")} /></label>
      <label>{t("每日通知上限")}<input type="number" min={0} max={50} value={draft.daily_notify_cap ?? 5} disabled={disabled} onChange={e => update({ daily_notify_cap: Math.max(0, Math.min(50, Number(e.target.value) || 0)) })} /></label>
    </div>
    <div className="bot-form-row">
      <label className="bot-check"><input type="checkbox" checked={Boolean(draft.pinned)} disabled={disabled} onChange={e => update({ pinned: e.target.checked })} />{t("置顶")}</label>
      {!isDefault && <label className="bot-check"><input type="checkbox" checked={Boolean(draft.hidden)} disabled={disabled} onChange={e => update({ hidden: e.target.checked })} />{t("在侧栏隐藏")}</label>}
    </div>
  </div>;
}

function fieldsOf(draft: Draft): BotFields {
  return { name: draft.name.trim(), title: draft.title.trim(), description: draft.description, focus: { companies: draft.focus.companies, theme: draft.focus.theme?.trim() || null },
    tool_scope: draft.tool_scope, model: draft.model, avatar: (draft.avatar ?? "").trim(), group: (draft.group ?? "").trim(), pinned: Boolean(draft.pinned),
    ...(draft.hidden !== undefined ? { hidden: draft.hidden } : {}), ...(draft.daily_notify_cap !== undefined ? { daily_notify_cap: draft.daily_notify_cap } : {}), template: draft.template };
}
function draftOf(bot: Bot): Draft {
  return { name: bot.name, title: bot.title, description: bot.description, focus: bot.focus, tool_scope: bot.tool_scope, model: bot.model, template: bot.template,
    avatar: bot.avatar, group: bot.group, pinned: bot.pinned, hidden: bot.hidden, daily_notify_cap: bot.daily_notify_cap };
}

export function NewBotDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (bot: Bot) => void }) {
  const { t } = useLocale();
  const { catalog, tools, templates, error } = useBotOptions();
  const [draft, setDraft] = useState<Draft | null>(null), [busy, setBusy] = useState(false), [status, setStatus] = useState("");
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => { dialog.current?.querySelector<HTMLElement>("button,input")?.focus(); const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); }; document.addEventListener("keydown", key); return () => document.removeEventListener("keydown", key); }, [onClose]);
  async function save() {
    if (!draft) return;
    setBusy(true); setStatus("");
    try { onCreated(await createBot(fieldsOf(draft))); } catch (err) { setStatus(message(err, t("创建失败，请重试。"))); } finally { setBusy(false); }
  }
  return <div className="profile-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <section ref={dialog} className="profile-modal bot-dialog" role="dialog" aria-modal="true" aria-labelledby="new-bot-title">
      <button className="profile-close" onClick={onClose} aria-label={t("关闭")} disabled={busy}>×</button>
      <p className="eyebrow">{t("新建 bot")}</p><h2 id="new-bot-title">{draft ? t("填写它的岗位") : t("从模板开始")}</h2>
      {error && <p role="alert">{error}</p>}
      {!draft && <div className="bot-templates">{templates.map(tpl => <button key={tpl.id} type="button" className="profile-card" onClick={() => setDraft(botDraft(tpl, tools))}>
        <strong>{t(tpl.label)}</strong><small>{tpl.description ? t(tpl.description) : t("从空白开始，自己写说明。")}</small>
        <small>{tpl.tool_scope === null ? t("全部工具") : `${tpl.tool_scope.length} ${t("个工具")}`}</small></button>)}
        {!templates.length && !error && <p role="status">{t("正在读取…")}</p>}
        <p className="fine-print">{t("模板只是捷径，选完照样能改。")}</p></div>}
      {draft && <><BotForm draft={draft} setDraft={setDraft} tools={tools} catalog={catalog} isDefault={false} disabled={busy} />
        {status && <p className="agent-chat-status is-error" role="alert">{status}</p>}
        <div className="chat-archive-tools"><button type="button" className="secondary-action" disabled={busy} onClick={() => setDraft(null)}>{t("换模板")}</button>
          <button type="button" className="primary-action" disabled={busy || !draft.name.trim()} onClick={() => void save()}>{busy ? t("正在保存…") : t("创建")}</button></div></>}
    </section>
  </div>;
}

/** The 档案 tab: the bot's settings, saved as a whole; its memory, routines and skills arrive in later stages. */
function BotProfileTab({ bot, onChanged, onDeleted }: { bot: Bot; onChanged: (bot: Bot) => void; onDeleted: () => void }) {
  const { t } = useLocale();
  const { catalog, tools, error } = useBotOptions();
  const [draft, setDraft] = useState<Draft>(() => draftOf(bot)), [busy, setBusy] = useState(false), [status, setStatus] = useState("");
  const model = botModelLabel(bot, catalog);
  async function save() {
    setBusy(true); setStatus("");
    try { const next = await updateBot(bot.id, fieldsOf(draft)); onChanged(next); setDraft(draftOf(next)); setStatus(t("已保存。下一轮按新设置运行。")); }
    catch (err) { setStatus(message(err, t("保存失败，请重试。"))); } finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm(`${t("删除")} ${bot.name}？${t("它的设置会删掉，这条线的对话归档保留；其他 bot 不受影响。")}`)) return;
    setBusy(true); setStatus("");
    try { await deleteBot(bot.id); onDeleted(); } catch (err) { setStatus(message(err, t("删除失败，请重试。"))); setBusy(false); }
  }
  return <div className="bot-profile">
    <p className="bot-model-line">{t("模型")}：<strong>{model.label || t("尚未设置")}</strong>{model.isDefault && ` · ${t("服务器默认")}`}{model.name && !model.usable && <span className="bot-warn"> · {t("缺 key，去设置里补上")}</span>}</p>
    {bot.default_company && <p className="fine-print">{t("默认公司")}：{bot.default_company.symbol}{bot.default_company.company ? ` · ${bot.default_company.company}` : ""}</p>}
    {error && <p role="alert">{error}</p>}
    <BotForm key={bot.id + bot.updated_at} draft={draft} setDraft={setDraft} tools={tools} catalog={catalog} isDefault={bot.is_default} disabled={busy} />
    {status && <p role="status">{status}</p>}
    <div className="chat-archive-tools"><button type="button" className="primary-action" disabled={busy || !draft.name.trim()} onClick={() => void save()}>{busy ? t("正在保存…") : t("保存")}</button>
      {!bot.is_default && <button type="button" className="secondary-action" disabled={busy} onClick={() => void remove()}>{t("删除这个 bot")}</button>}</div>
    {bot.is_default && <small>{t("默认 bot 可以改名和说明，不能删除。没指明给谁的消息进它。")}</small>}
    <section className="bot-placeholder"><h3>{t("它的记忆")}</h3><p>{t("这个 bot 自己的记忆在下一阶段加入；现在全局记忆在「记忆」页。")}</p></section>
    <TaskManager botId={bot.id} />
    <section className="bot-placeholder"><h3>{t("技能")}</h3><p>{t("给 bot 指定常用技能在后续阶段加入。")}</p></section>
  </div>;
}

/** The 工作区 tab: where the line's work points — its focus companies and the research pages they open. */
function BotWorkspaceTab({ bot, symbol, onOpenCompany }: { bot: Bot; symbol: string; onOpenCompany: (ticker: string) => void }) {
  const { t } = useLocale();
  const companies = bot.focus.companies;
  return <div className="bot-workspace">
    <p className="fine-print">{t("当前股票")}：<strong>{symbol}</strong></p>
    {companies.length ? <ul className="bot-focus-list">{companies.map(c => <li key={c.symbol}><button type="button" className="secondary-action" onClick={() => onOpenCompany(c.symbol)}>{c.symbol}{c.name ? ` · ${c.name}` : ""} →</button></li>)}</ul>
      : <p>{t("这个 bot 没有关注公司；任何公司都能问。")}</p>}
    {bot.focus.theme && <p>{t("关注主题")}：{bot.focus.theme}</p>}
    <p className="fine-print">{t("隔离的是记忆和注意力，不是数据：任何 bot 都能查所有公司的数据，工具范围限定的除外。")}</p>
  </div>;
}

export function BotSide({ bot, symbol, onOpenCompany, onChanged, onDeleted }: { bot: Bot; symbol: string; onOpenCompany: (ticker: string) => void; onChanged: (bot: Bot) => void; onDeleted: () => void }) {
  const { t } = useLocale();
  const [tab, setTab] = useState<"workspace" | "profile">("workspace");
  return <aside className="bot-side" aria-label={`${bot.name} · ${t("档案")}`}>
    <div className="section-tabs" role="tablist">
      <button type="button" role="tab" aria-selected={tab === "workspace"} aria-current={tab === "workspace" ? "page" : undefined} onClick={() => setTab("workspace")}>{t("工作区")}</button>
      <button type="button" role="tab" aria-selected={tab === "profile"} aria-current={tab === "profile" ? "page" : undefined} onClick={() => setTab("profile")}>{t("档案")}</button>
    </div>
    {tab === "workspace" ? <BotWorkspaceTab bot={bot} symbol={symbol} onOpenCompany={onOpenCompany} /> : <BotProfileTab key={bot.id} bot={bot} onChanged={onChanged} onDeleted={onDeleted} />}
  </aside>;
}

type EntryDraft = { name: string; display_name: string; provider: string; model: string; base_url: string; key_mode: "vault" | "env"; api_key: string; key_env: string; is_default: boolean; editing: CatalogModel | null };
const blankEntry = (): EntryDraft => ({ name: "", display_name: "", provider: CATALOG[0].provider, model: "", base_url: CATALOG[0].baseUrl, key_mode: "vault", api_key: "", key_env: "", is_default: false, editing: null });

/** Settings: the server's model catalog.  The page sends a key once and never reads it back. */
export function ModelCatalogCard() {
  const { t } = useLocale();
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null), [status, setStatus] = useState(""), [busy, setBusy] = useState(false), [entry, setEntry] = useState<EntryDraft | null>(null);
  const load = useCallback(async () => { try { setCatalog(await getModelCatalog()); } catch (err) { setStatus(message(err, t("模型目录暂时无法读取。"))); } }, [t]);
  useEffect(() => { void load(); }, [load]);
  if (getChatMode() !== "personal") return null;
  const moved = entry?.editing ? entry.provider !== entry.editing.provider || (entry.base_url || null) !== (entry.editing.base_url || null) : false;
  const needsKey = !entry?.editing || moved;
  const hasKey = entry ? (entry.key_mode === "vault" ? entry.api_key.length > 0 : entry.key_env.trim().length > 0) : false;
  async function run(work: () => Promise<unknown>, done: string) {
    setBusy(true); setStatus("");
    try { await work(); await load(); setStatus(t(done)); return true; } catch (err) { setStatus(message(err, t("保存失败。"))); return false; } finally { setBusy(false); }
  }
  async function saveEntry() {
    if (!entry) return;
    const body: CatalogEntryInput = { display_name: entry.display_name.trim() || undefined, provider: entry.provider, model: entry.model.trim(), base_url: entry.base_url.trim() || null,
      ...(hasKey ? entry.key_mode === "vault" ? { api_key: entry.api_key } : { key_env: entry.key_env.trim() } : {}), ...(entry.is_default ? { is_default: true } : {}) };
    const ok = await run(() => entry.editing ? updateCatalogModel(entry.editing.name, body) : addCatalogModel({ ...body, name: entry.name.trim() }), "已保存。用它的 bot 下一轮生效。");
    if (ok) setEntry(null);
  }
  return <section className="agent-settings-card" aria-labelledby="model-catalog-title">
    <h2 id="model-catalog-title">{t("服务器模型目录")}</h2>
    <p>{t("bot 只引用这里的名字，自己不存 key。key 在服务器的环境变量、加密库或配置文件里；页面只显示「已配置」或「缺 key」，不会再显示 key。")}</p>
    {catalog?.config_file.present && <small>{t("服务器配置文件里的模型只能在服务器上改。")}{catalog.config_file.problems.length ? ` ${t("配置文件有问题")}：${catalog.config_file.problems.length}` : ""}</small>}
    {catalog && <ul className="catalog-list">{catalog.models.map(m => <li key={m.name}>
      <div><strong>{m.display_name || m.name}</strong>{m.is_default && <span className="bot-star"> ★ {t("默认")}</span>}<small>{m.name} · {m.provider} · {m.model}{m.key_source === "env" && m.key_env ? ` · ${t("环境变量")} ${m.key_env}` : ""}</small></div>
      <span className={`catalog-key ${m.key_status === "configured" ? "is-ready" : "is-missing"}`}>{t(keyStatusLabel(m))}</span>
      {m.origin === "server" ? <span className="catalog-actions">
        {!m.is_default && <button type="button" className="text-button" disabled={busy} onClick={() => void run(() => setDefaultCatalogModel(m.name), "已设为默认模型。")}>{t("设为默认")}</button>}
        <button type="button" className="text-button" disabled={busy} onClick={() => setEntry({ name: m.name, display_name: m.display_name, provider: m.provider, model: m.model, base_url: m.base_url ?? "", key_mode: m.key_source, api_key: "", key_env: m.key_env ?? "", is_default: m.is_default, editing: m })}>{t("编辑")}</button>
        <button type="button" className="text-button" disabled={busy} onClick={() => { if (window.confirm(`${t("删除模型")} ${m.name}？`)) void run(() => deleteCatalogModel(m.name), "已删除。"); }}>{t("删除")}</button>
      </span> : <small>{t("来自配置文件")}</small>}
    </li>)}{!catalog.models.length && <li><small>{t("目录是空的：先添加一个模型，bot 才能回答。")}</small></li>}</ul>}
    {!entry && <div className="chat-archive-tools"><button type="button" className="secondary-action" disabled={busy} onClick={() => setEntry(blankEntry())}>{t("＋ 添加模型")}</button></div>}
    {entry && <div className="bot-form">
      {!entry.editing && <label>{t("名字（小写字母、数字、._-）")}<input value={entry.name} maxLength={40} onChange={e => setEntry({ ...entry, name: e.target.value.toLowerCase() })} placeholder="main" /></label>}
      <label>{t("显示名称")}<input value={entry.display_name} maxLength={80} onChange={e => setEntry({ ...entry, display_name: e.target.value })} /></label>
      <label>{t("模型服务")}<select value={entry.provider} onChange={e => { const p = CATALOG.find(x => x.provider === e.target.value)!; setEntry({ ...entry, provider: p.provider, base_url: p.baseUrl }); }}>{CATALOG.map(x => <option key={x.provider} value={x.provider}>{t(x.label)}</option>)}</select></label>
      <label>{t("Base URL")}<input value={entry.base_url} maxLength={2048} autoComplete="url" onChange={e => setEntry({ ...entry, base_url: e.target.value })} /></label>
      <label>{t("Model ID")}<input value={entry.model} maxLength={160} onChange={e => setEntry({ ...entry, model: e.target.value })} placeholder={CATALOG.find(x => x.provider === entry.provider)?.suggestion} /></label>
      <label>{t("key 从哪来")}<select value={entry.key_mode} onChange={e => setEntry({ ...entry, key_mode: e.target.value as "vault" | "env" })}><option value="vault">{t("在这里填，加密存进服务器")}</option><option value="env">{t("读服务器的环境变量")}</option></select></label>
      {entry.key_mode === "vault" ? <label>{t("API key")}<input type="password" value={entry.api_key} maxLength={2048} autoComplete="new-password" onChange={e => setEntry({ ...entry, api_key: e.target.value })} placeholder={needsKey ? "" : t("不改就留空")} /></label>
        : <label>{t("环境变量名")}<input value={entry.key_env} maxLength={64} onChange={e => setEntry({ ...entry, key_env: e.target.value.toUpperCase() })} placeholder="DEEPSEEK_API_KEY" /></label>}
      {moved && <small className="bot-warn">{t("换了服务或接口地址，需要重新填写 key。")}</small>}
      <label className="bot-check"><input type="checkbox" checked={entry.is_default} onChange={e => setEntry({ ...entry, is_default: e.target.checked })} />{t("设为默认模型")}</label>
      <div className="chat-archive-tools"><button type="button" className="secondary-action" disabled={busy} onClick={() => setEntry(null)}>{t("取消")}</button>
        <button type="button" className="primary-action" disabled={busy || !entry.model.trim() || (!entry.editing && !entry.name.trim()) || (needsKey && !hasKey)} onClick={() => void saveEntry()}>{busy ? t("正在保存…") : t("保存")}</button></div>
    </div>}
    {status && <p role="status">{status}</p>}
  </section>;
}
