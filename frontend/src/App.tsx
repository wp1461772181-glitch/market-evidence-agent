import { AgentAdminPage, AgentChatPanel, AgentChatProviderRoot } from "./AgentChatPanel";
import { Tx, formatDateTime as formatLocalizedDateTime, getLocalePreference, translatePhrase, useLocale } from "./i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, getFilingInventory, getJevLearningStatus, getMaterialLibrary, getV2Evaluations, getV2ForecastRoots, getV2MonitorStatus, getV2Prices, getV2Workspace, initializeMarketData, scanOfficialFilings } from "./api";
import type { FilingInventory, JevLearningStatus, PriceHistory } from "./types";
import { V2ForecastWorkspace } from "./V2ForecastWorkspace";
import { EvidenceCenter } from "./EvidenceCenter";
import { MaterialLibrary } from "./MaterialLibrary";
import { HistoricalReplayWorkspace } from "./HistoricalReplayWorkspace";
import { normalizeTicker, normalizeWatchlist, selectionForWatchlist, MAX_WATCHLIST } from "./watchlist.mjs";
import { getChatMode, setChatMode, personalChatLogin, getResearchQueue, putResearchQueue } from "./api";
import { registerWatchlistAdder, setWatchlistSnapshot } from "./watchlist-bridge.mjs";
import { chatSessionKey } from "./chat-session.mjs";
import { completePersonalLogin } from "./chat-mode-switch.mjs";
import { BriefsPage, MemoryPage, MePage, SkillsPage } from "./PersonalPages";
import { formatDelta } from "./delta.mjs";
import { applyTheme, saveTheme, storedTheme, type ThemeChoice } from "./theme.mjs";

const DEFAULT_SYMBOL = "AAPL";
const DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"];
const WATCHLIST_KEY = "market-evidence-agent:watchlist:v1";
type SupportedSymbol = string;
type FilingState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; data: FilingInventory } | { kind: "error"; message: string };
type WorkspaceSection = "overview" | "forecast" | "evidence" | "materials" | "revisions" | "evaluation" | "replay" | "agent" | "admin" | "briefs" | "skills" | "memory" | "me";
type ResearchSection = Exclude<WorkspaceSection, "agent" | "admin" | "briefs" | "skills" | "memory" | "me">;
// The personal-agent navigation groups the older sections: 公司 holds 总览 and the 预测 tab (预测, 修订,
// 到期评估, 历史回放); 资料库 holds 材料库 and 证据.  The older hashes stay the addresses, so links inside
// the older pages (#materials, #evidence/<id>/…) and the agent's open_page keep working unchanged.
type NavGroup = "chat" | "briefs" | "company" | "library" | "skills" | "memory" | "settings";
const FORECAST_TABS = ["forecast", "revisions", "evaluation", "replay"] as const;
const LIBRARY_TABS = ["materials", "evidence"] as const;
const HASH_ALIASES: Record<string, WorkspaceSection> = { chat: "agent", company: "overview", library: "materials", settings: "admin" };
function navGroup(section: WorkspaceSection): NavGroup {
  if (section === "agent") return "chat";
  if (section === "admin") return "settings";
  if (section === "briefs" || section === "skills" || section === "memory") return section;
  if (section === "me") return "memory";
  if ((LIBRARY_TABS as readonly string[]).includes(section)) return "library";
  return "company";
}
function isResearchSection(section: WorkspaceSection): section is ResearchSection { const group = navGroup(section); return group === "company" || group === "library"; }
const WORKSPACE_SECTION_TITLE: Record<ResearchSection, string> = {
  overview: "研究总览", forecast: "预测工作台", evidence: "证据中心", materials: "材料库", revisions: "预测修订", evaluation: "到期评估", replay: "历史回放",
};

export function App() {
  return <AppEntry />;
}

function AppEntry(){
  const {t}=useLocale();
  const [mode,setMode]=useState(()=>localStorage.getItem("market-evidence-agent:chat-mode:v1")?getChatMode():null),[code,setCode]=useState(""),[error,setError]=useState("");
  async function login(){try{const credential=await personalChatLogin(code);setChatMode("personal",credential);window.location.hash="agent";setMode("personal")}catch{setError("个人登录暂不可用，请检查登录码后重试。")}}
  if(mode)return <AgentChatProviderRoot key={chatSessionKey(mode,localStorage.getItem(mode==="personal"?"market-evidence-agent:personal-chat-session:v1":"market-evidence-agent:chat-session:v1"))}><WorkspaceApp key={mode} onModeChange={setMode}/></AgentChatProviderRoot>;
  return <main className="entry-choice"><p className="eyebrow">Market Evidence Agent</p><h1>{t("选择聊天模式")}</h1><p>{t("访客与个人聊天记录保存在服务端 PostgreSQL。登录个人账户后即可进行站点操作。")}</p><div><button className="primary-action" onClick={()=>{setChatMode("guest");window.location.hash="agent";setMode("guest")}}>{t("继续访客聊天")}</button><label>{t("个人聊天登录码")}<input type="password" value={code} maxLength={256} autoComplete="current-password" onChange={e=>setCode(e.target.value)}/></label><button className="secondary-action" disabled={!code} onClick={()=>void login()}>{t("登录个人聊天")}</button>{error&&<p role="alert">{error}</p>}</div></main>;
}

