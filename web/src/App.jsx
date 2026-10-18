import { useEffect, useState } from "react";
import HealthPage from "./HealthPage.jsx";
import LabPage from "./LabPage.jsx";
import PaperPage from "./PaperPage.jsx";

const PAGES = [
  ["#/", "Data health", HealthPage],
  ["#/lab", "Strategy lab", LabPage],
  ["#/paper", "Paper", PaperPage],
];

function useHash() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const hash = useHash();
  const [, , Page] = PAGES.find(([h]) => h === hash) ?? PAGES[0];
  useEffect(() => {
    const [, label] = PAGES.find(([h]) => h === hash) ?? PAGES[0];
    document.title = `Trader · ${label}`;
  }, [hash]);
  return (
    <div className="page">
      <header className="header">
        <div className="brand">
          <span className="logo" aria-hidden="true" />
          <div className="brand-name">Trader</div>
        </div>
        <nav className="tabs" aria-label="Pages">
          {PAGES.map(([h, label]) => (
            <a key={h} href={h} className={`tab ${h === hash || (!PAGES.some(([x]) => x === hash) && h === "#/") ? "tab-on" : ""}`}
               aria-current={h === hash ? "page" : undefined}>{label}</a>
          ))}
        </nav>
      </header>
      <main><Page /></main>
    </div>
  );
}
