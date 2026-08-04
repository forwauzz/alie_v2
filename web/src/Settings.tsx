import { useEffect, useRef, useState } from "react";
import type { FlagSpec } from "./api";

/** Everything in the register is built; unproven features ship off, each paired with the
 *  metric that decides whether it earns its place (PRD §9.2). It lives behind a control
 *  rather than on the work surface — a paralegal is not running an experiment.
 *
 *  Safety invariants are not flags and appear read-only (§9). */
export function Settings({
  specs,
  invariants,
  overrides,
  onChange,
}: {
  specs: FlagSpec[];
  invariants: string[];
  overrides: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const changed = specs.filter((s) => s.id in overrides && overrides[s.id] !== (s.default === true));

  return (
    <div className="popover-wrap" ref={wrap}>
      <button className="outline" onClick={() => setOpen((o) => !o)} data-testid="settings-toggle">
        Réglages
        {changed.length > 0 && <span className="tag accent" style={{ marginLeft: 6 }}>{changed.length}</span>}
      </button>

      {open && (
        <div className="popover" data-testid="settings-popover">
          <h3>Ce qui tourne</h3>
          <p className="note">
            Chaque option est accompagnée de la mesure qui décide si elle mérite d'être
            activée. Celles marquées « relance » invalident le travail déjà calculé.
          </p>

          <div data-testid="flag-register">
            {specs.map((spec) => {
              const value = overrides[spec.id] ?? spec.default === true;
              return (
                <label className="switch" key={spec.id} data-testid="flag" data-flag-id={spec.id}>
                  <input
                    type="checkbox"
                    checked={value}
                    // Named explicitly: the wrapping label's text is the id plus the
                    // metric plus a badge, which computes to nothing useful. Eleven
                    // switches all announced as "on" is not a register anyone can audit.
                    aria-label={spec.id}
                    aria-describedby={`metric-${spec.id}`}
                    onChange={(e) => onChange({ ...overrides, [spec.id]: e.target.checked })}
                  />
                  <span className="meta">
                    <code>{spec.id}</code>
                    {spec.requires_rerun && <span className="tag warn" style={{ marginLeft: 6 }}>relance</span>}
                    <span className="metric" id={`metric-${spec.id}`}>{spec.metric}</span>
                  </span>
                </label>
              );
            })}
          </div>

          <h3 style={{ marginTop: 16 }}>Toujours vrai</h3>
          <p className="note">Non désactivable.</p>
          <ul className="locked" data-testid="invariants">
            {invariants.map((inv) => <li key={inv}>{inv}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
