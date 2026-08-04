import { useEffect, useRef, useState } from "react";
import type { Bullet, Case, Row } from "./api";
import { api } from "./api";
import { Inspector } from "./Inspector";
import { Settings } from "./Settings";
import { Note, PlanTurn, RunTurn, UserTurn } from "./Turns";
import { useCase } from "./useCase";

export function App() {
  const [cases, setCases] = useState<Case[]>([]);
  const [caseId, setCaseId] = useState("");
  const [selected, setSelected] = useState<{ row: Row; bullet: Bullet | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const c = useCase(caseId);

  useEffect(() => {
    api
      .cases()
      .then((list) => {
        setCases(list);
        setCaseId((current) => current || list[0]?.id || "");
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [c.turns.length, c.rows.length, c.run?.status]);

  // The selected row is a snapshot; after a re-run its bullets are stale.
  useEffect(() => setSelected(null), [c.rows]);

  const active = cases.find((x) => x.id === caseId);

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <b>ALIE</b>
          <span>chronologie médico-légale</span>
        </div>
        <div className="group">Dossiers</div>
        <nav data-testid="case-list">
          {cases.map((x) => (
            <a
              key={x.id}
              className="case"
              aria-current={x.id === caseId}
              data-testid="case-item"
              data-case-name={x.name}
              onClick={() => setCaseId(x.id)}
            >
              {x.name}
            </a>
          ))}
        </nav>
        <div className="rail-foot">
          {active ? `${active.primary_pack.toUpperCase()} · un seul acteur, local` : "—"}
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <h1>{active?.name ?? "—"}</h1>
          <span className="spacer" />
          <Settings
            specs={c.flagSpecs}
            invariants={c.invariants}
            overrides={c.overrides}
            onChange={c.setOverrides}
          />
        </div>

        <div className="thread" data-testid="thread">
          <div className="thread-inner">
            {error && <p className="error">{error}</p>}

            {c.turns.length === 0 && !error && (
              <p className="empty" data-testid="thread-empty">Ouverture du dossier…</p>
            )}

            {c.turns.map((turn) => {
              if (turn.kind === "user") return <UserTurn key={turn.id} text={turn.text} />;
              if (turn.kind === "note") return <Note key={turn.id} text={turn.text} tone={turn.tone} />;
              if (turn.kind === "plan") {
                return (
                  <PlanTurn
                    key={turn.id}
                    plan={c.plan ?? turn.plan}
                    busy={c.busy}
                    callsModel={c.callsModel}
                    onRun={() => void c.start("Produisez la chronologie.")}
                  />
                );
              }
              return (
                <RunTurn
                  key={turn.id}
                  run={c.run?.id === turn.runId ? c.run : null}
                  rows={c.rows}
                  validation={c.validation}
                  selectedId={selected?.row.id ?? null}
                  onPick={(row, bullet) => setSelected({ row, bullet })}
                />
              );
            })}

            <div ref={bottom} />
          </div>
        </div>

        <Composer
          busy={c.busy}
          hasRun={Boolean(c.run)}
          onRun={(text) => void c.start(text)}
        />
      </main>

      {selected && (
        <Inspector
          row={selected.row}
          bullet={selected.bullet}
          onClose={() => setSelected(null)}
          onCorrect={c.correct}
        />
      )}
    </div>
  );
}

/** The composer offers the actions the engine can actually take. It does not pretend to
 *  understand free prose — an unrecognised request says so rather than guessing, because
 *  a scoping error that slips through here costs a whole run (§4.1). */
function Composer({
  busy,
  hasRun,
  onRun,
}: {
  busy: boolean;
  hasRun: boolean;
  onRun: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const [rejected, setRejected] = useState<string | null>(null);

  const suggestions = hasRun
    ? ["Relancer la chronologie", "Qu'est-ce qui reste à vérifier ?"]
    : ["Produire la chronologie"];

  function submit(value: string) {
    const t = value.trim();
    if (!t || busy) return;
    setText("");
    if (/chronolog|relanc|produi|générer|generer|refai/i.test(t)) {
      setRejected(null);
      onRun(t);
      return;
    }
    setRejected(t);
  }

  return (
    <div className="composer">
      <div className="composer-inner">
        {rejected && (
          <p className="sub" data-testid="composer-rejected">
            Je ne sais pas encore faire « {rejected} ». Ce que je peux faire :{" "}
            produire ou relancer la chronologie, et enregistrer une correction depuis une ligne.
          </p>
        )}
        <div className="suggestions">
          {suggestions.map((s) => (
            <button key={s} onClick={() => submit(s)} disabled={busy} data-testid="suggestion">
              {s}
            </button>
          ))}
        </div>
        <div className="box">
          <textarea
            rows={1}
            value={text}
            placeholder="Demandez une chronologie, ou corrigez une ligne…"
            data-testid="composer-input"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(text);
              }
            }}
          />
          <button
            className="primary"
            onClick={() => submit(text)}
            disabled={busy || !text.trim()}
            data-testid="composer-send"
          >
            {busy ? <span className="spin">◐</span> : "Envoyer"}
          </button>
        </div>
        <p className="hint">
          Les corrections vont au manifeste, jamais au tableau. Aucun modèle n'est appelé.
        </p>
      </div>
    </div>
  );
}
