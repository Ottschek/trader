import { useState } from "react";
import { PageState, useJson } from "./ui.jsx";
import { shortDateTime } from "./health.js";

const KINDS = [["all", "All"], ["stock", "Companies"], ["coin", "Coins"], ["briefing", "Briefings"], ["news", "/news"]];

function stripHtml(text) {
  return String(text || "").replace(/<[^>]+>/g, "");
}

function Reports({ data }) {
  const [kind, setKind] = useState("all");
  const [open, setOpen] = useState(null);
  const reports = data.reports.filter((r) => kind === "all" || r.kind === kind);
  return (
    <>
      <section className="card">
        <div className="card-head"><h2>Telegram reports</h2><span className="muted">/analise and /news in the bot, plus the 08:00 and 22:30 briefings</span></div>
        <div className="chips" role="group" aria-label="Filter reports">
          {KINDS.map(([k, label]) => (
            <button key={k} type="button" className={`chip ${k === kind ? "chip-on" : ""}`} onClick={() => setKind(k)}>{label}</button>
          ))}
        </div>
        {reports.length === 0 && <p className="muted">No reports yet. Send /analise AAPL or /news to the bot.</p>}
        <div className="report-grid">
          {reports.map((r) => (
            <button key={r.id} type="button" className="report-item" onClick={() => setOpen(r)}>
              <img src={`/telegram/${r.id}.png`} alt={`${r.title} report`} loading="lazy" />
              <span className="report-title">{r.title}</span>
              <span className="muted small">{shortDateTime(r.created_at)}</span>
            </button>
          ))}
        </div>
      </section>
      {open && (
        <div className="report-overlay" role="dialog" aria-modal="true" aria-label={open.title} onClick={() => setOpen(null)}>
          <div className="report-full" onClick={(e) => e.stopPropagation()}>
            <div className="card-head"><h2>{open.title}</h2><button type="button" className="chip" onClick={() => setOpen(null)}>Close</button></div>
            <img src={`/telegram/${open.id}.png`} alt={`${open.title} report`} />
            <p className="small report-caption">{stripHtml(open.caption)}</p>
          </div>
        </div>
      )}
    </>
  );
}

export default function ReportsPage() {
  const state = useJson("/api/reports");
  return <PageState state={state}>{(data) => <Reports data={data} />}</PageState>;
}
