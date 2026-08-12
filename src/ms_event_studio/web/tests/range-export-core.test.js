import assert from "node:assert/strict";
import test from "node:test";

import {
  RANGE_EXPORT_FIXTURE_IDS,
  auditExportBody,
  estimatedReviewRows,
  fixtureRangeExport,
  normalizeExportResult,
  normalizeOperationJob,
  normalizeRangePreview,
  rangeApplyBody,
  rangeCancelAction,
  rangeCancelBody,
  rangePreviewBody,
  reviewExportBody,
  validateRangeInput,
} from "../range-export-core.js";

test("R5 fixtures are explicit and split range from export states", () => {
  assert.deepEqual(RANGE_EXPORT_FIXTURE_IDS, [
    "range-input", "range-calculating", "range-preview", "range-applying", "range-error",
    "export-review-results", "export-audit-package", "exporting", "export-error",
  ]);
  for (const id of RANGE_EXPORT_FIXTURE_IDS) assert.ok(fixtureRangeExport(id), id);
  assert.equal(fixtureRangeExport("not-r5"), null);
  assert.equal(fixtureRangeExport("range-calculating").range.jobCancellable, true);
  assert.equal(fixtureRangeExport("range-applying").range.jobCancellable, false);
});

test("range models retain only the canonical capability and impact fields", () => {
  const preview = normalizeRangePreview({
    preview_token: "opaque-preview",
    old_range: { start_min: "0.5", end_min: "96" },
    new_range: { start_min: "2.25", end_min: "88.5" },
    impacts: {
      reusable_count: 5,
      moved_out_count: 1,
      needs_reconfirmation_count: 2,
      newly_detected_count: 3,
      retained_manual_count: 1,
      event_id: 99,
    },
    project_id: "private",
  });
  assert.deepEqual(preview.impacts, {
    reusable: 5, movedOut: 1, reconfirm: 2, newlyDetected: 3, retainedManual: 1,
  });
  assert.equal(JSON.stringify(preview).includes("project_id"), false);
  assert.deepEqual(rangePreviewBody("2.25", "88.5"), { start_min: "2.25", end_min: "88.5" });
  assert.deepEqual(rangeApplyBody("opaque-preview", "收窄范围"), {
    preview_token: "opaque-preview", confirmed: true, note: "收窄范围",
  });
  assert.deepEqual(rangeCancelBody("opaque-preview"), { preview_token: "opaque-preview" });
  assert.equal(validateRangeInput("5", "4").ok, false);
});

test("export bodies are closed and results remain browser-safe", () => {
  assert.deepEqual(reviewExportBody("opaque-file", true, "包含待定"), {
    target_token: "opaque-file", include_pending: true, note: "包含待定",
  });
  assert.deepEqual(auditExportBody("opaque-folder", "归档"), {
    target_token: "opaque-folder", note: "归档",
  });
  assert.equal(estimatedReviewRows({ accepted: 4, pending: 2 }, false), 4);
  assert.equal(estimatedReviewRows({ accepted: 4, pending: 2 }, true), 6);
  const result = normalizeExportResult({
    kind: "review_results", display_name: "C:\\private\\review.csv", row_count: 4,
    message: "导出完成", schema: "private",
  });
  assert.deepEqual(result, {
    kind: "review_results", displayName: "review.csv", rowCount: 4, message: "导出完成",
  });
});

test("operation jobs expose lifecycle without leaking worker result fields", () => {
  const job = normalizeOperationJob({ job: {
    job_id: "opaque-job", state: "running", phase: "calculating", cancellable: true,
    progress: { fraction: 0.4 }, result: null,
  } });
  assert.equal(job.active, true);
  assert.equal(job.cancellable, true);
  assert.equal(job.fraction, 0.4);
});

test("range cancellation intent waits for the first job response without closing", () => {
  assert.equal(rangeCancelAction({ state: "calculating", jobId: "", jobCancellable: false }), "wait_for_job");
  assert.equal(rangeCancelAction({ state: "calculating", jobId: "opaque", jobCancellable: true }), "cancel_job");
  assert.equal(rangeCancelAction({ state: "calculating", jobId: "opaque", cancelIssued: true }), "wait_terminal");
  assert.equal(rangeCancelAction({ state: "preview", previewToken: "opaque" }), "cancel_preview");
  assert.equal(rangeCancelAction({ state: "applying" }), "blocked");
});
