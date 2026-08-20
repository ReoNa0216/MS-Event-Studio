import assert from "node:assert/strict";
import test from "node:test";

import {
  API_ENDPOINTS,
  FIXTURE_IDS,
  PATH_ROLES,
  allowedFixture,
  analysisStateFromJob,
  fixtureScenario,
  formatBytes,
  normalizeInspection,
  safeDisplayName,
  validateAnalysisRange,
} from "../app-core.js";
import {
  EVENT_EDIT_FIXTURE_IDS,
  REVIEW_FIXTURE_IDS,
  REVIEW_SAVE_FIXTURE_IDS,
  RESPONSIVE_FIXTURE_IDS,
} from "../workspace-core.js";
import { RANGE_EXPORT_FIXTURE_IDS } from "../range-export-core.js";

const R1_FIXTURE_IDS = [
  "welcome",
  "create-idle",
  "create-running",
  "create-cancelling",
  "create-cancelled",
  "create-error",
  "create-ready",
  "open",
];

test("screenshot fixture ids are explicit and unknown values are ignored", () => {
  assert.deepEqual(FIXTURE_IDS, [
    ...R1_FIXTURE_IDS,
    ...REVIEW_FIXTURE_IDS,
    ...REVIEW_SAVE_FIXTURE_IDS,
    ...EVENT_EDIT_FIXTURE_IDS,
    ...RESPONSIVE_FIXTURE_IDS,
    ...RANGE_EXPORT_FIXTURE_IDS,
  ]);
  assert.equal(allowedFixture("create-ready"), "create-ready");
  assert.equal(allowedFixture("anything-else"), null);
  assert.equal(allowedFixture(""), null);
});

test("fixture query coexists with a desktop capability query", () => {
  const query = new URLSearchParams("native_bridge=opaque&fixture=create-running");
  assert.equal(allowedFixture(query.get("fixture")), "create-running");
  assert.equal(query.get("native_bridge"), "opaque");
});

test("all screenshot fixtures have deterministic dialog and analysis states", () => {
  const expected = {
    welcome: [null, "idle"],
    "create-idle": ["create", "idle"],
    "create-running": ["create", "running"],
    "create-cancelling": ["create", "cancelling"],
    "create-cancelled": ["create", "cancelled"],
    "create-error": ["create", "error"],
    "create-ready": ["create", "ready"],
    open: ["open", "idle"],
  };
  for (const id of R1_FIXTURE_IDS) {
    const scenario = fixtureScenario(id);
    assert.ok(scenario);
    assert.equal(scenario.dialog, expected[id][0]);
    assert.equal(scenario.create.analysisState, expected[id][1]);
  }
});

test("closed single-point analysis range remains valid", () => {
  assert.deepEqual(
    validateAnalysisRange("1.25", "1.25", { start_min: 0, end_min: 2 }),
    { ok: true, start: 1.25, end: 1.25, message: "" },
  );
  assert.equal(
    validateAnalysisRange("1.26", "1.25", { start_min: 0, end_min: 2 }).message,
    "终点不能早于起点。",
  );
});

test("range validation respects the source's closed available interval", () => {
  assert.equal(validateAnalysisRange("0.5", "96", { start_min: 0.5, end_min: 96 }).ok, true);
  assert.equal(validateAnalysisRange("0.4", "96", { start_min: 0.5, end_min: 96 }).ok, false);
  assert.equal(validateAnalysisRange("0.5", "96.1", { start_min: 0.5, end_min: 96 }).ok, false);
});

test("display names defensively discard any directory components", () => {
  assert.equal(safeDisplayName("C:\\raw\\Lin-_MPP.txt"), "Lin-_MPP.txt");
  assert.equal(safeDisplayName("/data/raw/Lin-_MPP.txt"), "Lin-_MPP.txt");
  assert.equal(safeDisplayName("\u0000"), "未命名");
});

test("job states map to the visible lifecycle", () => {
  assert.equal(analysisStateFromJob("queued"), "running");
  assert.equal(analysisStateFromJob("running"), "running");
  assert.equal(analysisStateFromJob("cancelling"), "cancelling");
  assert.equal(analysisStateFromJob("succeeded"), "ready");
  assert.equal(analysisStateFromJob("cancelled"), "cancelled");
  assert.equal(analysisStateFromJob("failed"), "error");
});

test("inspection normalization keeps only a display leaf and browser-safe values", () => {
  const inspection = normalizeInspection({
    inspection_token: "opaque-inspection",
    source_name: "D:\\private\\run.txt",
    available_range: { start_min: "0.5", end_min: "96" },
    scan_count: 54091,
    size_bytes: 7937091790,
  });
  assert.equal(inspection.displayName, "run.txt");
  assert.equal(inspection.availableRange.start_min, 0.5);
  assert.equal(inspection.availableRange.end_min, 96);
  assert.equal(formatBytes(inspection.sizeBytes), "7.39 GB");
});

test("front-end API paths are same-origin, query-free, and roles are narrow", () => {
  const paths = [
    API_ENDPOINTS.bootstrap,
    API_ENDPOINTS.selectPath,
    API_ENDPOINTS.sourceInspections,
    API_ENDPOINTS.projects,
    API_ENDPOINTS.openProject,
    API_ENDPOINTS.reviewDecision,
    API_ENDPOINTS.reviewBulkAccept,
    API_ENDPOINTS.restoreAutomaticApex,
    API_ENDPOINTS.reviewUndo,
    API_ENDPOINTS.reviewRedo,
    API_ENDPOINTS.eventEditAim,
    API_ENDPOINTS.eventEditPreview,
    API_ENDPOINTS.eventEditApply,
    API_ENDPOINTS.eventEditCancel,
    API_ENDPOINTS.rangePreview,
    API_ENDPOINTS.rangeApply,
    API_ENDPOINTS.rangeCancel,
    API_ENDPOINTS.exportReviewResults,
    API_ENDPOINTS.exportAuditPackage,
    API_ENDPOINTS.job("opaque id"),
    API_ENDPOINTS.cancelJob("opaque id"),
  ];
  for (const path of paths) {
    assert.match(path, /^\/api\//);
    assert.equal(path.includes("?"), false);
  }
  assert.deepEqual(PATH_ROLES, {
    source: "source_file",
    open: "project_open",
    target: "project_target",
    reviewExport: "review_export_file",
    auditExport: "audit_export_parent",
  });
});
