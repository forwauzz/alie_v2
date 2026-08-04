import type { Bullet, Row } from "./api";

/** Flagged items first, worst confidence at top (PRD §10.2), with undated rows leading
 *  the document so they are the first thing reviewed rather than the last thing
 *  discovered (§8.5). */
export function reviewOrder(rows: Row[]): Row[] {
  const rank = (r: Row) => {
    if (r.date_status === "undated" || r.date_status === "illegible") return 0;
    if (r.date_status === "ambiguous") return 1;
    if (r.warns || r.locators.some((l) => l.flagged)) return 2;
    return 3;
  };
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.confidence - b.confidence);
}

const STATUS_WORD: Record<string, string> = {
  undated: "à dater",
  illegible: "illisible",
  ambiguous: "date ambiguë",
  inferred: "siècle déduit",
  manual: "corrigé",
};

export function Chronology({
  rows,
  selectedId,
  onPick,
}: {
  rows: Row[];
  selectedId: string | null;
  onPick: (row: Row, bullet: Bullet | null) => void;
}) {
  const ordered = reviewOrder(rows);
  const toDate = rows.filter((r) => r.date_status === "undated").length;

  return (
    <div className="chrono" data-testid="chronology">
      {toDate > 0 && (
        <div className="band" data-testid="undated-heading">
          SANS DATE — {toDate} document{toDate > 1 ? "s" : ""} à dater
        </div>
      )}
      {ordered.map((row) => (
        <button
          key={row.id}
          className="entry"
          aria-selected={selectedId === row.id}
          data-testid="row"
          data-row-id={row.id}
          data-date-status={row.date_status}
          onClick={() => onPick(row, row.bullets[0] ?? null)}
        >
          <div>
            <div className="when" data-testid="row-date">
              {row.date ?? "—"}
              {row.date_status === "ambiguous" ? " (?)" : ""}
            </div>
            {/* Both page numbers exist; the printed one is what renders (§8.1). */}
            <div className="locator" data-testid="row-locator">
              {row.locators.map((l) => `${l.folder} p. ${l.display_page}`).join(" · ")}
            </div>
          </div>
          <div className="what">
            <div className="name" data-testid="row-title">{row.title}</div>
            {row.illegible_reason && (
              <div className="sub" style={{ marginTop: 4 }}>{row.illegible_reason}</div>
            )}
            {row.bullets.length > 0 && (
              <ul>
                {row.bullets.slice(0, 4).map((b, i) => (
                  <li key={i} data-testid="bullet">{b.text}</li>
                ))}
                {row.bullets.length > 4 && (
                  <li className="muted">+{row.bullets.length - 4} de plus</li>
                )}
              </ul>
            )}
            <div className="rowtags">
              {STATUS_WORD[row.date_status] && (
                <span className="tag flag" data-testid="row-status">
                  {STATUS_WORD[row.date_status]}
                </span>
              )}
              {row.warns && (
                <span className="tag warn" data-testid="row-confidence">
                  confiance {row.confidence.toFixed(2)}
                </span>
              )}
              {row.locators.length > 1 && (
                <span className="tag ok" title="Same document in two bundles — both locators kept">
                  {row.locators.length} sources
                </span>
              )}
              {row.locators.some((l) => l.flagged) && (
                <span className="tag" title="No page number printed; showing the PDF index">
                  page non imprimée
                </span>
              )}
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}
