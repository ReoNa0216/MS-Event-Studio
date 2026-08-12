export const RANGE_EXPORT_FIXTURE_IDS = Object.freeze([
  "range-input",
  "range-calculating",
  "range-preview",
  "range-applying",
  "range-error",
  "export-review-results",
  "export-audit-package",
  "exporting",
  "export-error",
]);

const EXPORT_KINDS = new Set(["review_results", "audit_package"]);

function safeText(value, fallback = "", maximum = 240) {
  const text = String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (text || fallback).slice(0, maximum);
}

function opaqueToken(value) {
  return typeof value === "string"
    ? value.replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, 256)
    : "";
}

function optionalNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function count(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.round(number)) : 0;
}

function noteText(value) {
  return String(value ?? "")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
    .slice(0, 500);
}

export function normalizeMinuteRange(value) {
  const startMin = optionalNumber(value?.start_min ?? value?.startMin);
  const endMin = optionalNumber(value?.end_min ?? value?.endMin);
  if (startMin === null || endMin === null || endMin < startMin) return null;
  return { startMin, endMin };
}

export function validateRangeInput(startValue, endValue) {
  const startText = String(startValue ?? "").trim();
  const endText = String(endValue ?? "").trim();
  const start = optionalNumber(startText);
  const end = optionalNumber(endText);
  if (start === null || end === null) {
    return { ok: false, message: "请填写有效的起点和终点。" };
  }
  if (end < start) {
    return { ok: false, message: "终点不能早于起点。" };
  }
  return { ok: true, start, end, startText, endText, message: "" };
}

export function normalizeRangePreview(value) {
  const payload = value && typeof value === "object" ? value : {};
  const previewToken = opaqueToken(payload.preview_token);
  const oldRange = normalizeMinuteRange(payload.old_range);
  const newRange = normalizeMinuteRange(payload.new_range);
  if (!previewToken || !oldRange || !newRange) throw new TypeError("范围预览响应无效。");
  const impacts = payload.impacts && typeof payload.impacts === "object" ? payload.impacts : {};
  return {
    previewToken,
    oldRange,
    newRange,
    impacts: {
      reusable: count(impacts.reusable_count),
      movedOut: count(impacts.moved_out_count),
      reconfirm: count(impacts.needs_reconfirmation_count),
      newlyDetected: count(impacts.newly_detected_count),
      retainedManual: count(impacts.retained_manual_count),
    },
  };
}

export function normalizeExportResult(value) {
  const payload = value && typeof value === "object" ? value : {};
  const kind = EXPORT_KINDS.has(payload.kind) ? payload.kind : null;
  if (!kind) throw new TypeError("导出结果响应无效。");
  return {
    kind,
    displayName: safeText(payload.display_name, "导出结果", 160).split(/[\\/]/).pop(),
    rowCount: count(payload.row_count),
    message: safeText(payload.message, "导出已完成。"),
  };
}

export function normalizeOperationJob(value) {
  const job = value?.job && typeof value.job === "object" ? value.job : value || {};
  const backendState = safeText(job.state, "failed", 24).toLowerCase();
  const error = job.error && typeof job.error === "object"
    ? {
        code: safeText(job.error.code, "operation_failed", 80),
        message: safeText(job.error.message, "后台任务未完成。"),
      }
    : null;
  return {
    jobId: opaqueToken(job.job_id),
    backendState,
    active: ["queued", "running", "cancelling"].includes(backendState),
    succeeded: backendState === "succeeded",
    cancelled: backendState === "cancelled",
    cancellable: Boolean(job.cancellable),
    phase: safeText(job.phase, "working", 80),
    fraction: Math.max(0, Math.min(1, Number(job.progress?.fraction) || 0)),
    result: job.result && typeof job.result === "object" ? job.result : null,
    error,
  };
}

export function rangePreviewBody(startValue, endValue) {
  const valid = validateRangeInput(startValue, endValue);
  if (!valid.ok) throw new TypeError(valid.message);
  return { start_min: valid.startText, end_min: valid.endText };
}

