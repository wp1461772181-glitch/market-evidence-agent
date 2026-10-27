import { useLocale } from "./i18n";

// Pages of the personal agent that later phases fill in.  Phase 1 gives them a place in the navigation
// and says plainly what will appear there; nothing here calls an API yet.

type Go = (section: "agent" | "briefs" | "skills" | "memory" | "admin" | "materials") => void;

export function BriefsPage({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  return <main className="personal-page" aria-labelledby="briefs-title">
    <h1 id="briefs-title">{t("简报")}</h1>
    <section className="personal-card">
      <span className="personal-badge">{t("即将推出")}</span>
      <h2>{t("还没有简报")}</h2>
      <p>{t("设好主动任务后，研究队列的每日简报、新申报和新闻提醒会出现在这里，每条都带来源编号；点进去就是普通对话，可以接着追问。")}</p>
    </section>
    <section className="personal-card">
      <h2>{t("待你确认")}</h2>
      <p>{t("后台任务只读不写。它想做的写操作会以确认卡的形式放在这里，你确认后才执行。")}</p>
    </section>
    <div><button className="primary-action" type="button" onClick={() => onNavigate("agent")}>{t("去对话")}</button></div>
  </main>;
}

const BUILTIN_SKILLS = [
  { name: "财报速读", text: "读最新一份 10-K 或 10-Q，列出收入、利润、现金流的变化和管理层提到的风险，每条带出处。" },
  { name: "同行对比", text: "把几家公司同一期的关键指标放在一张表里，数字都取自本轮读取的申报。" },
  { name: "新闻摘要及对研究队列的影响", text: "汇总近期新闻，标出和研究队列里哪家公司有关、可能影响什么。" },
] as const;

export function SkillsPage() {
  const { t } = useLocale();
  return <main className="personal-page" aria-labelledby="skills-title">
    <h1 id="skills-title">{t("技能")}</h1>
    <p className="fine-print">{t("技能是一份做事的步骤和允许用的工具清单，不是代码。技能只能缩小可用的工具，写操作仍然要你在确认卡上同意。")}</p>
    <div className="personal-grid">
      {BUILTIN_SKILLS.map(skill => <article className="personal-card" key={skill.name}>
        <span className="personal-badge">{t("内置 · 即将推出")}</span>
        <h2>{t(skill.name)}</h2>
        <p>{t(skill.text)}</p>
      </article>)}
    </div>
  </main>;
}

export function MemoryPage({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  return <main className="personal-page" aria-labelledby="memory-title">
    <h1 id="memory-title">{t("记忆")}</h1>
    <section className="personal-card">
      <h2>{t("画像")}</h2>
      <p>{t("对 Agent 说「记住……」「以后都……」，确认后它会在之后的每次对话里按你的偏好回答；说「忘掉……」即可撤回。Agent 也会从你自己说的话里推断偏好，标为“推断”，你可以在这里确认或删除。画像只是偏好，不是证据，也不授权任何操作。")}</p>
    </section>
    <section className="personal-card">
      <span className="personal-badge">{t("即将推出")}</span>
      <h2>{t("往事")}</h2>
      <p>{t("以后你说「上次我们聊过……」时，Agent 能找到是哪一次对话、聊了什么。往事只是线索，回答里的数字仍要重新取证。")}</p>
    </section>
    <div><button className="secondary-action" type="button" onClick={() => onNavigate("agent")}>{t("去对话")}</button></div>
  </main>;
}

export function MePage({ onNavigate }: { onNavigate: Go }) {
  const { t } = useLocale();
  const links: Array<{ id: Parameters<Go>[0]; label: string; hint: string }> = [
    { id: "memory", label: "记忆", hint: "画像和往事" },
    { id: "skills", label: "技能", hint: "做事的步骤" },
    { id: "materials", label: "资料库", hint: "证据与材料" },
    { id: "admin", label: "设置", hint: "模型与账户" },
  ];
  return <main className="personal-page" aria-labelledby="me-title">
    <h1 id="me-title">{t("我")}</h1>
    <nav className="personal-links" aria-label={t("我")}>
      {links.map(link => <button key={link.id} type="button" onClick={() => onNavigate(link.id)}>{t(link.label)}<small>{t(link.hint)}</small></button>)}
    </nav>
  </main>;
}
