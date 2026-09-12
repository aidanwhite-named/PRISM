export interface AnswerExample {
  element: string; issue: string; judgment: string; reason: string;
  report_quote: string; source_id: string; source_quote: string; location: string;
}
export interface AnswerContent { style_rules: string[]; examples: AnswerExample[] }
export interface AnswerCaseSummary {
  id: string; title: string; status: "draft" | "approved" | "archived";
  revision: number; edit_version: number; example_count: number;
  source_job_id: string | null; source_job_label: string;
  extraction_error: string | null; created_at: string; updated_at: string;
}
export interface AnswerExtraction {
  id: string; status: string; provider: string; model: string | null;
  error: string | null; result_text: string | null; created_at: string;
  prompt_sha256: string; execution_manifest: Record<string, unknown> | null;
}
export interface AnswerCase extends AnswerCaseSummary {
  claim_text: string; original_report: string; report_text: string; draft: AnswerContent;
  files: {id: string; name: string; kind: "source" | "report"; read_ok: boolean; error: string | null}[];
  extractions: AnswerExtraction[];
}
export interface ReportContext {
  enabled: boolean; estimated_tokens: number; token_budget: number; style: string;
  examples: (AnswerExample & {case_id: string; revision: number; title: string})[];
  sha256?: string; error?: string | null;
}
export const emptyExample = (): AnswerExample => ({element: "", issue: "", judgment: "", reason: "",
  report_quote: "", source_id: "", source_quote: "", location: ""});
