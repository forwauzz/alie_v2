import { useEffect, useState } from "react";
import type { Bullet, Row, SourceCrop, Why } from "./api";
import { api } from "./api";

/** Flagged items first, worst confidence at top (PRD §10.2). Undated rows lead the
 *  document, so they are the first thing reviewed rather than the last thing
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

function locatorText(row: Row): string {
  return row.locators
    .map((l) => `${l.folder} p. ${l.display_page}${l.flagged ? " [pdf]" : ""}`)
    .join(" ; ");
}

export function Chronology({
  rows,
  selected,
  onSelect,
}: {
  rows: Row[];
  selected: string | null;
  onSelect: (row: Row, bullet: Bullet | null) => void;
}) {
  const ordered = reviewOrder(rows);
  const needsDating = rows.filter((r) => r.date_status === "undated").length;

  if (!rows.length) {
    return <p className="empty" data-testid="chronology-empty">No rows yet — approve a run.</p>;
  }

  return (
    <div data-testid="chronology">
      {needsDating > 0 && (
        <p className="undated-heading" data-testid="undated-heading">
          SANS DATE — {needsDating} document{needsDating > 1 ? "s" : ""} à dater
        </p>
      )}
      {ordered.map((row) => (
        <div
          key={row.id}
          className="row"
          role="button"
          tabIndex={0}
          aria-selected={selected === row.id}
          data-testid="row"
          data-row-id={row.id}
          data-date-status={row.date_status}
          onClick={() => onSelect(row, row.bullets[0] ?? null)}
          onKeyDown={(e) => e.key === "Enter" && onSelect(row, row.bullets[0] ?? null)}
        >
          <div className="head">
            <span className="date" data-testid="row-date">
              {row.date ?? "—"}
              {row.date_status === "ambiguous" ? " (?)" : ""}
            </span>
            <span className="title" data-testid="row-title">{row.title}</span>
            {row.warns && (
              <span className="chip warn" data-testid="row-confidence">
                confiance {row.confidence.toFixed(2)}
              </span>
            )}
            {row.date_status !== "resolved" && (
              <span className="chip flag" data-testid="row-status">{row.date_status}</span>
            )}
            {row.locators.length > 1 && (
              <span className="chip ok" title="Cross-bundle union — both locators retained">
                {row.locators.length} locators
              </span>
            )}
          </div>
          <div className="muted" style={{ fontSize: 12 }} data-testid="row-locator">
            {locatorText(row)}
          </div>
          {row.illegible_reason && (
            <div className="chip flag" style={{ marginTop: 6, display: "inline-block" }}>
              Illisible — {row.illegible_reason}
            </div>
          )}
          <ul>
            {row.bullets.map((b, i) => (
              <li
                key={i}
                data-testid="bullet"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(row, b);
                }}
              >
                {b.text}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function SourcePane({ bullet }: { bullet: Bullet | null }) {
  const [crop, setCrop] = useState<SourceCrop | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blockId = bullet?.citation.block_id ?? null;

  useEffect(() => {
    if (!blockId) {
      setCrop(null);
      return;
    }
    let live = true;
    api
      .block(blockId)
      .then((c) => live && (setCrop(c), setError(null)))
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [blockId]);

  if (error) return <p className="err">{error}</p>;
  if (!crop || !bullet) {
    return <p className="empty" data-testid="source-empty">Select a line to see its source.</p>;
  }

  const { start, end } = bullet.citation;
  const before = crop.text.slice(0, start ?? 0);
  const hit = crop.text.slice(start ?? 0, end ?? crop.text.length);
  const after = crop.text.slice(end ?? crop.text.length);

  return (
    <div data-testid="source-crop">
      <div className="crop" data-testid="source-text">
        {before}
        <mark data-testid="source-span">{hit}</mark>
        {after}
      </div>
      <dl className="kv" style={{ marginTop: 10 }}>
        <dt>page (imprimée)</dt>
        <dd data-testid="crop-printed-label">{crop.printed_label ?? "— none printed —"}</dd>
        {/* Both page numbers, on every page, always (§8.1). */}
        <dt>page (pdf)</dt>
        <dd data-testid="crop-pdf-index">{crop.pdf_index}</dd>
        <dt>bloc</dt>
        <dd>{crop.type}</dd>
        <dt>bbox</dt>
        <dd>{crop.bbox.map((n) => n.toFixed(1)).join(", ")}</dd>
        <dt>source</dt>
        <dd>{crop.source}</dd>
        <dt>confiance</dt>
        <dd>{crop.confidence.toFixed(2)}</dd>
      </dl>
      {crop.attrs.degenerate_number === "true" && (
        <p className="chip flag" style={{ display: "inline-block", marginTop: 8 }}>
          Nombre malformé — une erreur de barème est une erreur juridique
        </p>
      )}
    </div>
  );
}