function WorkspaceApp({onModeChange}:{onModeChange:(mode:"guest"|"personal")=>void}) {
  const [symbols, setSymbols] = useState<string[]>(() => { try { return normalizeWatchlist(JSON.parse(localStorage.getItem(WATCHLIST_KEY) ?? "null"), DEFAULT_SYMBOLS); } catch { return DEFAULT_SYMBOLS; } });
  const [requestedSymbol, setRequestedSymbol] = useState(() => { try { return normalizeTicker(localStorage.getItem("market-evidence-agent:selected-symbol:v1")) ?? DEFAULT_SYMBOL; } catch { return DEFAULT_SYMBOL; } });
  const [watchInput, setWatchInput] = useState("");
  const [watchError, setWatchError] = useState("");
  const [activeSection, setActiveSection] = useState<WorkspaceSection>(sectionFromHash());
  const [activeData, setActiveData] = useState<{ kind: "loading"; symbol: string } | { kind: "ready"; symbol: string; filings: FilingState; priceHistory: PriceHistory | null; priceError: string | null }>({ kind: "loading", symbol: DEFAULT_SYMBOL });
  const [retryKey, setRetryKey] = useState(0);
  const requestVersion = useRef(0);
  const activeSymbolRef = useRef(requestedSymbol);
  activeSymbolRef.current = requestedSymbol;
  useEffect(() => { try { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(symbols)); } catch { /* optional preference */ } }, [symbols]);
  // The queue the chat request carries, and the add the agent's "not in your research queue" card uses.
  useEffect(() => { setWatchlistSnapshot(symbols); }, [symbols]);
  useEffect(() => registerWatchlistAdder((raw: string) => {
    const ticker = normalizeTicker(raw);
    if (ticker) setSymbols(items => items.includes(ticker) || items.length >= MAX_WATCHLIST ? items : [...items, ticker]);
  }), []);
  useEffect(() => {
    const receive = (event: Event) => {
      const action = (event as CustomEvent).detail;
      if (action.action === "queue_add") setSymbols(items => items.includes(action.symbol) || items.length >= MAX_WATCHLIST ? items : [...items, action.symbol]);
      if (action.action === "queue_remove") setSymbols(items => items.filter(item => item !== action.symbol));
      if (action.action === "queue_sync") setSymbols(action.symbols);
      if (action.action === "select_stock") setRequestedSymbol(action.symbol);
      if (action.action === "open_page") window.location.hash = ({predictions:"forecast",sec:"evidence",knowledge:"admin"} as Record<string,string>)[action.page] ?? action.page;
    };
    window.addEventListener("market-evidence-agent:client-action", receive);
    return () => window.removeEventListener("market-evidence-agent:client-action", receive);
  }, []);
  // The owner's queue lives in the database (clearing the browser must not clear it); a guest's stays in the
  // browser.  The browser copy is the cache: it shows at once, and the server's copy replaces it when read.
  const personal = getChatMode() === "personal";
  const [queueReady, setQueueReady] = useState(!personal);
  const lastSavedQueue = useRef("");
  useEffect(() => {
    if (!personal) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const saved = await getResearchQueue(controller.signal);
        if (controller.signal.aborted) return;
        if (saved === null) {
          // First time: the list this browser holds becomes the owner's.
          lastSavedQueue.current = JSON.stringify(symbols);
          await putResearchQueue(symbols, controller.signal);
        } else {
          const clean = [...new Set(saved.map(normalizeTicker).filter((item): item is string => Boolean(item)))].slice(0, MAX_WATCHLIST);
          lastSavedQueue.current = JSON.stringify(clean);
          setSymbols(clean);
        }
      } catch { /* offline, or the server copy is not readable: the browser copy stays */ }
      finally { if (!controller.signal.aborted) setQueueReady(true); }
    })();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personal]);
  useEffect(() => {
    if (!personal || !queueReady) return;
    const body = JSON.stringify(symbols);
    if (body === lastSavedQueue.current) return;
    lastSavedQueue.current = body;
    void putResearchQueue(symbols).catch(() => { /* the browser copy still holds it */ });
  }, [symbols, personal, queueReady]);
  useEffect(() => { if (symbols.length) setRequestedSymbol(current => selectionForWatchlist(current, symbols)); }, [symbols]);
  useEffect(() => { try { localStorage.setItem("market-evidence-agent:selected-symbol:v1", requestedSymbol); } catch { /* optional preference */ } }, [requestedSymbol]);

  useEffect(() => {
    const onHashChange = () => setActiveSection(sectionFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function navigate(section: WorkspaceSection) {
    if (section === activeSection) {
      if (section === "evidence" && window.location.hash !== "#evidence") window.location.hash = "evidence";
      return;
    }
    window.location.hash = section;
    setActiveSection(section);
  }

  function openStock(ticker: SupportedSymbol) {
    if (ticker === requestedSymbol) setRetryKey((value) => value + 1);
    else setRequestedSymbol(ticker);
    navigate("overview");
  }

  useEffect(() => {
    const controller = new AbortController();
    const version = ++requestVersion.current;
    setActiveData({ kind: "loading", symbol: requestedSymbol });
    Promise.allSettled([
      getFilingInventory(requestedSymbol, controller.signal),
      getV2Prices(requestedSymbol, controller.signal),
    ]).then(([filingResult, pricesResult]) => {
      if (controller.signal.aborted || version !== requestVersion.current) return;
      const nextFilings: FilingState = filingResult.status === "fulfilled"
        ? { kind: "ready", data: filingResult.value }
        : { kind: "error", message: actionMessage(filingResult.reason, "暂时无法读取官方申报清单。") };
      const priceHistory = pricesResult.status === "fulfilled" ? pricesResult.value.price_history : null;
      const priceError = pricesResult.status === "rejected" ? actionMessage(pricesResult.reason, "行情暂不可用。") : null;
      setActiveData({ kind: "ready", symbol: requestedSymbol, filings: nextFilings, priceHistory, priceError });
    });
    return () => controller.abort();
  }, [requestedSymbol, retryKey]);

  function addWatchSymbol() { const ticker = normalizeTicker(watchInput); if (!ticker) { setWatchError("请输入有效的美股代码，例如 AMD 或 BRK.B。"); return; } if (symbols.includes(ticker)) { setWatchError("该代码已在关注列表中。"); return; } if (symbols.length >= MAX_WATCHLIST) { setWatchError("关注列表最多保存 30 个代码。"); return; } setSymbols(items => [...items, ticker]); setWatchInput(""); setWatchError(""); openStock(ticker); }
  function removeWatchSymbol(ticker: string) { const remaining = symbols.filter(item => item !== ticker); setSymbols(remaining); setRequestedSymbol(current => selectionForWatchlist(current, remaining)); }
  const shellProps = { activeSection, navigate, symbol: requestedSymbol, symbols, onSelectStock: openStock, watchInput, setWatchInput, addWatchSymbol, watchError, setWatchError, onModeChange };
  if (activeSection === "agent") return <Shell {...shellProps}><AgentChatPanel onSettings={() => navigate("admin")} onModeChange={onModeChange} /></Shell>;
  if (activeSection === "admin") return <Shell {...shellProps}><AgentAdminPage onBack={() => navigate("agent")} /></Shell>;
  if (activeSection === "briefs") return <Shell {...shellProps}><BriefsPage onNavigate={navigate} /></Shell>;
  if (activeSection === "skills") return <Shell {...shellProps}><SkillsPage /></Shell>;
  if (activeSection === "memory") return <Shell {...shellProps}><MemoryPage onNavigate={navigate} /></Shell>;
  if (activeSection === "me") return <Shell {...shellProps}><MePage onNavigate={navigate} /></Shell>;

  async function startFilingScan() {
    const scanSymbol = requestedSymbol;
    if (!isSupportedSymbol(scanSymbol)) throw new Error("SEC 扫描需要有效的美股代码。");
    const result = await scanOfficialFilings(scanSymbol);
    if (activeSymbolRef.current === scanSymbol) {
      setActiveData((previous) => previous.kind === "ready" && previous.symbol === scanSymbol ? { ...previous, filings: { kind: "ready", data: result } } : previous);
    }
    return result;
  }

  if (activeSection === "materials") return <Shell {...shellProps}><SectionTabs section={activeSection} navigate={navigate} /><MaterialLibrary key={requestedSymbol} symbol={requestedSymbol} onScan={async () => { const result = await startFilingScan(); setRetryKey((value) => value + 1); return result; }} /></Shell>;
  if (!symbols.length) return <Shell {...shellProps}><main className="workspace"><section className="empty-watchlist"><h1>{translatePhrase("关注列表为空")}</h1><p>{translatePhrase("添加一只股票，或恢复默认关注股票，继续查看研究总览。")}</p><button className="primary-action" onClick={() => { setSymbols(DEFAULT_SYMBOLS); setRequestedSymbol(DEFAULT_SYMBOL); }}>{translatePhrase("恢复默认股票")}</button></section></main></Shell>;
  if (activeData.kind === "loading" || activeData.symbol !== requestedSymbol) return <Shell {...shellProps}><Loading symbol={requestedSymbol} /></Shell>;
  const supported = isSupportedSymbol(requestedSymbol);
  const revisionsNavigate = activeSection === "revisions" ? "revisions" : "forecast";
  return <Shell {...shellProps}>
    <main className="workspace" aria-labelledby="workspace-title">
      <WorkspacePageHeader symbol={requestedSymbol} section={activeSection as ResearchSection} recordCount={null} supported={supported} />
      <SectionTabs section={activeSection} navigate={navigate} />
      {activeSection === "overview" && <CurrentOverviewWorkspace symbols={symbols} onRemove={removeWatchSymbol} symbol={requestedSymbol} currentFilings={activeData.filings} priceHistory={activeData.priceHistory} priceError={activeData.priceError} onNavigate={navigate} onOpenStock={openStock} onPricesInitialized={() => setRetryKey(value => value + 1)} />}
      {(activeSection === "forecast" || activeSection === "revisions") && <section className="workspace-stack">
        <V2ForecastWorkspace key={`${requestedSymbol}-${revisionsNavigate}`} mode={revisionsNavigate} symbol={requestedSymbol} filings={activeData.filings.kind === "ready" ? activeData.filings.data : null} priceHistory={activeData.priceHistory} onOpenForecast={() => navigate("forecast")} />
        {activeData.priceError && <p className="inventory-status inventory-error" role="status">行情暂不可用：{activeData.priceError}</p>}
      </section>}
      {activeSection === "evidence" && <EvidenceCenter key={requestedSymbol} symbol={requestedSymbol} supported={supported} onOpenForecast={() => navigate("forecast")} />}
      {activeSection === "evaluation" && <V2EvaluationWorkspace key={requestedSymbol} symbol={requestedSymbol} />}
      {activeSection === "replay" && <HistoricalReplayWorkspace key={requestedSymbol} initialSymbol={requestedSymbol} symbols={symbols} />}
    </main>
  </Shell>;
}

function CurrentOverviewWorkspace({ symbols, onRemove, symbol, currentFilings, priceHistory, priceError, onNavigate, onOpenStock, onPricesInitialized }: { symbols: string[]; onRemove: (ticker: string) => void; symbol: string; currentFilings: FilingState; priceHistory: PriceHistory | null; priceError: string | null; onNavigate: (section: WorkspaceSection) => void; onOpenStock: (ticker: SupportedSymbol) => void; onPricesInitialized: () => void }) {
  const { t } = useLocale();
  type UniverseItem = { workspace?: Awaited<ReturnType<typeof getV2Workspace>>; workspaceState: "loading" | "ready" | "error"; roots?: Awaited<ReturnType<typeof getV2ForecastRoots>>; materials?: number; filings?: FilingInventory; error?: string; loading: boolean };
  const [universe, setUniverse] = useState<Record<string, UniverseItem>>(() => Object.fromEntries(symbols.map((ticker) => [ticker, { workspaceState: "loading", loading: true }])));
  const [monitor, setMonitor] = useState<{ kind: "loading" } | { kind: "ready"; data: Awaited<ReturnType<typeof getV2MonitorStatus>> } | { kind: "error" }>({ kind: "loading" });
  const [priceSeedState, setPriceSeedState] = useState("");
  async function initializePrices() { if (priceSeedState === "正在初始化行情…") return; setPriceSeedState("正在初始化行情…"); try { const result = await initializeMarketData(symbol); setPriceSeedState(result.inserted_count ? "行情初始化成功，新增行情已自动刷新。" : "初始化完成，但供应商没有返回可保存的行情。"); onPricesInitialized(); } catch (error) { setPriceSeedState(actionMessage(error, "行情初始化失败，请登录个人账户后重试。")); } }
  useEffect(() => {
    const controller = new AbortController();
    setMonitor({ kind: "loading" });
    setUniverse(Object.fromEntries(symbols.map((ticker) => [ticker, { workspaceState: "loading", loading: true }])));
    Promise.all(symbols.map(async (ticker) => {
      const [workspace, roots, materials, filings] = await Promise.allSettled([
        getV2Workspace(ticker, controller.signal), getV2ForecastRoots(ticker, controller.signal),
        getMaterialLibrary(ticker, { limit: 1, signal: controller.signal }), getFilingInventory(ticker, controller.signal),
      ]);
      if (controller.signal.aborted) return [ticker, { workspaceState: "loading" as const, loading: false }] as const;
      const firstError = [workspace, roots, materials, filings].find((value) => value.status === "rejected");
      return [ticker, {
        ...(workspace.status === "fulfilled" ? { workspace: workspace.value } : {}),
        workspaceState: workspace.status === "fulfilled" ? "ready" as const : "error" as const,
        ...(roots.status === "fulfilled" ? { roots: roots.value } : {}),
        ...(materials.status === "fulfilled" ? { materials: materials.value.total } : {}),
        ...(filings.status === "fulfilled" ? { filings: filings.value } : {}),
        ...(firstError ? { error: actionMessage(firstError.reason, "暂时无法读取该股票的研究状态。") } : {}), loading: false,
      }] as const;
    })).then((entries) => { if (!controller.signal.aborted) setUniverse(Object.fromEntries(entries)); });
    getV2MonitorStatus(controller.signal).then((data) => { if (!controller.signal.aborted) setMonitor({ kind: "ready", data }); }).catch(() => { if (!controller.signal.aborted) setMonitor({ kind: "error" }); });
    return () => controller.abort();
  }, [symbol, symbols.join(",")]);
  const current = universe[symbol];
  const currentWorkspaceState = current?.workspaceState ?? "loading";
  const currentVersion = currentWorkspaceState === "ready" ? current?.workspace?.current_version : null;
  const currentFilingsList = currentFilings.kind === "ready" ? currentFilings.data.filings : [];
  const officialFilingCount = currentFilings.kind === "ready" ? currentFilingsList.length : null;
  return <section className="workspace-stack overview-workspace">
    <WorkspaceIntro eyebrow={t("研究总览")} title={t("从单股研究队列开始")} description={t("队列汇总独立预测、官方资料和已保存材料。没有预测时仍可上传材料、读取 SEC 原文并开始新研究。")} />
    <section className="overview-focus" aria-label={`${symbol} ${t("当前研究状态")}`}>
      <div><p className="eyebrow">{t("当前股票")} / {symbol}</p><h2>{currentWorkspaceState === "loading" ? t("正在读取研究状态") : currentWorkspaceState === "error" ? t("研究状态暂不可用") : currentVersion ? `${t("预测版本")} ${currentVersion.version_no}` : t("可开始一项新研究")}</h2><p>{currentWorkspaceState === "loading" ? t("正在读取当前预测与研究记录。") : currentWorkspaceState === "error" ? t("当前预测状态读取失败，请稍后重试。") : currentVersion ? `${t("最近决策时间")} ${formatUtc(currentVersion.decision_at)}; ${t("选择预测区查看简报、固定目标和行情。")}` : t("目前没有当前预测版本。历史行情与材料仍可查看，也可以直接创建预测。")}</p></div>
      <div className="overview-focus-actions"><button className="primary-action" disabled={currentWorkspaceState === "loading"} onClick={() => onNavigate("forecast")}>{currentWorkspaceState === "loading" ? t("正在读取…") : currentVersion ? t("打开预测") : currentWorkspaceState === "ready" ? t("创建预测") : t("查看预测工作台")}</button><button className="secondary-action" onClick={() => onNavigate("materials")}>{<Tx text={"查看材料库"} />}</button></div>
    </section>
    <section className="overview-metrics" aria-label={t("当前研究状态")}>
      <article><span>{<Tx text={"独立预测数"} />}</span><strong>{current?.roots?.roots.length ?? "—"}</strong><small>{current?.workspace?.pending_job_count ? `${current.workspace.pending_job_count} ${t("项任务处理中")}` : t("按当前预测记录统计")}</small></article>
      <article><span>{<Tx text={"材料总数"} />}</span><strong>{current?.materials ?? "—"}</strong><small>{<Tx text={"已保存官方和上传材料"} />}</small></article>
      <article><span>{<Tx text={"SEC 官方材料"} />}</span><strong>{officialFilingCount ?? "—"}</strong><small>{currentFilings.kind === "error" ? t("官方资料目录暂不可用") : t("当前股票已保存的官方文件")}</small></article>
      <article><span>{<Tx text={"监控状态"} />}</span><strong>{monitor.kind === "loading" ? t("正在读取") : monitor.kind === "error" ? t("暂不可用") : t(monitorHealthLabel(monitor.data.health))}</strong><small>{monitor.kind === "loading" ? t("正在读取监控记录") : monitor.kind === "error" ? t("无法读取监控状态") : monitor.data.last_success?.completed_at ? `${t("最近成功")} ${formatUtc(monitor.data.last_success.completed_at)}` : t("尚无成功运行记录")}</small></article>
    </section>
    <section className="universe-section" aria-labelledby="universe-title"><div className="section-heading"><div><p className="eyebrow">{<Tx text={"研究队列"} />}</p><h2 id="universe-title">{<Tx text={"自选股票"} />}</h2></div><span>{<Tx text={"按当前研究数据读取"} />}</span></div><div className="universe-grid">
      {symbols.map((ticker) => {
        const item = universe[ticker];
        const count = item?.roots?.roots.length;
        const filingCount = item?.filings?.filings.length;
        return <article className={`universe-card ${ticker === symbol ? "is-current" : ""}`} key={ticker}>
          <div className="universe-card-top"><strong>{ticker}</strong>{ticker === symbol && <span>{<Tx text={"当前"} />}</span>}</div>
          {item?.loading && <p className="universe-pending">{<Tx text={"正在读取研究状态…"} />}</p>}
          {item?.error && <p className="universe-error" role="status">{item.error}</p>}
          {item && !item.loading && <><dl><div><dt>{<Tx text={"独立预测"} />}</dt><dd>{count ?? "—"}</dd></div><div><dt>{<Tx text={"材料"} />}</dt><dd>{item.materials ?? "—"}</dd></div><div><dt>{<Tx text={"SEC 官方材料"} />}</dt><dd>{filingCount ?? "—"}</dd></div></dl><small>{item.workspaceState === "loading" ? t("正在读取预测状态") : item.workspaceState === "error" ? t("预测状态暂不可用") : item.workspace?.current_version ? `${t("最新版本")} ${item.workspace.current_version.version_no} · ${t(modelStatusLabel(item.workspace.current_version.model_status))}` : t("当前没有预测版本")}</small></>}
          <div className="watch-card-actions"><button className="universe-open" type="button" onClick={() => onOpenStock(ticker)}>{t("打开")} {ticker} {t("总览")} <span aria-hidden="true">→</span></button><button className="watch-remove" type="button" onClick={() => onRemove(ticker)} aria-label={`${t("从关注列表移除")} ${ticker}`}>×</button></div>
        </article>;
      })}
    </div></section>
    <div className="overview-shortcuts"><button className="text-button" onClick={() => onNavigate("materials")}>{<Tx text={"管理材料 / 扫描 SEC"} />}</button><button className="text-button" onClick={() => onNavigate("evaluation")}>{<Tx text={"查看到期评估"} />}</button><span>{priceError ? `${t("行情暂不可用")}: ${priceError}` : priceHistory?.latest_trading_date ? `${symbol} ${t("行情截至")} ${priceHistory.latest_trading_date}` : t("本地没有可展示的行情")}</span><button className="text-button" type="button" onClick={initializePrices} disabled={priceSeedState === "正在初始化行情…"}>{t("初始化一年行情")}</button>{priceSeedState && <small role="status">{t(priceSeedState)}</small>}</div>
  </section>;
}

function V2EvaluationWorkspace({ symbol }: { symbol: string }) {
  const { t } = useLocale();
  const [state, setState] = useState<{ kind: "loading" } | { kind: "ready"; data: Awaited<ReturnType<typeof getV2Evaluations>> } | { kind: "error"; message: string }>({ kind: "loading" });
  const [learning, setLearning] = useState<{ kind: "loading" } | { kind: "ready"; data: JevLearningStatus } | { kind: "error"; message: string }>({ kind: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    setLearning({ kind: "loading" });
    getV2Evaluations(symbol, controller.signal).then((data) => { if (!controller.signal.aborted) setState({ kind: "ready", data }); }).catch((error: unknown) => { if (!controller.signal.aborted) setState({ kind: "error", message: actionMessage(error, "暂时无法读取到期评估。") }); });
    getJevLearningStatus(symbol, controller.signal).then((data) => { if (!controller.signal.aborted) setLearning({ kind: "ready", data }); }).catch((error: unknown) => { if (!controller.signal.aborted) setLearning({ kind: "error", message: actionMessage(error, "暂时无法读取 Jev 学习状态。") }); });
    return () => controller.abort();
  }, [symbol]);
  return <section className="workspace-stack">
    <WorkspaceIntro eyebrow="到期评估" title="按模型分组查看已到期预测" description="每组只统计同一时间模式、模型、服务方和问题版本的代表版本。未到期样本显示为待评，不混入其他模型的指标。" />
    <JevLearningReadout state={learning} />
    {state.kind === "loading" && <p className="inventory-status">{<Tx text={"正在读取评估结果…"} />}</p>}
    {state.kind === "error" && <p className="inventory-status inventory-error" role="alert">{state.message}</p>}
    {state.kind === "ready" && <div className="model-cohort-list">{state.data.model_cohorts?.length ? state.data.model_cohorts.map((cohort, index) => {
      const versions = cohort.roots.flatMap((root) => root.versions);
      const scored = versions.map((version) => version.latest_evaluation).filter((evaluation) => evaluation?.status === "succeeded");
      const average = (values: Array<number | null | undefined>) => { const valid = values.filter((value): value is number => typeof value === "number"); return valid.length ? (valid.reduce((sum, value) => sum + value, 0) / valid.length).toFixed(3) : "—"; };
      return <article className="model-cohort-card" key={`${cohort.model_status}-${cohort.provider}-${cohort.actual_model}-${cohort.question_version ?? "none"}-${index}`}>
        <div className="section-heading"><div><p className="eyebrow">{t(cohort.time_mode === "observed" ? "真实观察" : cohort.time_mode === "historical_research" ? "历史研究" : "时间模式未分类")} · {t(modelStatusLabel(cohort.model_status))}</p><h2>{cohort.provider} / {cohort.actual_model}</h2></div><span>{t(cohort.status === "available" ? "指标可用" : cohort.status === "pending" ? "等待到期" : "样本不足")}</span></div>
        <p className="fine-print">{t("问题版本")}: {cohort.question_version ?? t("未记录")} · {t("样本按同组最高版本计数")}</p>
        <div className="overview-metrics"><article><span>{<Tx text={"独立预测样本"} />}</span><strong>{cohort.sample.root_denominator}</strong><small>{<Tx text={"同组代表记录"} />}</small></article><article><span>{<Tx text={"已标注 / 已评分"} />}</span><strong>{cohort.sample.labelled_root_count} / {cohort.sample.scored_root_count}</strong><small>{cohort.sample.unscored_root_count} {t("条尚未评分")}</small></article><article><span>{<Tx text={"平均 Brier"} />}</span><strong>{average(scored.map((value) => value?.brier_score))}</strong><small>{<Tx text={"仅已成功评分的版本"} />}</small></article><article><span>{<Tx text={"平均 Log loss"} />}</span><strong>{average(scored.map((value) => value?.log_loss))}</strong><small>{<Tx text={"仅已成功评分的版本"} />}</small></article></div>
      </article>;
    }) : <div className="evaluation-empty-state"><h2>{state.data.model_cohorts ? t("还没有模型分组评估") : t("模型分组数据暂不可用")}</h2><p>{<Tx text={"这里不会用旧版离线报告或跨模型混合分组填补。预测到期并获得可用行情后，结果会进入对应模型组。"} />}</p></div>}</div>}
  </section>;
}

function JevLearningReadout({ state }: { state: { kind: "loading" } | { kind: "ready"; data: JevLearningStatus } | { kind: "error"; message: string } }) {
  const { locale, t } = useLocale();
  if (state.kind === "loading") return <section className="jev-learning-card"><p className="eyebrow">{<Tx text={"Jev 本地校准"} />}</p><p>{<Tx text={"正在读取学习状态…"} />}</p></section>;
  if (state.kind === "error") return <section className="jev-learning-card"><p className="eyebrow">{<Tx text={"Jev 本地校准"} />}</p><p className="inventory-error" role="alert">{state.message}</p></section>;
  const status = state.data;
  const cohorts = status.cohorts;
  const current = cohorts[0] ?? null;
  const active = current?.active_model ?? null;
  const metrics = active?.test_metrics && typeof active.test_metrics === "object" ? active.test_metrics as Record<string, unknown> : null;
  const rawMetrics = metrics?.raw_jev && typeof metrics.raw_jev === "object" ? metrics.raw_jev as Record<string, unknown> : null;
  const correctedMetrics = metrics?.candidate && typeof metrics.candidate === "object" ? metrics.candidate as Record<string, unknown> : null;
  const priorMetrics = metrics?.training_prior_baseline && typeof metrics.training_prior_baseline === "object" ? metrics.training_prior_baseline as Record<string, unknown> : null;
  const metric = (value: unknown) => typeof value === "number" ? value.toFixed(3) : "—";
  return <section className="jev-learning-card" aria-label={t("Jev 本地校准器学习状态")}>
    <div className="section-heading"><div><p className="eyebrow">{<Tx text={"Jev 本地校准"} />}</p><h2>{active ? t("已启用经过时间外验证的校准器") : t("正在积累可训练的真实预测结果")}</h2></div><span>{active ? t("已启用") : t("收集样本中")}</span></div>
    <p>{current ? `${current.actual_model} · ${t("问题版本")} ${current.question_version}` : t("当前股票还没有可统计的 Jev 预测组。")} {t("校准器只学习如何调整 Jev 的三类概率，不修改 Jev 本身，也不声称改变它的内部参数。")}</p>
    <div className="overview-metrics jev-learning-metrics">
      <article><span>{<Tx text={"当前预测组"} />}</span><strong>{current?.forecast_roots ?? 0}</strong><small>{<Tx text={"独立根预测"} />}</small></article>
      <article><span>{<Tx text={"已到期并评分"} />}</span><strong>{current?.matured_roots ?? 0} / {status.required_mature_roots}</strong><small>{locale === "en-US" ? `At least ${status.minimum_validation_months} months of out-of-time validation` : `至少 ${status.minimum_validation_months} 个月时间外验证`}</small></article>
      <article><span>{<Tx text={"待到期"} />}</span><strong>{current?.pending_roots ?? 0}</strong><small>{<Tx text={"预测目标为 20 个交易日"} />}</small></article>
      <article><span>{<Tx text={"历史回放排除"} />}</span><strong>{status.historical_replay_mature_roots_excluded}</strong><small>{<Tx text={"不进入实时校准训练"} />}</small></article>
    </div>
    {active && <div className="jev-learning-validation"><strong>{<Tx text={"最近校准器的封存时间外结果"} />}</strong><span>{t("原始 Jev Brier")} {metric(rawMetrics?.brier)} → {t("校准后")} {metric(correctedMetrics?.brier)}</span><span>{t("训练期类别基线 Brier")} {metric(priorMetrics?.brier)} → {t("校准后")} {metric(correctedMetrics?.brier)}</span><span>{t("原始 Log loss")} {metric(rawMetrics?.log_loss)} → {t("校准后")} {metric(correctedMetrics?.log_loss)}</span><small>{<Tx text={"启用条件：按月份分块的 95% 置信区间显示 Brier 同时优于 Jev 和训练期类别基线，且 Log loss 没有明显退化。新的成熟结果会进入下一轮验证。"} />}</small></div>}
    {!active && <p className="jev-learning-note">{<Tx text={"校准器目前没有启用，所以新预测继续显示原始 Jev 概率。只有足够的真实观察结果通过时间顺序留出验证后才会自动启用；历史回放和同一目标的修订不会混入训练。"} />}</p>}
  </section>;
}
function WorkspacePageHeader({ symbol, section, recordCount, supported }: { symbol: string; section: ResearchSection; recordCount: number | null; supported: boolean }) {
  const { t } = useLocale();
  const title = t(WORKSPACE_SECTION_TITLE[section]);
  return <header className="workspace-heading">
    <div><p className="eyebrow">{symbol} / {title}</p><h1 id="workspace-title">{title}</h1></div>
    <div className="workspace-context"><span className={`live-dot ${supported ? "" : "muted"}`} aria-hidden="true" /><span>{supported ? t("支持新建研究") : t("可查看已有研究")}</span>{recordCount !== null && <span className="workspace-record-count">{recordCount} {t("项独立预测")}</span>}</div>
  </header>;
}

function WorkspaceIntro({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  const { t } = useLocale();
  return <section className="workspace-intro">
    <div><p className="eyebrow">{t(eyebrow)}</p><h2>{t(title)}</h2><p>{t(description)}</p></div>
    {action && <div className="workspace-intro-action">{action}</div>}
  </section>;
}

function Shell({ activeSection, navigate, symbol, symbols, onSelectStock, watchInput, setWatchInput, addWatchSymbol, watchError, setWatchError, onModeChange, children }: { activeSection: WorkspaceSection; navigate: (section: WorkspaceSection) => void; symbol: string; symbols: string[]; onSelectStock: (ticker: SupportedSymbol) => void; watchInput: string; setWatchInput: (value: string) => void; addWatchSymbol: () => void; watchError: string; setWatchError: (value: string) => void; onModeChange:(mode:"guest"|"personal")=>void; children: ReactNode }) {
  const { locale, setLocale, t } = useLocale();
  const navRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [menuOpen,setMenuOpen]=useState(false),[pinned,setPinned]=useState(()=>localStorage.getItem("market-evidence-agent:nav-pinned:v1")==="1");
  const [isNarrow,setIsNarrow]=useState(()=>window.matchMedia("(max-width: 760px)").matches);
  const effectivePinned=pinned&&!isNarrow;
  const [ownerDialog,setOwnerDialog]=useState(false),[ownerInput,setOwnerInput]=useState(""),[ownerError,setOwnerError]=useState(""),[ownerBusy,setOwnerBusy]=useState(false);
  const ownerDialogRef=useRef<HTMLElement>(null),ownerInputRef=useRef<HTMLInputElement>(null),ownerReturnFocus=useRef<HTMLElement|null>(null);
  useEffect(()=>{if(!ownerDialog)return;ownerReturnFocus.current=document.activeElement instanceof HTMLElement?document.activeElement:null;const background=Array.from(document.querySelectorAll<HTMLElement>(".app-frame > :not(.app-content), .app-content > :not(.owner-auth-backdrop)"));const prior=background.map(el=>el.inert);background.forEach(el=>{el.inert=true});ownerInputRef.current?.focus();const trap=(event:KeyboardEvent)=>{if(event.key!=="Tab")return;const items=ownerDialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),[href],select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])');if(!items?.length)return;const first=items[0],last=items[items.length-1];if(event.shiftKey&&(document.activeElement===first||!ownerDialogRef.current?.contains(document.activeElement))){event.preventDefault();last.focus()}else if(!event.shiftKey&&(document.activeElement===last||!ownerDialogRef.current?.contains(document.activeElement))){event.preventDefault();first.focus()}};document.addEventListener("keydown",trap,true);return()=>{document.removeEventListener("keydown",trap,true);background.forEach((el,i)=>{el.inert=prior[i]});ownerReturnFocus.current?.focus()}},[ownerDialog]);
  const menuRegion=useRef<HTMLDivElement>(null);
  const unpin=()=>{setPinned(false);localStorage.removeItem("market-evidence-agent:nav-pinned:v1");setMenuOpen(false)};
  useEffect(()=>{const media=window.matchMedia("(max-width: 760px)"),change=()=>setIsNarrow(media.matches);change();media.addEventListener("change",change);return()=>media.removeEventListener("change",change)},[]);
  useEffect(()=>{const insideNav=(node:EventTarget|null)=>Boolean(menuRegion.current?.contains(node as Node)||triggerRef.current?.contains(node as Node));const key=(e:KeyboardEvent)=>{if(e.key!=="Escape")return;if(ownerDialog){setOwnerDialog(false);return}if(!menuOpen&&!pinned)return;if(isNarrow||!pinned)setMenuOpen(false);else unpin();window.requestAnimationFrame(()=>triggerRef.current?.focus())};const outside=(e:PointerEvent)=>{if((isNarrow||!pinned)&&!insideNav(e.target))setMenuOpen(false)};const auth=()=>{setOwnerInput("");setOwnerError("");setOwnerDialog(true)};document.addEventListener("keydown",key);document.addEventListener("pointerdown",outside);window.addEventListener("owner-auth-required",auth);return()=>{document.removeEventListener("keydown",key);document.removeEventListener("pointerdown",outside);window.removeEventListener("owner-auth-required",auth)}},[pinned,isNarrow,ownerDialog,menuOpen]);
  const drawerNavigate=(section:WorkspaceSection)=>{if(isNarrow||!pinned)setMenuOpen(false);navigate(section)};
  const navFocusRequested=useRef(false);
  const focusFirstNavItem=()=>{navFocusRequested.current=true};
  useEffect(()=>{if(!navFocusRequested.current||!(menuOpen||effectivePinned))return;navFocusRequested.current=false;navRef.current?.querySelector<HTMLButtonElement>("button")?.focus()},[menuOpen,effectivePinned]);
  const closeNavDrawer=()=>{if(isNarrow||!pinned)setMenuOpen(false);else unpin();window.requestAnimationFrame(()=>triggerRef.current?.focus())};
  const toggleNavDrawer=()=>{if(isNarrow){setMenuOpen(open=>{const next=!open;if(next)focusFirstNavItem();return next});return}if(pinned){unpin();window.requestAnimationFrame(()=>triggerRef.current?.focus());return}setPinned(true);localStorage.setItem("market-evidence-agent:nav-pinned:v1","1");setMenuOpen(true);focusFirstNavItem()};
  useEffect(() => {
    if (activeSection === "admin") return;
    const navElement = navRef.current;
    const activeItem = navElement?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!navElement || !activeItem) return;
    const navRect = navElement.getBoundingClientRect();
    const itemRect = activeItem.getBoundingClientRect();
    if (itemRect.left < navRect.left) navElement.scrollLeft -= navRect.left - itemRect.left;
    else if (itemRect.right > navRect.right) navElement.scrollLeft += itemRect.right - navRect.right;
  }, [activeSection]);
  const nav: Array<{ id: WorkspaceSection; group: NavGroup; label: string; hint: string; icon: string }> = [
    { id: "agent", group: "chat", label: t("对话"), hint: t("首页"), icon: "✳" },
    { id: "briefs", group: "briefs", label: t("简报"), hint: t("收件箱"), icon: "✉" },
    { id: "overview", group: "company", label: t("公司"), hint: t("总览与预测"), icon: "◌" },
    { id: "materials", group: "library", label: t("资料库"), hint: t("证据与材料"), icon: "▤" },
    { id: "skills", group: "skills", label: t("技能"), hint: t("做事的步骤"), icon: "⌁" },
    { id: "memory", group: "memory", label: t("记忆"), hint: t("画像和往事"), icon: "◇" },
    { id: "admin", group: "settings", label: t("设置"), hint: t("模型与账户"), icon: "⚙" },
  ];
  const mobileTabs: Array<{ id: WorkspaceSection; label: string; icon: string; active: boolean }> = [
    { id: "agent", label: t("对话"), icon: "✳", active: activeSection === "agent" },
    { id: "briefs", label: t("简报"), icon: "✉", active: activeSection === "briefs" },
    { id: "overview", label: t("公司"), icon: "◌", active: navGroup(activeSection) === "company" },
    { id: "me", label: t("我"), icon: "☺", active: ["me", "memory", "skills", "admin", "materials", "evidence"].includes(activeSection) },
  ];
  const research = isResearchSection(activeSection);
  const [theme, setTheme] = useState<ThemeChoice>(() => storedTheme());
  return <div className={`app-frame${effectivePinned?" is-nav-pinned":""}`}>
    <div className="nav-drawer-region" ref={menuRegion}>
      {isNarrow&&menuOpen&&<div className="nav-scrim" aria-hidden="true" onClick={()=>setMenuOpen(false)}/>}
      <aside id="workspace-navigation" className={`app-rail ${menuOpen||effectivePinned?"is-open":""}`} aria-label={t("主要导航")}>
        <button className="rail-nav-close" type="button" aria-label={t("收起导航")} aria-controls="workspace-navigation" onClick={closeNavDrawer}><span aria-hidden="true">×</span></button>
      <a className="workspace-brand" href="#agent" onClick={() => navigate("agent")} aria-label={`Market Evidence Agent · ${t("对话")}`}><img className="brand-logo" src={`${import.meta.env.BASE_URL}logo.svg`} width="32" height="32" alt="" /><span>market<br /><em>evidence</em></span></a>
      <div className="rail-context"><span>{!research ? t("工作区") : <Tx text={"当前股票"} />}</span><strong>{!research ? t("Agent") : symbol}</strong><small>{<Tx text={"Agent 研究工作台"} />}</small></div>
      <nav ref={navRef} className="workspace-nav">
        {nav.map((item) => <button key={item.id} type="button" className={navGroup(activeSection) === item.group ? "active" : ""} aria-current={navGroup(activeSection) === item.group ? "page" : undefined} onClick={() => drawerNavigate(item.id)}><span aria-hidden="true">{item.icon}</span><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>)}
      </nav>
      <div className="rail-bottom">{research && <span><span className="rail-review-dot" aria-hidden="true" />{<Tx text={"判断可回到原始来源查看"} />}</span>}</div>
      </aside>
    </div>
    <div className="app-content">
      <header className="topbar">
        <div className="topbar-lead">
          <button ref={triggerRef} className="nav-menu-trigger" type="button" aria-label={t(isNarrow?(menuOpen?"收起导航":"打开导航菜单"):(pinned?"收起导航":"打开导航菜单"))} aria-expanded={isNarrow?menuOpen:menuOpen||effectivePinned} aria-controls="workspace-navigation" onClick={toggleNavDrawer}>☰</button>
          {research && <div className="topbar-path"><span>{<Tx text={"研究工作台"} />}</span><b>/</b><strong>{symbol}</strong></div>}
        </div>
        <div className="toolbar-controls">
        {research && <div className="symbol-form">
          <label htmlFor="symbol">{<Tx text={"切换股票"} />}</label>
          <select id="symbol" value={symbol} onChange={(event) => onSelectStock(event.target.value as SupportedSymbol)} aria-label={t("切换股票")}>
            {!symbols.includes(symbol) && <option value={symbol}>{symbol} · {t("当前")}</option>}
            {symbols.map((ticker) => <option key={ticker} value={ticker}>{ticker}</option>)}
          </select>
        </div>}
        {research && <form className="watchlist-add" onSubmit={event => { event.preventDefault(); addWatchSymbol(); }}><label className="visually-hidden" htmlFor="watch-add">{t("添加股票")}</label><input id="watch-add" value={watchInput} onChange={event => { setWatchInput(event.target.value); setWatchError(""); }} placeholder={t("添加代码，如 AMD")} maxLength={10} /><button type="submit" aria-label={t("添加股票")}>+</button>{watchError && <span role="alert">{t(watchError)}</span>}</form>}
        <div className="language-form theme-form">
          <label htmlFor="theme">{t("外观")}</label>
          <select id="theme" value={theme} onChange={(event) => { const choice = saveTheme(event.target.value as ThemeChoice); setTheme(choice); applyTheme(choice); }} aria-label={t("外观")}>
            <option value="light">{t("浅色")}</option>
            <option value="dark">{t("深色")}</option>
            <option value="system">{t("跟随系统")}</option>
          </select>
        </div>
        <div className="language-form">
          <label htmlFor="locale">{t("语言")}</label>
          <select id="locale" value={locale} onChange={(event) => setLocale(event.target.value as "zh-CN" | "en-US")} aria-label={t("语言")}>
            <option value="zh-CN">中文</option>
            <option value="en-US">English</option>
          </select>
        </div>
        </div>
      </header>
      {children}
      {ownerDialog&&<div className="owner-auth-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setOwnerDialog(false)}}><section ref={ownerDialogRef} className="owner-auth-dialog" role="dialog" aria-modal="true" aria-labelledby="owner-auth-title"><button className="owner-auth-close" type="button" aria-label={t("关闭")} onClick={()=>setOwnerDialog(false)}>×</button><p className="eyebrow">{t("站点写入操作")}</p><h2 id="owner-auth-title">{t("请登录个人账户")}</h2><p>{t("登录后可继续进行站点操作。登录完成后，请手动重试刚才未完成的操作。")}</p><label>{t("个人登录密码或口令")}<input ref={ownerInputRef} type="password" value={ownerInput} autoComplete="current-password" onChange={e=>setOwnerInput(e.target.value)}/></label>{ownerError&&<p role="alert">{ownerError}</p>}<div><button className="primary-action" disabled={!ownerInput||ownerBusy} onClick={()=>{setOwnerBusy(true);void personalChatLogin(ownerInput).then(credential=>{completePersonalLogin(credential,setChatMode,onModeChange);setOwnerDialog(false);setOwnerInput("")}).catch(error=>setOwnerError(error instanceof Error?error.message:t("登录暂不可用。"))).finally(()=>setOwnerBusy(false))}}>{ownerBusy?t("正在登录…"):t("登录并授权")}</button><button className="secondary-action" onClick={()=>setOwnerDialog(false)}>{t("稍后")}</button></div></section></div>}
      <footer>{<Tx text={"Market Evidence Agent · 本地研究记录 · 研究判断可追溯到原始来源"} />}</footer>
      <nav className="mobile-tabbar" aria-label={t("主要导航")}>
        {mobileTabs.map(tab => <button key={tab.id} type="button" aria-current={tab.active ? "page" : undefined} onClick={() => navigate(tab.id)}><span aria-hidden="true">{tab.icon}</span>{tab.label}</button>)}
      </nav>
    </div>
  </div>;
}

function Loading({ symbol }: { symbol: string }) {
  const { t } = useLocale();
  return <main className="state-card" aria-live="polite" aria-busy="true"><span className="loading-mark" aria-hidden="true" /><p className="kicker">{<Tx text={"研究工作台"} />}</p><h1>{t("正在读取")} {symbol} {t("的研究资料")}</h1><p>{<Tx text={"正在读取本地预测、SEC 目录和行情。"} />}</p></main>;
}

function SectionTabs({ section, navigate }: { section: WorkspaceSection; navigate: (section: WorkspaceSection) => void }) {
  const { t } = useLocale();
  const group = navGroup(section);
  const forecastTab = (FORECAST_TABS as readonly string[]).includes(section);
  const labels: Record<string, string> = { overview: "总览", forecast: "预测", revisions: "预测修订", evaluation: "到期评估", replay: "历史回放", materials: "材料库", evidence: "证据" };
  const tab = (id: WorkspaceSection, active: boolean, label = labels[id]) => <button key={id} type="button" aria-current={active ? "page" : undefined} onClick={() => navigate(id)}>{t(label)}</button>;
  if (group === "library") return <nav className="section-tabs" aria-label={t("资料库")}>{LIBRARY_TABS.map(id => tab(id, section === id))}</nav>;
  if (group !== "company") return null;
  return <>
    <nav className="section-tabs" aria-label={t("公司")}>{tab("overview", section === "overview")}{tab("forecast", forecastTab)}</nav>
    {forecastTab && <nav className="section-tabs is-secondary" aria-label={t("预测")}>{FORECAST_TABS.map(id => tab(id, section === id, id === "forecast" ? "预测工作台" : labels[id]))}</nav>}
  </>;
}

function isSupportedSymbol(symbol: string): symbol is SupportedSymbol { return isValidSymbol(symbol); }
function isValidSymbol(symbol: string): boolean { return /^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$/i.test(symbol.trim()); }
function sectionFromHash(): WorkspaceSection {
  const value = window.location.hash.replace(/^#/, "").split("/", 1)[0];
  if ((["overview", "forecast", "evidence", "materials", "revisions", "evaluation", "replay", "agent", "admin", "briefs", "skills", "memory", "me"] as const).includes(value as WorkspaceSection)) return value as WorkspaceSection;
  // The conversation is the home page.
  return HASH_ALIASES[value] ?? "agent";
}
function actionMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return translatePhrase(fallback);
  if (error.message.includes("SEC_EDGAR_USER_AGENT")) return translatePhrase("尚未配置 SEC 联系邮箱。请先按 README 配置后重试。");
  return translatePhrase(error.message);
}
function formatPrice(value: number) { return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value); }
function formatReturn(value: number) { return formatDelta(value, { scale: 100, suffix: "%" }); }
function formatPercent(value: number) { return `${(value * 100).toFixed(1)}%`; }
function modelStatusLabel(status: string) {
  if (status === "research_only") return translatePhrase("研究结果（未输出概率）");
  if (status === "experimental_jev") return translatePhrase("Jev 实验模型");
  if (status === "experimental_joint") return translatePhrase("联合研究模型");
  if (status === "baseline_only") return translatePhrase("基线模型");
  return translatePhrase("模型状态未记录");
}
function monitorHealthLabel(status: string) {
  if (status === "healthy") return translatePhrase("监控正常");
  if (status === "delayed") return translatePhrase("监控延迟");
  if (status === "degraded") return translatePhrase("监控异常");
  return translatePhrase("尚无记录");
}
function signedPoints(value: number) { return formatDelta(value, { scale: 100, suffix: "pp" }); }
function probabilityLabel(key: "bearish" | "neutral" | "bullish") { return translatePhrase(({ bearish: "看跌", neutral: "中性", bullish: "看涨" })[key]); }
function formatDateTime(value: string, locale: "zh-CN" | "en-US" = getLocalePreference()) { return formatLocalizedDateTime(value, locale); }
function formatUtc(value: string) { return formatDateTime(value); }
