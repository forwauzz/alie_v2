import { useCallback, useEffect, useRef, useState } from "react";
import type { FlagSpec, Plan, Row, Run, Validation } from "./api";
import { api } from "./api";

const TERMINAL = ["done", "failed", "superseded"];

/** One entry in the thread. Every one is generated from real state — a plan the manifest
 *  produced, a run's own stage counts, a validated chronology. Nothing here narrates
 *  something the engine did not actually do. */
export type Turn =
  | { kind: "user"; id: string; text: string }
  | { kind: "plan"; id: string; plan: Plan }
  | { kind: "run"; id: string; runId: string }
  | { kind: "note"; id: string; text: string; tone?: "plain" | "error" };

export type CaseState = ReturnType<typeof useCase>;

let seq = 0;
const nextId = () => `t${++seq}`;

export function useCase(caseId: string) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [flagSpecs, setFlagSpecs] = useState<FlagSpec[]>([]);
  const [invariants, setInvariants] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const poll = useRef<number | null>(null);

  const say = useCallback((turn: Turn) => setTurns((t) => [...t, turn]), []);

  useEffect(() => {
    api
      .flags()
      .then((f) => {
        setFlagSpecs(f.flags);
        setInvariants(f.safety_invariants);
      })
      .catch(() => undefined);
  }, []);

  // Reset the thread when the case changes; a thread belongs to one case.
  useEffect(() => {
    if (poll.current) window.clearInterval(poll.current);
    poll.current = null;
    setTurns([]);
    setRun(null);
    setRows([]);
    setValidation(null);
    setPlan(null);
    if (!caseId) return;

    let live = true;
    api
      .plan(caseId)
      .then((p) => {
        if (!live) return;
        setPlan(p);
        setTurns([{ kind: "plan", id: nextId(), plan: p }]);
      })
      .catch((e) => live && setTurns([{ kind: "note", id: nextId(), text: String(e), tone: "error" }]));
    return () => {
      live = false;
    };
  }, [caseId]);

  const loadRows = useCallback(async (runId: string) => {
    const payload = await api.rows(runId);
    setRows(payload.rows);
    setValidation(payload.validation);
  }, []);

  const watch = useCallback(
    (runId: string) => {
      const tick = async () => {
        try {
          const fresh = await api.run(runId);
          setRun(fresh);
          if (TERMINAL.includes(fresh.status)) {
            if (poll.current) window.clearInterval(poll.current);
            poll.current = null;
            setBusy(false);
            if (fresh.status === "done") {
              await loadRows(runId);
              // Re-read the plan: a run rebuilds the manifest, and the plan is the
              // manifest summary (§4.1).
              api.plan(fresh.case_id).then(setPlan).catch(() => undefined);
            } else if (fresh.status === "failed") {
              const failed = (fresh.jobs ?? []).find((j) => j.error);
              say({
                kind: "note",
                id: nextId(),
                tone: "error",
                text: failed?.error ?? "L'exécution s'est arrêtée.",
              });
            }
          }
        } catch (e) {
          setBusy(false);
          say({ kind: "note", id: nextId(), tone: "error", text: String(e) });
        }
      };
      void tick();
      poll.current = window.setInterval(tick, 350);
    },
    [loadRows, say],
  );

  useEffect(() => () => {
    if (poll.current) window.clearInterval(poll.current);
  }, []);

  const start = useCallback(
    async (utterance?: string) => {
      if (!caseId || busy) return;
      setBusy(true);
      setRows([]);
      setValidation(null);
      if (utterance) say({ kind: "user", id: nextId(), text: utterance });
      try {
        const created = await api.createRun(caseId, overrides);
        setRun(created);
        say({ kind: "run", id: nextId(), runId: created.id });
        watch(created.id);
      } catch (e) {
        setBusy(false);
        say({ kind: "note", id: nextId(), tone: "error", text: String(e) });
      }
    },
    [caseId, busy, overrides, say, watch],
  );

  const correct = useCallback(
    async (unitId: string, value: string | null, previous: string | null) => {
      await api.correct({
        subject_id: unitId,
        field: "row_date",
        new_value: value,
        old_value: previous,
      });
      say({
        kind: "user",
        id: nextId(),
        text: value ? `Cette date est le ${value}.` : "Ce document n'a pas de date utilisable.",
      });
      say({
        kind: "note",
        id: nextId(),
        text:
          "Écrit au manifeste. Je régénère la chronologie — la correction survivra aux " +
          "prochaines exécutions.",
      });
      await start();
    },
    [say, start],
  );

  return {
    plan,
    turns,
    run,
    rows,
    validation,
    flagSpecs,
    invariants,
    overrides,
    setOverrides,
    busy,
    start,
    correct,
  };
}
