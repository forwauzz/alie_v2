import type { Bullet, Plan, Row, Run, Validation } from "./api";
import { api } from "./api";
import { Chronology } from "./Chronology";

const STAGES = ["parse", "manifest", "structured", "assemble", "render"] as const;
const STAGE_WORD: Record<string, string> = {
  parse: "Lecture des pages",
  manifest: "Découpage en rapports",
  structured: "Lecture des formulaires",
  assemble: "Assemblage des lignes",
  render: "Rendu et vérification",
};

export function Said({ children }: { children: React.ReactNode }) {
  return (
    <div className="turn">
      <div className="mark" aria-hidden>A</div>
      <div className="said">{children}</div>
    </div>
  );
}

export function UserTurn({ text }: { text: string }) {
  return (
    <div className="turn user" data-testid="user-turn">
      <div className="bubble">{text}</div>
    </div>
  );
}

export function Note({ text, tone }: { text: string; tone?: "plain" | "error" }) {
  return (
    <Said>
      {tone === "error" ? <p className="error">{text}</p> : <p className="sub">{text}</p>}
    </Said>
  );
}

/** The plan is the manifest summary in readable form — the first moment a scoping error
 *  can be caught, before cost is spent (PRD §4.1). */
export function PlanTurn({
  plan,
  onRun,
  busy,
  callsModel,
}: {
  plan: Plan;
  onRun: () => void;
  busy: boolean;
  callsModel: boolean;
}) {
  const flagged = Object.entries(plan.flagged).filter(([, n]) => n > 0);
  const classes = Object.entries(plan.units_by_class);

  return (
    <Said>
      <p className="lede" data-testid="plan-summary">
        {plan.pages} page{plan.pages > 1 ? "s" : ""} dans {plan.bundles.length} verse
        {plan.bundles.length > 1 ? "ments" : "ment"}. J'ai lu {plan.units} rapport
        {plan.units > 1 ? "s" : ""}.
      </p>

      {classes.length > 0 && (
        <div className="facts">
          {classes.map(([label, n]) => (
            <span className="tag" key={label}>{n} × {label}</span>
          ))}
        </div>
      )}

      {flagged.length > 0 && (
        <>
          <p className="sub">Ce qui demandera votre œil :</p>
          <div className="facts">
            {flagged.map(([name, n]) => (
              <span className="tag flag" key={name} data-testid={`flag-${name}`}>{n} {name}</span>
            ))}
          </div>
        </>
      )}

      <p className="sub">
        Régime {plan.pack.toUpperCase()} · pack v{plan.pack_version} · estimation ≈{" "}
        {Math.max(1, Math.round(plan.estimate_seconds))} s.{" "}
        {callsModel ? (
          <span data-testid="model-notice">
            Un modèle choisira les lignes à transcrire. Il ne rédige rien : il désigne des
            passages, et le texte vient du document.
          </span>
        ) : (
          <span data-testid="model-notice">Aucun modèle n'est appelé.</span>
        )}
      </p>

      <div className="actions">
        <button className="primary" onClick={onRun} disabled={busy} data-testid="approve">
          {busy ? "En cours…" : "Produire la chronologie"}
        </button>
      </div>
    </Said>
  );
}

export function RunTurn({
  run,
  rows,
  validation,
  selectedId,
  onPick,
}: {
  run: Run | null;
  rows: Row[];
  validation: Validation | null;
  selectedId: string | null;
  onPick: (row: Row, bullet: Bullet | null) => void;
}) {
  if (!run) return null;
  const progress = run.stage_progress ?? {};
  const finished = run.status === "done";

  return (
    <Said>
      {!finished && (
        <div className="stages" data-testid="stages">
          {STAGES.map((stage) => {
            const counts = progress[stage] ?? {};
            const done = counts.done ?? 0;
            const total = Object.values(counts).reduce((a, b) => a + b, 0);
            const state = total === 0 ? "" : done === total ? "done" : "running";
            return (
              <div className={`stage ${state}`} key={stage} data-stage={stage} data-state={state}>
                <span className="dot">{state === "done" ? "✓" : state === "running" ? "•" : "·"}</span>
                <span>{STAGE_WORD[stage]}</span>
                {total > 1 && <span className="muted">{done}/{total}</span>}
              </div>
            );
          })}
        </div>
      )}

      {run.status === "failed" && <p className="error">La chaîne s'est arrêtée. Voir le journal.</p>}

      {finished && validation && (
        <>
          <p className="lede">
            {rows.length} ligne{rows.length > 1 ? "s" : ""}. Chaque chaîne de caractères est
            citée, et chaque page du versement est rattachée à un rapport.
          </p>
          <div className="facts">
            <span className={`tag ${validation.uncited === 0 ? "ok" : "flag"}`} data-testid="validation" data-passes={String(validation.passes)}>
              {validation.uncited} non cité
            </span>
            <span className={`tag ${validation.coverage === 1 ? "ok" : "flag"}`}>
              couverture {(validation.coverage * 100).toFixed(0)} %
            </span>
            {validation.rows_without_printed_label > 0 && (
              <span className="tag warn">
                {validation.rows_without_printed_label} sans page imprimée
              </span>
            )}
          </div>

          <div className="card">
            <header>
              <b>Chronologie médicale</b>
              <span className="spacer" />
              <a href={api.exportUrl(run.id)} data-testid="export-link">
                <button className="outline">Exporter</button>
              </a>
            </header>
            <Chronology rows={rows} selectedId={selectedId} onPick={onPick} />
          </div>

          <p className="sub">
            Cliquez une ligne pour voir d'où elle vient et pourquoi elle porte cette date.
          </p>
        </>
      )}
    </Said>
  );
}
