export const API_ENDPOINTS = Object.freeze({
  bootstrap: "/api/bootstrap",
  workspace: "/api/workspace",
  workspaceWindow: "/api/workspace/window",
  reviewDecision: "/api/review/decision",
  reviewBulkAccept: "/api/review/bulk-accept",
  restoreAutomaticApex: "/api/review/restore-automatic-apex",
  reviewUndo: "/api/review/undo",
  reviewRedo: "/api/review/redo",
  eventEditAim: "/api/event-edits/aim",
  eventEditPreview: "/api/event-edits/preview",
  eventEditApply: "/api/event-edits/apply",
  eventEditCancel: "/api/event-edits/cancel",
  rangePreview: "/api/range-changes/preview",
  rangeApply: "/api/range-changes/apply",
  rangeCancel: "/api/range-changes/cancel",
  exportReviewResults: "/api/exports/review-results",
  exportAuditPackage: "/api/exports/audit-package",
  selectPath: "/api/select-path",
  sourceInspections: "/api/source-inspections",
  projects: "/api/projects",
  openProject: "/api/projects/open",
  job(jobId) {
    return `/api/jobs/${encodeURIComponent(String(jobId))}`;
  },
  cancelJob(jobId) {
    return `/api/jobs/${encodeURIComponent(String(jobId))}/cancel`;
  },
});

export const REQUEST_TOKEN_HEADER = "X-MS-Event-Token";

export const PATH_ROLES = Object.freeze({
  source: "source_file",
  open: "project_open",
  target: "project_target",
  reviewExport: "review_export_file",
  auditExport: "audit_export_parent",
});

export const FIXTURE_IDS = Object.freeze([
  "welcome",
  "create-idle",
  "create-running",
  "create-cancelling",
  "create-cancelled",
  "create-error",
  "create-ready",
  "open",
  "review-no-selection",
  "review-unreviewed-auto",
  "review-accepted-auto",
  "review-rejected-auto",
  "review-pending-auto",
  "review-manual",
  "review-highest",
  "review-edge",
  "review-dense",
  "save-in-progress",
  "save-failed",
  "add-aim",
  "add-preview",
  "adjust-aim",
  "adjust-preview",
  "edit-out-of-range",
  "undo-empty",
  "undo-redo-ready",
  "long-chinese-copy",
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

const FIXTURE_SET = new Set(FIXTURE_IDS);

export function allowedFixture(value) {
  const requested = String(value || "").trim();
  return FIXTURE_SET.has(requested) ? requested : null;
}

export function clampFraction(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(1, number));
}

export function safeDisplayName(value, fallback = "未命名") {
  const text = String(value || "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
  if (!text) return fallback;
  const leaf = text.split(/[\\/]/).filter(Boolean).pop() || fallback;
  return leaf.slice(0, 160);
}

export function suggestedProjectName(sourceName) {
  const leaf = safeDisplayName(sourceName, "新项目");
  const withoutExtension = leaf.replace(/\.[^.]+$/, "").trim();
  return (withoutExtension || "新项目").slice(0, 80);
}

export function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = bytes;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  const digits = index === 0 ? 0 : amount >= 10 ? 1 : 2;
  return `${amount.toFixed(digits)} ${units[index]}`;
}

export function formatCount(value, fallback = "—") {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return fallback;
  return Math.round(number).toLocaleString("zh-CN");
}

export function formatMinute(value, digits = 3) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toFixed(digits);
}

export function formatRange(range) {
  const start = Number(range?.start_min);
  const end = Number(range?.end_min);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "—";
  return `${formatMinute(start)}–${formatMinute(end)} min`;
}

export function validateAnalysisRange(startValue, endValue, availableRange) {
  const start = Number(startValue);
  const end = Number(endValue);
  const minimum = Number(availableRange?.start_min);
  const maximum = Number(availableRange?.end_min);
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return { ok: false, message: "请填写有效的起点和终点。" };
  }
  if (start > end) {
    return { ok: false, message: "终点不能早于起点。" };
  }
  if (Number.isFinite(minimum) && start < minimum) {
    return { ok: false, message: `起点不能早于 ${formatMinute(minimum)} min。` };
  }
  if (Number.isFinite(maximum) && end > maximum) {
    return { ok: false, message: `终点不能晚于 ${formatMinute(maximum)} min。` };
  }
  return { ok: true, start, end, message: "" };
}

export function analysisStateFromJob(value) {
  const state = String(value || "").toLowerCase();
  const mapping = {
    idle: "idle",
    queued: "running",
    running: "running",
    cancelling: "cancelling",
    succeeded: "ready",
    completed: "ready",
    cancelled: "cancelled",
    failed: "error",
    error: "error",
  };
  return mapping[state] || "error";
}

export function phaseLabel(value) {
  const phase = String(value || "").toLowerCase();
  if (phase.includes("read") || phase.includes("pars")) return "正在读取扫描数据";
  if (phase.includes("finger") || phase.includes("hash") || phase.includes("verify")) return "正在核对文件完整性";
  if (phase.includes("prepar") || phase.includes("final")) return "正在整理分析结果";
  if (phase.includes("detect")) return "正在识别主 marker 信号";
  return "正在检查源文件";
}

export function normalizedProgress(value) {
  const progress = value && typeof value === "object" ? value : {};
  return {
    fraction: clampFraction(progress.fraction),
    bytesRead: Math.max(0, Number(progress.bytes_read) || 0),
    totalBytes: Math.max(0, Number(progress.total_bytes) || 0),
    parsedSpectra: Math.max(0, Number(progress.parsed_spectra) || 0),
  };
}

