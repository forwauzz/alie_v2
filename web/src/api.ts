// Thin client over the ALIE API. Vite proxies /api to the FastAPI port in dev.

const BASE = "/api";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} → ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export type Case = { id: string; name: string; primary_pack: string };

export type Plan = {
  case_id: string;
  pack: string;
  pack_version: string;
  pages: number;
  units: number;
  units_by_class: Record<string, number>;
  flagged: Record<string, number>;
  excluded_by_rule: number;
  off_by_toggle: number;
  estimate_seconds: number;
  summary: string;
  bundles: { id: string; folder: string; pages: number }[];
};

export type FlagSpec = {
  id: string;
  kind: "behaviour" | "implementation";
  default: boolean | string;
  question: string;
  metric: string;
  requires_rerun: boolean;
};

export type Run = {
  id: string;
  case_id: string;
  status: string;
  flags: Record<string, unknown>;
  stage_progress?: Record<string, Record<string, number>>;
  jobs?: { id: string; stage: string; status: string; error: string | null }[];
};

export type Locator = {
  folder: string;
  pdf_index: number;
  printed_label: string | null;
  display_page: string;
  flagged: boolean;
  unit_id: string;
};

export type Bullet = {
  text: string;
  confidence: number;
  rule: string | null;
  citation: {
    bundle_id: string;
    pdf_index: number;
    printed_label: string | null;
    unit_id: string;
    block_id: string | null;
    start: number | null;
    end: number | null;
  };
};

export type Row = {
  id: string;
  date: string | null;
  date_status: string;
  date_explanation: string;
  date_alternatives: string[];
  title: string;
  author: string | null;
  doc_class: string;
  confidence: number;
  warns: boolean;
  illegible_reason: string | null;
  second_hand: boolean;
  unit_ids: string[];
  locators: Locator[];
  bullets: Bullet[];
};

export type Validation = {
  uncited: number;
  unlocated_rows: number;
  coverage: number;
  pages_total: number;
  pages_covered: number;
  rows_without_printed_label: number;
  passes: boolean;
};

export type Why = {
  unit_id: string;
  pages: number[];
  contiguous: boolean;
  bundle: { id: string; folder: string };
  classification: {
    class: string;
    label: string;
    confidence: number;
    source: string;
    matched: string[];
    needs_fallback: boolean;
  };
  form: { serial: string | null; revision: string | null };
  legibility: { level: string; reason: string | null };
  row_date: {
    value: string | null;
    rendered: string;
    status: string;
    role: string | null;
    rule: string;
    explanation: string;
    alternatives: string[];
  };
  dates_found: {
    role: string;
    eligible: boolean;
    raw: string;
    readings: string[];
    ambiguous: boolean;
    century_inferred: boolean;
    pdf_index: number;
    printed_label: string | null;
    block_id: string;
    span: [number, number];
  }[];
  records: {
    field: string;
    value: string | null;
    stage: string;
    confidence: number;
    derived: boolean;
    rule: string | null;
    epistemic_tag: string | null;
    prompt_version: string | null;
    model: string | null;
    block_id: string | null;
    span: [number, number] | null;
  }[];
  corrections: { field: string; new_value: string; actor: string; created_at: string }[];
  audit: { action: string; rule: string | null; ts: string; detail: Record<string, unknown> }[];
};

export type SourceCrop = {
  block_id: string;
  pdf_index: number;
  printed_label: string | null;
  type: string;
  text: string;
  bbox: [number, number, number, number];
  source: string;
  confidence: number;
  attrs: Record<string, string>;
};

export const api = {
  cases: () => json<Case[]>("/cases"),
  plan: (caseId: string) => json<Plan>(`/cases/${caseId}/plan`),
  flags: () => json<{ flags: FlagSpec[]; safety_invariants: string[] }>("/flags"),
  createRun: (caseId: string, flags: Record<string, unknown>) =>
    json<Run>(`/cases/${caseId}/runs`, { method: "POST", body: JSON.stringify({ flags }) }),
  run: (runId: string) => json<Run>(`/runs/${runId}`),
  rows: (runId: string) =>
    json<{ run_id: string; rows: Row[]; validation: Validation }>(`/runs/${runId}/rows`),
  exportUrl: (runId: string) => `${BASE}/runs/${runId}/export.md`,
  why: (unitId: string) => json<Why>(`/units/${unitId}/why`),
  block: (blockId: string) => json<SourceCrop>(`/blocks/${blockId}`),
  correct: (body: {
    subject_id: string;
    field: string;
    new_value: string | null;
    old_value?: string | null;
  }) =>
    json<{ id: string; requires_rerun: boolean }>("/corrections", {
      method: "POST",
      body: JSON.stringify({ subject_type: "unit", ...body }),
    }),
  devState: () => json<Record<string, unknown>>("/dev/state"),
  devReset: () => json<Record<string, unknown>>("/dev/reset", { method: "POST" }),
};
