# ALIE

Medico-legal document understanding for Quebec compensation regimes.
[ALIE-PRD.md](ALIE-PRD.md) is the source of truth for architecture; this file is how to run it.

**Status: the PRD is built, and no language model has ever run.** Parse (text layer, OCR,
vision), manifest, filters, dedupe, regime screening, the §8.6 required fields, assemble,
render, review, export, delta runs, the firm layer, the eval harness and shadow mode —
across CNESST, SAAQ and IVAC packs. Every model-dependent path degrades safely and is
tested against fakes; none has been exercised against a live model.
See [What is not built](#what-is-not-built).

---

## Run it

No `make` on this machine, so these are the direct commands. The `Makefile` wraps the
same ones.

```bash
uv venv && uv pip install -e ".[dev]" && npm --prefix web install
```

### OCR

`parse.ocr` is on by default and needs Tesseract plus the French model. Both are found
automatically — the binary from `PATH` or the standard install location, the model from
`.tessdata/` in the repo. With neither present the tier simply is not registered and pages
fall through to unparseable, exactly as when the flag was off.

```bash
curl -L -o .tessdata/fra.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/fra.traineddata
```

Tesseract itself is a system install (`winget install UB-Mannheim.TesseractOCR`, `brew
install tesseract`, `apt install tesseract-ocr`). Overrides, if the defaults don't find
them: `ALIE_TESSERACT_EXE`, `ALIE_TESSDATA_DIR`, `ALIE_OCR_LANG`, `ALIE_OCR_SCALE`.

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

Score every gold end to end. Exits non-zero when a release-blocking metric fails —
groundedness, uncited strings, page coverage and truncation are must-holds, not
diagnostics (§11.3):

```bash
make eval
```

Runs are logged to MLflow on :5471, one per gold, tagged with a shared run group (§11.1).
`alie dev` starts it alongside the API; on its own:

```bash
uv run alie mlflow
```

MLflow is a **recording surface, not a source of truth**. Raw PDFs and answer keys are
never logged — a gold's id, version and content hash prove which key a run scored against
without copying patient files into a second store. If the server is down the sweep still
runs, still scores, and still writes its artifacts to `var/eval`; it says so rather than
reporting a silent zero.

Measure one flag against the baseline over the same golds (§9.1). Reports the metric delta
and the row churn separately, and refuses to vary two things at once:

```bash
uv run alie shadow manifest.orphan_rejoin
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
| `src/alie/parse/` | text-layer, OCR and vision tiers; page labels, block typing (§4.3) |
| `src/alie/manifest/` | boundaries, re-join, classify, dates, filters, dedupe, screening (§4.4) |
| `src/alie/stages/` | the pipeline, each stage a pure function over ids (§4.2) |
| `src/alie/stores/` | blocks, manifest, audit log, runs, rows (§4.5) |
| `src/alie/eval/` | gold scoring, must-hold metrics, shadow mode, MLflow sink (§11, §9.1) |
| `src/alie/seams/` | the five seams, and no more (§13.4) |
| `src/alie/packs/` | pack + template-registry loading (§6) |
| `packs/` | CNESST, SAAQ, IVAC — rules as data, one-page regime skills, gaps declared (§6.2) |
| `fixtures/` | `tiny` `hard` `dupes` `admin` `fields` `mixed`, each a gold (§13.3) |
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

### What the real case measured

Case 1 is 12 bundles, 312 pages, real scans. This is the number §14 says Phase 1 exists to
produce, and the reason `parse.ocr` is now on by default:

| | OCR off | OCR on |
|---|---|---|
| pages with no readable text | 188 | 0 |
| report units | 208 | 54 |
| units the pipeline can read | 19 (9%) | 44 (81%) |
| pages with a printed label | 56 | 84 |
| seconds for 312 pages | 4.8 | 226 |

The unit count falling from 208 to 54 is not a regression: most of the 208 were single
unreadable pages stranded as their own unit. 54 units across 312 pages is a plausible case.

Two things this exposed that synthetic fixtures could not. These bundles arrive *already*
OCR'd by whoever scanned them, and that pass often failed while still emitting characters
— so a legibility gate that counts characters calls noise legible and would feed it
straight to a model. And the footers carry reference numbers and birth years, so a bare
number is not a page label: `1937` appears on four pages of one bundle, and
`printed_label` is what *renders* into a citation.

---

## What is not built

Honest scope, not a roadmap gloss.

**No model has ever run.** This is the largest gap and it cuts across three features.
There is no API credential on the machine that built this, so stage 4b (model extraction),
the vision parse tier, and the health composer have never executed against a live model.
Each degrades safely — the tier is not registered, the stage is skipped, rows fall back to
deterministic selection — and each is tested against fakes deliberately built to lie, which
proves the *safety* checks work and says nothing about whether the model is any good at the
job. Treat every model-dependent number as computable, not computed.

**The health narrative composer.** Not built. §16 parks the liability question — whether
the draft is adopted by the clinician, and what disclaimer it carries — and the PRD says to
answer that before building the vertical.

**Referenced units.** `UnitKind.REFERENCED` exists in the model and nothing produces it. A
document that appears only as a citation inside another note ("IRM du 12 mai") does not yet
become a second-hand row.

**SAAQ and IVAC packs are deliberately incomplete, and say where.** Each declares
`known_gaps` naming the fields it cannot read and why, and the plan surfaces them before
cost is approved. SAAQ needs real forms catalogued into the template registry; IVAC needs a
practitioner for two legal questions the PRD leaves open (§15.7, §15.8) — how the CNESST
REM/2064 coexists with the LAPVIC Répertoire post-2021, and when LATMP takes precedence.
`faute_lourde` is detected and never adjudicated.

**Table structure.** Table *rows* are detected; cell structure is not recovered. pdfium
collapses runs of spaces, so whitespace cannot stand in for column geometry. Cell-level
reads on templated forms come from the template registry, which crops known coordinates.

**Coordinate-based template crops.** The registry is keyed by form id + revision and falls
back to 4b on an unknown revision, but the field maps shipped here read by text anchor.
Coordinate crops need scanned samples to calibrate against, and inventing coordinates that
have never been validated is exactly the failure §4.3 warns about.

**Handwriting detection.** Absent from the text-layer tier: handwriting has no text layer,
so that tier cannot see it. The vision tier transcribes it and marks it `HANDWRITING`, which
keeps it out of the deliverable — a reviewer's private note must never reach a document
destined for opposing counsel (§4.3) — while still recording that the page had a line there.

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
