# ALIE

Medico-legal document understanding for Quebec compensation regimes.
[ALIE-PRD.md](ALIE-PRD.md) is the source of truth for architecture; this file is how to run it.

**Status: Phase 1 — the deterministic floor.** Text-layer parse, manifest, template
registry, assemble, render, review and export, with the CNESST pack. No model has run.
See [What is not built](#what-is-not-built).

---

## Run it

No `make` on this machine, so these are the direct commands. The `Makefile` wraps the
same ones.

```bash
uv venv && uv pip install -e ".[dev]" && npm --prefix web install
```

Start the API (fixed port 8471, idempotent, fails loudly if something else holds the port):

```bash
uv run alie dev
```

Start the review UI in another shell (fixed port 5472, proxies `/api` to the API):

```bash
npm --prefix web run dev
```

Or run a fixture end to end with no server at all and print the chronology:

```bash
uv run alie run hard --rejoin
```

Tests:

```bash
uv run pytest -q
```

## The agent-QA loop (§13.2)

`alie dev` → `POST /dev/reset` → drive the browser → assert `GET /dev/state` → read
`var/logs/alie.log`. `/dev/state` returns job status, stage progress and counts as JSON so
QA asserts on facts rather than scraping a progress bar. Everything the UI exposes carries
a `data-testid`.

Fixtures seed automatically when the database is empty, so a fresh clone is a testable app
in one step.

---

## What is here

| | |
|---|---|
| `src/alie/models/` | the data model (§8) — blocks, units, dates, rows, citations |
| `src/alie/parse/` | text-layer tier, page labels, block typing (§4.3) |
| `src/alie/manifest/` | boundaries, orphan re-join, classify, dates, legibility (§4.4) |
| `src/alie/stages/` | the pipeline, each stage a pure function over ids (§4.2) |
| `src/alie/stores/` | blocks, manifest, audit log, runs, rows (§4.5) |
| `src/alie/seams/` | the five seams, and no more (§13.4) |
| `src/alie/packs/` | pack + template-registry loading (§6) |
| `packs/cnesst/` | the CNESST pack — rules as data, every rule tagged (§6.2) |
| `fixtures/` | `tiny`, `hard`, `dupes`, each with an expected page map (§13.3) |
| `web/` | the three-pane review surface (§10.2) and the why-panel (§7.1) |

### The §14.1 proof

`hard` is an 8-page bundle built to break the manifest. With `manifest.orphan_rejoin` on it
resolves to the page map the fixture declares:

```
[1]     rapport_evaluation_medicale   1992-12-10  inferred   ← exam date, not the événement
[2, 5]  note_consultation             2023-08-03  resolved   ← a page set, wrapping the IRM
[3, 4]  rapport_imagerie              2023-08-01  resolved
[6]     certificat_medical            2002-03-04  ambiguous  ← both readings kept
[7]     resultat_laboratoire          —           undated
[8]     unknown                       —           illegible  ← never sent to a model
```

The REM row date is the **examen** date (`92-12-10`), not the **événement** date
(`90-05-08`) two lines above it, and the century comes from the file's own year anchors
rather than a pivot. No model ran to produce any of this.

Turning the flag off is the flag's own metric (§9.2): 7 units instead of 6, and the orphan
page becomes a spurious undated row.

---

## What is not built

Honest scope, not a roadmap gloss.

**Phase 2+, per §14.** The eval harness and MLflow (`make eval` fails loudly rather than
exiting 0 on nothing). Delta runs. The OCR and vision parse tiers — the seam routes to
them and the flags exist, but only the text-layer tier is implemented. The seven-axis
duplicate view and clean-PDF export. SAAQ and IVAC packs. The health narrative composer.

**Stage 4b.** No model is configured. `seams/model.py` fails loudly rather than returning
plausible text, and the legibility gate is enforced at the seam so no caller can route
around it. Classification below the pack threshold stays `unknown` and is flagged rather
than guessed at.

**Table structure.** Table *rows* are detected; cell structure is not recovered. pdfium
collapses runs of spaces, so whitespace cannot stand in for column geometry. Cell-level
reads on templated forms come from the template registry, which crops known coordinates.

**Coordinate-based template crops.** The registry is keyed by form id + revision and falls
back to 4b on an unknown revision, but the field maps shipped here read by text anchor.
Coordinate crops need scanned samples to calibrate against, and inventing coordinates that
have never been validated is exactly the failure §4.3 warns about.

**Handwriting detection.** Deliberately absent from the text-layer tier: handwriting has no
text layer, so this tier cannot see it. It belongs to the vision tier.

**Bullet selection rules.** Bullets are transcribed source lines with spans, not summaries,
which satisfies the citation invariant. *Which* lines get selected is not yet rule-driven —
that is what the pack's line templates and 4b are for.

---

## Conventions

- One concern per module, soft cap around 150 lines (§13.1).
- Stages are pure functions over ids; jobs never run inside an HTTP request (§3.8, §13.4).
- Rules live in packs as data, never restated in prose in a skill or docstring — duplicated
  rules drift silently (§13.1, §5.1).
- Config and secrets come from the environment (`ALIE_API_PORT`, `ALIE_WEB_PORT`,
  `ALIE_VAR_DIR`, `ALIE_ACTOR`, `ALIE_MODEL_<TASK>`), never from code.

### Invariants that are not flags (§9)

Disabling one produces no data point, only a bad chronology. They appear read-only in the UI.

- Illegible units never reach the model.
- No uncited string in an export.
- Only strictly identical duplicates are auto-removable.
