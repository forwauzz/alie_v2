import { useCallback, useEffect, useRef, useState } from "react";
import type { Bullet, Case, FlagSpec, Plan, Row, Validation } from "./api";
import { api } from "./api";
import { Chronology, SourcePane, WhyPanel } from "./panes";

const STEPS = ["Upload", "Plan", "Approve", "Run", "Review", "Export"] as const;
type Step = (typeof STEPS)[number];

export function App() {
  const [cases, setCases] = useState<Case[]>([]);
  const [caseId, setCaseId] = useState<string>("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [flagSpecs, setFlagSpecs] = useState<FlagSpec[]>([]);
  const [invariants, setInvariants] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [runId, setRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<string>("");
  const [rows, setRows] = useState<Row[]>([]);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [selectedRow, setSelectedRow] = useState<Row | null>(null);
  const [selectedBullet, setSelectedBullet] = useState<Bullet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const poll = useRef<number | null>(null);

  const step: Step = !caseId
    ? "Upload"
    : !runId
      ? "Plan"
      : runStatus !== "done"
        ? "Run"
        : rows.length
          ? "Review"
          : "Approve";

  useEffect(() => {
    api
      .cases()
      .then((c) => {
        setCases(c);
        if (c.length && !caseId) setCaseId(c[0].id);
      })
      .catch((e) => setError(String(e)));
    api
      .flags()
      .then((f) => {
        setFlagSpecs(f.flags);
        setInvariants(f.safety_invariants);
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!caseId) return;
    setRunId(null);
    setRows([]);
    setSelectedRow(null);
    setSelectedBullet(null);
    api.plan(caseId).then(setPlan).catch((e) => setError(String(e)));
  }, [caseId]);

  const loadRows = useCallback(async (id: string) => {
    const payload = await api.rows(id);
    setRows(payload.rows);
    setValidation(payload.validation);
  }, []);

  // The user may close the tab; progress is per stage (§4.1). Polling stops as soon as
  // the run reaches a terminal state.
  useEffect(() => {
    if (!runId) return;
    const tick = async () => {
      try {
        const run = await api.run(runId);
        setRunStatus(run.status);
        if (["done", "failed", "superseded"].includes(run.status)) {
          if (poll.current) window.clearInterval(poll.current);
          poll.current = null;
          if (run.status === "done") await loadRows(runId);
        }
      } catch (e) {
        setError(String(e));
      }
    };
    void tick();
    poll.current = window.setInterval(tick, 400);
    return () => {
      if (poll.current) window.clearInterval(poll.current);
      poll.current = null;
    };
  }, [runId, loadRows]);

  async function approve() {
    if (!caseId) return;
    setError(null);
    setRows([]);
    try {
      const run = await api.createRun(caseId, overrides);
      setRunId(run.id);
      setRunStatus(run.status);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <>
      <header className="app">
        <h1>ALIE</h1>
        <span className="sub">
          {plan ? `${plan.pack} v${plan.pack_version}` : "chronologie médico-légale"}
        </span>
        <span className="spacer" />
        {validation && (
          <span
            className={`chip ${validation.passes ? "ok" : "flag"}`}
            data-testid="validation"
            data-passes={String(validation.passes)}
          >
            uncited {validation.uncited} · coverage{" "}
            {(validation.coverage * 100).toFixed(0)}%
          </span>
        )}
        {runId && runStatus === "done" && (
          <a href={api.exportUrl(runId)} data-testid="export-link">
            <button>Exporter .md</button>
          </a>
        )}
      </header>

      <nav className="journey" data-testid="journey">
        {STEPS.map((s) => (
          <span key={s} className={`step${s === step ? " active" : ""}`} data-step={s}>
            {s}
          </span>
        ))}
      </nav>

      <div className="toolbar">
        <label className="muted">Dossier</label>
        <select
          value={caseId}
          data-testid="case-picker"
          onChange={(e) => setCaseId(e.target.value)}
        >
          {cases.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <button className="primary" onClick={approve} data-testid="approve">
          Approuver et lancer
        </button>
        {runId && (
          <span className="chip" data-testid="run-status" data-status={runStatus}>
            {runStatus}
          </span>
        )}
      </div>

      {error && <p className="err" data-testid="error">{error}</p>}

      {plan && (
        <div className="toolbar" data-testid="plan">
          {/* The plan is the manifest summary in readable form — the first moment a
              scoping error can be caught, before cost is spent (§4.1). */}
          <strong data-testid="plan-summary">{plan.summary}</strong>
        </div>
      )}

      <div className="panes">
        <section className="pane">
          <h2>
            <span>Chronologie</span>
            <span className="muted">{rows.length} lignes</span>
          </h2>
          <div className="body">
            <Chronology
              rows={rows}
              selected={selectedRow?.id ?? null}
              onSelect={(row, bullet) => {
                setSelectedRow(row);
                setSelectedBullet(bullet);
              }}
            />
          </div>
        </section>

        <section className="pane">
          <h2>Source</h2>
          <div className="body">
            <SourcePane bullet={selectedBullet} />
          </div>
        </section>

        <section className="pane">
          <h2>Pourquoi</h2>
          <div className="body">
            <WhyPanel
              unitId={selectedRow?.unit_ids[0] ?? null}
              onCorrected={() => void approve()}
            />
            <FlagRegister specs={flagSpecs} overrides={overrides} onToggle={setOverrides} />
            <h3 style={{ fontSize: 12, margin: "14px 0 4px" }}>Invariants de sûreté</h3>
            <ul className="muted" style={{ fontSize: 11, paddingLeft: 16 }} data-testid="invariants">
              {invariants.map((inv) => (
                <li key={inv}>{inv}</li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </>
  );
}

/** Every flag states the question it answers and the metric that judges it (§9.2). */
function FlagRegister({
  specs,
  overrides,
  onToggle,
}: {
  specs: FlagSpec[];
  overrides: Record<string, boolean>;
  onToggle: (next: Record<string, boolean>) => void;
}) {
  if (!specs.length) return null;
  return (
    <>
      <h3 style={{ fontSize: 12, margin: "16px 0 4px" }}>Drapeaux</h3>
      <div data-testid="flag-register">
        {specs.map((spec) => {
          const value = overrides[spec.id] ?? spec.default === true;
          return (
            <label className="flagrow" key={spec.id} data-testid="flag" data-flag-id={spec.id}>
              <input
                type="checkbox"
                checked={value}
                onChange={(e) => onToggle({ ...overrides, [spec.id]: e.target.checked })}
              />
              <span className="meta">
                <code>{spec.id}</code>{" "}
                {spec.requires_rerun && (
                  <span className="chip warn" title="Invalidates derived work">
                    re-run
                  </span>
                )}
                <span className="metric">{spec.metric}</span>
              </span>
            </label>
          );
        })}
      </div>
    </>
  );
}