export function rangeApplyBody(previewToken, note = "") {
  const token = opaqueToken(previewToken);
  if (!token) throw new TypeError("范围预览已经失效。");
  return { preview_token: token, confirmed: true, note: noteText(note) };
}

export function rangeCancelBody(previewToken) {
  const token = opaqueToken(previewToken);
  if (!token) throw new TypeError("范围预览已经失效。");
  return { preview_token: token };
}

export function reviewExportBody(targetToken, includePending = false, note = "") {
  const token = opaqueToken(targetToken);
  if (!token) throw new TypeError("请选择审阅结果的保存文件。");
  return { target_token: token, include_pending: Boolean(includePending), note: noteText(note) };
}

export function auditExportBody(targetToken, note = "") {
  const token = opaqueToken(targetToken);
  if (!token) throw new TypeError("请选择完整审计数据包的保存位置。");
  return { target_token: token, note: noteText(note) };
}

export function estimatedReviewRows(review, includePending = false) {
  return count(review?.accepted) + (includePending ? count(review?.pending) : 0);
}

export function rangeCancelAction(flow) {
  if (!flow || flow.state === "closed") return "close";
  if (flow.state === "applying") return "blocked";
  if (flow.state === "calculating") {
    if (flow.cancelIssued) return "wait_terminal";
    return flow.jobId && flow.jobCancellable ? "cancel_job" : "wait_for_job";
  }
  return flow.previewToken ? "cancel_preview" : "close";
}

export function emptyRangeFlow() {
  return {
    state: "closed",
    jobId: "",
    jobCancellable: false,
    previewToken: "",
    oldRange: null,
    newRange: null,
    impacts: null,
    error: "",
    fraction: 0,
    cancelRequested: false,
    cancelIssued: false,
    cancelBusy: false,
  };
}

export function emptyExportFlow() {
  return {
    state: "closed",
    kind: null,
    target: null,
    includePending: false,
    jobId: "",
    jobCancellable: false,
    result: null,
    error: "",
    fraction: 0,
  };
}

export function fixtureRangeExport(id) {
  if (!RANGE_EXPORT_FIXTURE_IDS.includes(id)) return null;
  const preview = {
    previewToken: "fixture-range-preview",
    oldRange: { startMin: 0.5, endMin: 96 },
    newRange: { startMin: 2.25, endMin: 88.5 },
    impacts: { reusable: 5, movedOut: 1, reconfirm: 1, newlyDetected: 2, retainedManual: 1 },
  };
  const range = emptyRangeFlow();
  const exportFlow = emptyExportFlow();
  if (id.startsWith("range-")) {
    range.state = id.replace("range-", "");
    if (["preview", "applying", "error"].includes(range.state)) Object.assign(range, preview);
    if (range.state === "calculating") {
      range.jobId = "fixture-range-job";
      range.jobCancellable = true;
      range.fraction = 0.46;
    }
    if (range.state === "applying") {
      range.jobId = "fixture-range-apply-job";
      range.jobCancellable = false;
      range.fraction = 0.72;
    }
    if (range.state === "error") {
      range.previewToken = "";
      range.error = "范围预览已失效，项目没有改变。请重新计算预览。";
    }
  } else {
    exportFlow.kind = id === "export-audit-package" || id === "export-error"
      ? "audit_package"
      : "review_results";
    exportFlow.state = id === "exporting" ? "exporting" : id === "export-error" ? "error" : "input";
    exportFlow.includePending = id === "export-review-results";
    if (["exporting", "export-error"].includes(id)) {
      exportFlow.target = {
        selectionToken: "fixture-export-target",
        displayName: exportFlow.kind === "audit_package" ? "Lin− MPP 审计数据包" : "Lin− MPP 审阅结果.csv",
      };
    }
    if (id === "exporting") {
      exportFlow.jobId = "fixture-export-job";
      exportFlow.fraction = 0.58;
    }
    if (id === "export-error") {
      exportFlow.target = null;
      exportFlow.error = "导出未完成。请选择新的保存位置后重试。";
    }
  }
  return { range, export: exportFlow };
}
