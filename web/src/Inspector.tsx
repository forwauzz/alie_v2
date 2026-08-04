import { useEffect, useState } from "react";
import type { Bullet, Row, SourceCrop, Why } from "./api";
import { api } from "./api";

/** The answer to "why does this row say this?" — the rule that fired and its epistemic
 *  tag, the source span, the resolved date decision (PRD §7.1). This is the trust
 *  surface, so it opens on demand beside the row rather than living on screen. */
export function Inspector({
  row,
  bullet,
  onClose,
  onCorrect,
}: {
  row: Row;
  bullet: Bullet | null;
  onClose: () => void;
  onCorrect: (unitId: string, value: string | null, previous: string | null) => Promise<void>;
}) {
  const [why, setWhy] = useState<Why | null>(null);
  const [crop, setCrop] = useState<SourceCrop | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unitId = row.unit_ids[0] ?? null;
  const blockId = bullet?.citation.block_id ?? null;

  useEffect(() => {
    if (!unitId) return;
    let live = true;
    api
      .why(unitId)
      .then((w) => {
        if (!live) return;
        setWhy(w);
        setDraft(w.row_date.value ?? "");
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [unitId]);

  useEffect(() => {
    if (!blockId) {
      setCrop(null);
      return;
    }
    let live = true;
    api.block(blockId).then((c) => live && setCrop(c)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [blockId]);

  async function save() {
    if (!unitId) return;
    setSaving(true);
    setError(null);
    try {
      await onCorrect(unitId, draft || null, why?.row_date.value ?? null);
      onClose();
    } catch (e) {
      setError(String(e));
      setSaving(false);
    }
  }

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="inspector" data-testid="inspector" data-unit-id={unitId ?? ""}>
        <header>
          <h2>{row.title}</h2>
          <span className="spacer" />
          <button onClick={onClose} aria-label="Fermer" data-testid="inspector-close">✕</button>
        </header>

        <div className="scroll">
          {error && <p className="error">{error}</p>}

          {why && (
            <div>
              <p className="reason" data-testid="why-date-explanation">
                <strong>{why.row_date.rendered}</strong> — {why.row_date.explanation}
                <span className="rule">règle {why.row_date.rule}</span>
              </p>
            </div>
          )}

          {bullet && crop && (
            <div>
              <div className="section-label">Source</div>
              <div className="quote" data-testid="source-text">
                {crop.text.slice(0, bullet.citation.start ?? 0)}
                <mark data-testid="source-span">
                  {crop.text.slice(bullet.citation.start ?? 0, bullet.citation.end ?? crop.text.length)}
                </mark>
                {crop.text.slice(bullet.citation.end ?? crop.text.length)}
              </div>
              <dl className="kv" style={{ marginTop: 10 }}>
                <dt>page imprimée</dt>
                <dd data-testid="crop-printed-label">{crop.printed_label ?? "aucune"}</dd>
                <dt>page pdf</dt>
                <dd data-testid="crop-pdf-index">{crop.pdf_index}</dd>
                <dt>bloc</dt>
                <dd>{crop.type} · {crop.source} · {crop.confidence.toFixed(2)}</dd>
              </dl>
              {crop.attrs.degenerate_number === "true" && (
                <p className="tag flag" style={{ display: "inline-block", marginTop: 8 }}>
                  Nombre malformé — vérifiez ce pourcentage
                </p>
              )}
            </div>
          )}

          {why && (
            <div>
              <div className="section-label">Dates trouvées</div>
              <table className="grid" data-testid="why-dates">
                <thead>
                  <tr><th>rôle</th><th>texte</th><th>lecture</th></tr>
                </thead>
                <tbody>
                  {why.dates_found.map((d, i) => (
                    <tr key={i} className={d.eligible ? "" : "struck"} data-testid="why-date">
                      <td>{d.role}</td>
                      <td>{d.raw}</td>
                      <td>{d.readings.join(" | ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="sub" style={{ marginTop: 6, fontSize: 12 }}>
                Les dates barrées ne peuvent jamais devenir la date de la ligne.
              </p>
            </div>
          )}

          {why && why.records.length > 0 && (
            <div>
              <div className="section-label">Champs lus sans modèle</div>
              <table className="grid" data-testid="why-records">
                <tbody>
                  {why.records.map((r, i) => (
                    <tr key={i}>
                      <td>{r.field}</td>
                      <td>{r.value}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {r.epistemic_tag && <span className="tag">{r.epistemic_tag}</span>}{" "}
                        {r.derived && <span className="tag muted">dérivé</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {why && (
            <div>
              <div className="section-label">Corriger</div>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="date"
                  value={draft}
                  data-testid="correction-input"
                  onChange={(e) => setDraft(e.target.value)}
                />
                <button className="primary" onClick={save} disabled={saving} data-testid="correction-save">
                  {saving ? "…" : "Corriger la date"}
                </button>
              </div>
              {/* Corrections write to the manifest, not the output (§10.2). */}
              <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>
                Écrit au manifeste, pas au tableau. La chronologie se régénère et la
                correction survit aux prochaines exécutions.
              </p>
              {why.corrections.length > 0 && (
                <ul className="locked" data-testid="why-corrections">
                  {why.corrections.map((c, i) => (
                    <li key={i}>{c.field} → {c.new_value} ({c.actor})</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {why && (
            <div>
              <div className="section-label">Unité</div>
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
                  {!why.contiguous && <span className="tag" style={{ marginLeft: 6 }}>non contiguë</span>}
                </dd>
                <dt>formulaire</dt>
                <dd>{why.form.serial ? `${why.form.serial} (${why.form.revision ?? "?"})` : "—"}</dd>
                <dt>lisibilité</dt>
                <dd>{why.legibility.level}</dd>
              </dl>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