export function WhyPanel({
  unitId,
  onCorrected,
}: {
  unitId: string | null;
  onCorrected: () => void;
}) {
  const [why, setWhy] = useState<Why | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!unitId) {
      setWhy(null);
      return;
    }
    let live = true;
    api
      .why(unitId)
      .then((w) => {
        if (!live) return;
        setWhy(w);
        setDraft(w.row_date.value ?? "");
        setError(null);
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [unitId]);

  if (error) return <p className="err">{error}</p>;
  if (!why) return <p className="empty" data-testid="why-empty">Select a row.</p>;

  async function saveDate() {
    if (!why) return;
    setSaving(true);
    try {
      await api.correct({
        subject_id: why.unit_id,
        field: "row_date",
        new_value: draft || null,
        old_value: why.row_date.value,
      });
      onCorrected();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div data-testid="why-panel" data-unit-id={why.unit_id}>
      <dl className="kv">
        <dt>classe</dt>
        <dd data-testid="why-class">
          {why.classification.label}{" "}
          <span className="muted">
            ({why.classification.confidence.toFixed(2)} · {why.classification.source})
          </span>
        </dd>
        <dt>pages</dt>
        <dd data-testid="why-pages">
          {why.pages.join(", ")}
          {!why.contiguous && <span className="chip" style={{ marginLeft: 6 }}>non contiguë</span>}
        </dd>
        <dt>formulaire</dt>
        <dd>{why.form.serial ? `${why.form.serial} (${why.form.revision ?? "?"})` : "—"}</dd>
        <dt>lisibilité</dt>
        <dd>{why.legibility.level}</dd>
      </dl>

      {/* The engine owes one line explaining why this date won (§8.4). */}
      <p className="explain" data-testid="why-date-explanation">
        <strong>{why.row_date.rendered}</strong> — {why.row_date.explanation}
        <br />
        <span className="muted">règle {why.row_date.rule}</span>
      </p>

      <h3 style={{ fontSize: 12, margin: "12px 0 4px" }}>Dates trouvées</h3>
      <table className="dates" data-testid="why-dates">
        <thead>
          <tr>
            <th>rôle</th>
            <th>texte</th>
            <th>lecture(s)</th>
            <th>p.</th>
          </tr>
        </thead>
        <tbody>
          {why.dates_found.map((d, i) => (
            <tr key={i} className={d.eligible ? "" : "ineligible"} data-testid="why-date">
              <td>{d.role}</td>
              <td>{d.raw}</td>
              <td>{d.readings.join(" | ")}</td>
              <td>{d.printed_label ?? d.pdf_index}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {why.records.length > 0 && (
        <>
          <h3 style={{ fontSize: 12, margin: "12px 0 4px" }}>Champs lus (4a)</h3>
          <table className="dates" data-testid="why-records">
            <tbody>
              {why.records.map((r, i) => (
                <tr key={i}>
                  <td>{r.field}</td>
                  <td>{r.value}</td>
                  <td>
                    {r.epistemic_tag && <span className="chip">{r.epistemic_tag}</span>}
                    {r.derived && <span className="chip muted">dérivé</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3 style={{ fontSize: 12, margin: "14px 0 4px" }}>Corriger la date</h3>
      {/* Corrections write to the manifest, not the output. The chronology regenerates
          on the next run; editing the output directly would be discarded (§10.2). */}
      <div style={{ display: "flex", gap: 6 }}>
        <input
          type="date"
          value={draft}
          data-testid="correction-input"
          onChange={(e) => setDraft(e.target.value)}
        />
        <button onClick={saveDate} disabled={saving} data-testid="correction-save">
          {saving ? "…" : "Corriger"}
        </button>
      </div>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        La correction est écrite au manifeste. Relancez pour régénérer la chronologie.
      </p>

      {why.corrections.length > 0 && (
        <ul className="muted" style={{ fontSize: 11 }} data-testid="why-corrections">
          {why.corrections.map((c, i) => (
            <li key={i}>
              {c.field} → {c.new_value} ({c.actor})
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