export function normalizeInspection(value) {
  const result = value && typeof value === "object" ? value : {};
  const range = result.available_range && typeof result.available_range === "object"
    ? result.available_range
    : {};
  return {
    inspectionToken: typeof result.inspection_token === "string" ? result.inspection_token : "",
    displayName: safeDisplayName(result.display_name || result.source_name, "MS 原始文件"),
    availableRange: {
      start_min: Number(range.start_min),
      end_min: Number(range.end_min),
    },
    scanCount: Math.max(0, Number(result.scan_count) || 0),
    sizeBytes: Math.max(0, Number(result.size_bytes) || 0),
  };
}

export function normalizeProject(value) {
  const project = value && typeof value === "object" ? value : {};
  const range = project.analysis_range && typeof project.analysis_range === "object"
    ? project.analysis_range
    : {};
  return {
    displayName: safeDisplayName(project.display_name, "未命名项目"),
    analysisRange: {
      start_min: Number(range.start_min),
      end_min: Number(range.end_min),
    },
    eventCount: Math.max(0, Number(project.event_count) || 0),
    primaryMarkerMz: Number.isFinite(Number(project.primary_marker_mz))
      ? Number(project.primary_marker_mz)
      : 760.5851,
    collisionGapSec: Number.isFinite(Number(project.collision_gap_sec))
      ? Number(project.collision_gap_sec)
      : 0.60,
  };
}

export function normalizeBootstrap(value) {
  const payload = value && typeof value === "object" ? value : {};
  const recent = Array.isArray(payload.recent_projects) ? payload.recent_projects : [];
  return {
    app: {
      name: safeDisplayName(payload.app?.name, "MS Event Studio"),
      version: String(payload.app?.version || ""),
      language: String(payload.app?.language || "zh-CN"),
    },
    view: payload.view === "project" ? "project" : "welcome",
    recentProjects: recent
      .filter((row) => row && typeof row.project_token === "string" && row.project_token)
      .slice(0, 8)
      .map((row) => ({
        projectToken: row.project_token,
        displayName: safeDisplayName(row.display_name, "未命名项目"),
        lastOpened: typeof row.last_opened === "string" ? row.last_opened : "",
      })),
    activeProject: payload.active_project ? normalizeProject(payload.active_project) : null,
    requestToken: typeof payload.request_token === "string" ? payload.request_token : "",
  };
}

export function normalizeJob(value) {
  const job = value?.job && typeof value.job === "object" ? value.job : value || {};
  const backendState = String(job.state || "failed").toLowerCase();
  return {
    jobId: typeof job.job_id === "string" ? job.job_id : "",
    backendState,
    state: analysisStateFromJob(backendState),
    phase: phaseLabel(job.phase),
    progress: normalizedProgress(job.progress),
    result: job.result && typeof job.result === "object" ? job.result : null,
  };
}

export function emptyCreateState() {
  return {
    source: null,
    target: null,
    jobId: "",
    jobKind: "",
    analysisState: "idle",
    phase: "",
    progress: normalizedProgress(null),
    inspection: null,
  };
}

const fixtureRecentProjects = Object.freeze([
  { project_token: "fixture-recent-a", display_name: "Lin− 重复 01", last_opened: "2026-08-12T09:30:00Z" },
  { project_token: "fixture-recent-b", display_name: "LSK 批次 07", last_opened: "2026-08-10T14:15:00Z" },
]);

function fixtureBootstrap() {
  return normalizeBootstrap({
    app: { name: "MS Event Studio", version: "0.4.1", language: "zh-CN" },
    view: "welcome",
    recent_projects: fixtureRecentProjects,
    active_project: null,
    request_token: "fixture-request",
  });
}

function fixtureCreateBase() {
  return {
    ...emptyCreateState(),
    source: { selectionToken: "fixture-source", displayName: "Lin-_MPP.txt" },
  };
}

export function fixtureScenario(value) {
  const id = allowedFixture(value);
  if (!id) return null;
  if (
    id.startsWith("review-")
    || id.startsWith("save-")
    || id.startsWith("add-")
    || id.startsWith("adjust-")
    || id === "edit-out-of-range"
    || id.startsWith("range-")
    || id.startsWith("export-")
    || id === "exporting"
  ) return null;
  const bootstrap = fixtureBootstrap();
  if (id === "welcome") return { id, bootstrap, dialog: null, create: emptyCreateState() };
  if (id === "open") return { id, bootstrap, dialog: "open", create: emptyCreateState() };

  const create = fixtureCreateBase();
  if (id === "create-idle") {
    create.source = null;
  } else if (id === "create-running") {
    create.jobId = "fixture-inspection";
    create.jobKind = "inspection";
    create.analysisState = "running";
    create.phase = "正在读取扫描数据";
    create.progress = normalizedProgress({
      fraction: 0.38,
      bytes_read: 3019898880,
      total_bytes: 7937091790,
      parsed_spectra: 20754,
    });
  } else if (id === "create-cancelling") {
    create.jobId = "fixture-inspection";
    create.jobKind = "inspection";
    create.analysisState = "cancelling";
    create.phase = "正在安全取消";
    create.progress = normalizedProgress({
      fraction: 0.54,
      bytes_read: 4286377369,
      total_bytes: 7937091790,
      parsed_spectra: 29411,
    });
  } else if (id === "create-cancelled") {
    create.analysisState = "cancelled";
  } else if (id === "create-error") {
    create.analysisState = "error";
  } else if (id === "create-ready") {
    create.analysisState = "ready";
    create.inspection = normalizeInspection({
      inspection_token: "fixture-inspection-result",
      display_name: "Lin-_MPP.txt",
      available_range: { start_min: 0.5, end_min: 96 },
      scan_count: 54091,
      size_bytes: 7937091790,
    });
    create.target = { selectionToken: "fixture-target", displayName: "Lin− MPP 审阅项目" };
  }
  return { id, bootstrap, dialog: "create", create };
}
