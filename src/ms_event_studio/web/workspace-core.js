const STATUS_META = Object.freeze({
  unreviewed: Object.freeze({ label: "未审阅", shape: "triangle", code: "U" }),
  accepted: Object.freeze({ label: "已保留", shape: "circle", code: "A" }),
  rejected: Object.freeze({ label: "已排除", shape: "cross", code: "R" }),
  pending: Object.freeze({ label: "待定", shape: "diamond", code: "P" }),
});

const ORIGIN_LABELS = Object.freeze({
  automatic: "自动识别",
  manual_added: "人工补充",
  manual_adjusted: "人工调整",
});

export const REVIEW_FILTER_VALUES = Object.freeze([
  "all",
  "unreviewed",
  "accepted",
  "rejected",
  "pending",
  "manual_added",
  "manual_adjusted",
]);

export const REVIEW_FIXTURE_IDS = Object.freeze([
  "review-no-selection",
  "review-unreviewed-auto",
  "review-accepted-auto",
  "review-rejected-auto",
  "review-pending-auto",
  "review-manual",
  "review-highest",
  "review-edge",
  "review-dense",
]);

export const REVIEW_SAVE_FIXTURE_IDS = Object.freeze([
  "save-in-progress",
  "save-failed",
]);

export const EVENT_EDIT_FIXTURE_IDS = Object.freeze([
  "add-aim",
  "add-preview",
  "adjust-aim",
  "adjust-preview",
  "edit-out-of-range",
]);

export const RESPONSIVE_FIXTURE_IDS = Object.freeze([
  "undo-empty",
  "undo-redo-ready",
  "long-chinese-copy",
]);

export const WORKBENCH_FIXTURE_IDS = Object.freeze([
  ...REVIEW_FIXTURE_IDS,
  ...REVIEW_SAVE_FIXTURE_IDS,
  ...EVENT_EDIT_FIXTURE_IDS,
  ...RESPONSIVE_FIXTURE_IDS,
]);

export const REVIEW_DECISION_VALUES = Object.freeze({
  accepted: "keep",
  rejected: "exclude",
  pending: "pending",
  unreviewed: "clear",
});

export const PLOT_LAYOUT = Object.freeze({
  width: 1000,
  height: 600,
  content: Object.freeze({ left: 62, top: 26, right: 980, bottom: 542 }),
  // The 1000-unit viewBox renders at ~0.59 CSS px/unit in the compact
  // viewport. Seven units preserve the required >=4 CSS px visual inset.
  safeGap: 7,
  markerExtent: 9,
  topHeadroom: 0.1,
  legend: Object.freeze({ left: 72, top: 38, right: 378, bottom: 68 }),
});

// The event overlay remains complete. Only persistent time callouts are capped:
// eight evenly spread labels keep a full-range, high-event-count project
// readable while selection and hover still replace one slot on demand.
export const PLOT_LABEL_LIMIT = 8;

function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function nonNegative(value, fallback = 0) {
  return Math.max(0, finite(value, fallback));
}

function boundedInteger(value, fallback = 0, maximum = Number.MAX_SAFE_INTEGER) {
  return Math.min(maximum, Math.max(0, Math.round(finite(value, fallback))));
}

function safeText(value, fallback = "—", maximum = 160) {
  const text = String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return (text || fallback).slice(0, maximum);
}

function opaqueToken(value) {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, 256);
}

function reviewNote(value) {
  return String(value ?? "")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
    .slice(0, 500);
}

function editMode(value) {
  const mode = String(value || "");
  return mode === "add" || mode === "adjust" ? mode : null;
}

function normalizedPoint(value) {
  if (!value || typeof value !== "object") return null;
  const time = Number(value.time_min);
  const intensity = Number(value.intensity);
  if (!Number.isFinite(time) || !Number.isFinite(intensity)) return null;
  return { timeMin: time, intensity: Math.max(0, intensity) };
}

function normalizedInterval(value) {
  const start = Number(value?.start_min);
  const end = Number(value?.end_min);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return { startMin: start, endMin: end };
}

export function normalizeEventEditAim(value) {
  const payload = value && typeof value === "object" ? value : {};
  const mode = editMode(payload.mode);
  const token = opaqueToken(payload.aim_token);
  const allowedInterval = normalizedInterval(payload.allowed_interval);
  if (!mode || !token || !allowedInterval) throw new TypeError("编辑步骤响应无效。");
  return { token, mode, before: normalizedPoint(payload.before), allowedInterval };
}

export function normalizeEventEditPreview(value) {
  const payload = value && typeof value === "object" ? value : {};
  const mode = editMode(payload.mode);
  const token = opaqueToken(payload.preview_token);
  const candidatePoint = normalizedPoint(payload.candidate);
  const allowedInterval = normalizedInterval(payload.allowed_interval);
  const offset = Number(payload.candidate?.offset_sec);
  const before = normalizedPoint(payload.change?.before);
  const after = normalizedPoint(payload.change?.after);
  if (!mode || !token || !candidatePoint || !allowedInterval || !after) {
    throw new TypeError("候选预览响应无效。");
  }
  return {
    token,
    mode,
    candidate: {
      ...candidatePoint,
      offsetSec: Number.isFinite(offset) ? offset : 0,
    },
    change: { before, after },
    allowedInterval,
  };
}

export function eventEditAimBody(mode, event = null) {
  const normalizedMode = editMode(mode);
  if (normalizedMode === "add") return { mode: "add" };
  if (normalizedMode === "adjust") {
    return { mode: "adjust", action_token: requiredActionToken(event) };
  }
  throw new TypeError("不支持的编辑方式。");
}

export function eventEditPreviewBody(aimToken, clickTimeMin) {
  const token = opaqueToken(aimToken);
  const time = Number(clickTimeMin);
  if (!token || !Number.isFinite(time)) throw new TypeError("候选位置无效。");
  return { aim_token: token, click_time_min: time };
}

export function eventEditApplyBody(previewToken, note = "") {
  const token = opaqueToken(previewToken);
  if (!token) throw new TypeError("候选预览已经失效。");
  return { preview_token: token, note: reviewNote(note) };
}

export function eventEditCancelBody(editToken) {
  const token = opaqueToken(editToken);
  if (!token) throw new TypeError("编辑步骤已经失效。");
  return { edit_token: token };
}

export function eventEditDefaultTime(interval, before = null) {
  const normalized = normalizedInterval({
    start_min: interval?.startMin,
    end_min: interval?.endMin,
  });
  if (!normalized) return null;
  const preferred = Number(before?.timeMin);
  const fallback = normalized.startMin + (normalized.endMin - normalized.startMin) / 2;
  return Math.max(
    normalized.startMin,
    Math.min(normalized.endMin, Number.isFinite(preferred) ? preferred : fallback),
  );
}

export function eventEditKeyboardStep(interval) {
  const start = Number(interval?.startMin);
  const end = Number(interval?.endMin);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  const span = end - start;
  if (span === 0) return 0;
  return Math.max(span / 100, Number.EPSILON * Math.max(1, Math.abs(start), Math.abs(end)) * 8);
}

export function eventEditHitGeometry(
  interval,
  viewport,
  data = PLOT_LAYOUT.content,
  minimumHitWidth = 24,
) {
  const intervalStart = Number(interval?.startMin);
  const intervalEnd = Number(interval?.endMin);
  const viewportStart = Number(viewport?.start_min);
  const viewportEnd = Number(viewport?.end_min);
  const left = Number(data?.left);
  const right = Number(data?.right);
  if (
    !Number.isFinite(intervalStart)
    || !Number.isFinite(intervalEnd)
    || intervalEnd < intervalStart
    || !Number.isFinite(viewportStart)
    || !Number.isFinite(viewportEnd)
    || viewportEnd <= viewportStart
    || !Number.isFinite(left)
    || !Number.isFinite(right)
    || right <= left
  ) return null;
  const startMin = Math.max(viewportStart, intervalStart);
  const endMin = Math.min(viewportEnd, intervalEnd);
  if (endMin < startMin) return null;
  const span = viewportEnd - viewportStart;
  const xForTime = (time) => left + ((time - viewportStart) / span) * (right - left);
  const visibleLeft = xForTime(startMin);
  const visibleRight = xForTime(endMin);
  const visibleWidth = Math.max(0, visibleRight - visibleLeft);
  const requestedHitWidth = Math.max(visibleWidth, Math.max(0, Number(minimumHitWidth) || 0));
  const hitWidth = Math.min(right - left, requestedHitWidth);
  const center = (visibleLeft + visibleRight) / 2;
  const hitLeft = Math.max(left, Math.min(right - hitWidth, center - hitWidth / 2));
  return {
    startMin,
    endMin,
    visibleLeft,
    visibleRight,
    expanded: hitWidth > visibleWidth + Number.EPSILON,
    hitLeft,
    hitRight: hitLeft + hitWidth,
  };
}

export function eventEditTimeFromHitX(x, hit) {
  const position = Number(x);
  if (
    !Number.isFinite(position)
    || !hit
    || position < hit.hitLeft
    || position > hit.hitRight
  ) return null;
  const span = hit.endMin - hit.startMin;
  if (!(span > 0) || !(hit.hitRight > hit.hitLeft)) return hit.startMin;
  const fraction = Math.max(0, Math.min(1, (position - hit.hitLeft) / (hit.hitRight - hit.hitLeft)));
  // Stay just inside the canonical interval so floating-point serialization
  // cannot turn an accepted edge click into an out-of-range request.
  const edgeInset = Math.min(span / 4, Math.max(span * 1e-9, Number.EPSILON * 8));
  return hit.startMin + edgeInset + fraction * Math.max(0, span - 2 * edgeInset);
}

export function plotTimeFromClientPoint(svg, clientX, clientY, viewport, content = PLOT_LAYOUT.content) {
  if (!svg?.getScreenCTM || !svg.createSVGPoint) return null;
  const matrix = svg.getScreenCTM();
  if (!matrix) return null;
  const point = svg.createSVGPoint();
  point.x = Number(clientX);
  point.y = Number(clientY);
  const local = point.matrixTransform(matrix.inverse());
  if (
    !Number.isFinite(local.x)
    || !Number.isFinite(local.y)
    || local.x < content.left
    || local.x > content.right
    || local.y < content.top
    || local.y > content.bottom
  ) return null;
  const start = Number(viewport?.start_min);
  const end = Number(viewport?.end_min);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  const fraction = (local.x - content.left) / (content.right - content.left);
  return { timeMin: start + fraction * (end - start), x: local.x, y: local.y };
}

function requiredActionToken(event) {
  const token = opaqueToken(event?.actionToken);
  if (!token) throw new TypeError("当前事件没有可用的操作凭据。");
  return token;
}

export function reviewDecisionBody(event, status, note = "") {
  const decision = REVIEW_DECISION_VALUES[String(status || "")];
  if (!decision) throw new TypeError("不支持的审阅结论。");
  return {
    action_token: requiredActionToken(event),
    decision,
    note: reviewNote(note),
  };
}

export function restoreAutomaticApexBody(event, note = "") {
  return {
    action_token: requiredActionToken(event),
    note: reviewNote(note),
  };
}

export function reviewHistoryBody(note = "") {
  return { note: reviewNote(note) };
}

function statusValue(value) {
  const status = String(value || "").toLowerCase();
  return Object.hasOwn(STATUS_META, status) ? status : "unreviewed";
}

function originValue(value) {
  const origin = String(value || "").toLowerCase();
  return Object.hasOwn(ORIGIN_LABELS, origin) ? origin : "automatic";
}

export function normalizeReviewEvent(value) {
  const row = value && typeof value === "object" ? value : {};
  const status = statusValue(row.status);
  const origin = originValue(row.origin);
  const canonical = STATUS_META[status];
  const shape = ["triangle", "circle", "cross", "diamond"].includes(row.marker?.shape)
    ? row.marker.shape
    : canonical.shape;
  const markerColor = typeof row.marker?.color === "string"
    && /^#[0-9a-f]{6}$/i.test(row.marker.color)
    ? row.marker.color.toLowerCase()
    : "";
  return {
    eventToken: opaqueToken(row.event_token),
    actionToken: opaqueToken(row.action_token),
    sequence: boundedInteger(row.sequence, 0, 1_000_000),
    apexTimeMin: finite(row.apex_time_min, 0),
    apexTimeSec: finite(row.apex_time_sec, 0),
    apexIntensity: nonNegative(row.apex_intensity),
    status,
    statusLabel: safeText(row.status_label, canonical.label, 32),
    origin,
    originLabel: safeText(row.origin_label, ORIGIN_LABELS[origin], 32),
    marker: {
      shape,
      color: markerColor,
      code: ["U", "A", "R", "P"].includes(row.marker?.code)
        ? row.marker.code
        : canonical.code,
      dash: Array.isArray(row.marker?.dash)
        ? row.marker.dash.map((item) => nonNegative(item)).slice(0, 4)
        : [],
    },
    apexModified: Boolean(row.apex_modified),
    canRestoreAutomaticApex: Boolean(row.can_restore_automatic_apex),
  };
}

function normalizeProject(value) {
  const project = value && typeof value === "object" ? value : {};
  const range = project.analysis_range && typeof project.analysis_range === "object"
    ? project.analysis_range
    : {};
  const start = finite(range.start_min, 0);
  const end = Math.max(start, finite(range.end_min, start));
  return {
    displayName: safeText(project.display_name, "未命名项目", 160),
    analysisRange: { start_min: start, end_min: end },
    eventCount: boundedInteger(project.event_count),
  };
}

function normalizeReview(value) {
  const review = value && typeof value === "object" ? value : {};
  return {
    total: boundedInteger(review.total),
    reviewed: boundedInteger(review.reviewed),
    unreviewed: boundedInteger(review.unreviewed),
    accepted: boundedInteger(review.accepted),
    rejected: boundedInteger(review.rejected),
    pending: boundedInteger(review.pending),
  };
}

function normalizeFilters(value, review) {
  const labels = {
    all: "全部事件",
    unreviewed: "未审阅",
    accepted: "已保留",
    rejected: "已排除",
    pending: "待定",
    manual_added: "人工补充",
    manual_adjusted: "人工调整",
  };
  const counts = {
    all: review.total,
    unreviewed: review.unreviewed,
    accepted: review.accepted,
    rejected: review.rejected,
    pending: review.pending,
    manual_added: 0,
    manual_adjusted: 0,
  };
  const rows = Array.isArray(value) ? value : [];
  const byValue = new Map(
    rows
      .filter((row) => row && REVIEW_FILTER_VALUES.includes(String(row.value || "")))
      .map((row) => [String(row.value), row]),
  );
  return REVIEW_FILTER_VALUES.map((filter) => ({
    value: filter,
    label: safeText(byValue.get(filter)?.label, labels[filter], 40),
    count: boundedInteger(byValue.get(filter)?.count, counts[filter]),
  }));
}

function normalizeQuality(value) {
  const quality = value && typeof value === "object" ? value : {};
  const level = ["ok", "attention"].includes(quality.level)
    ? quality.level
    : "attention";
  return {
    level,
    label: safeText(quality.label, level === "ok" ? "质量良好" : "建议关注", 80),
    notes: (Array.isArray(quality.notes) ? quality.notes : [])
      .map((note) => safeText(note, "", 120))
      .filter(Boolean)
      .slice(0, 8),
  };
}

function nullableFinite(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function formatAdjustmentRange(value) {
  if (!value || typeof value !== "object") return "—";
  const start = nullableFinite(value.start_sec);
  const end = nullableFinite(value.end_sec);
  return start === null || end === null
    ? "—"
    : `${start.toFixed(3)}–${end.toFixed(3)} s`;
}

function normalizeEvidence(selection) {
  const core = selection?.core_evidence && typeof selection.core_evidence === "object"
    ? selection.core_evidence
    : {};
  const more = selection?.more_evidence && typeof selection.more_evidence === "object"
    ? selection.more_evidence
    : {};
  const adjustment = more.adjustment_range && typeof more.adjustment_range === "object"
    ? more.adjustment_range
    : null;
  return {
    core: {
      pc34Intensity: nullableFinite(core.pc34_intensity),
      measuredMz: nullableFinite(core.measured_mz),
      massErrorPpm: nullableFinite(core.mass_error_ppm),
      quality: normalizeQuality(core.quality),
    },
    more: {
      scanNumber: safeText(more.scan_number, "—", 80),
      ms782Intensity: nullableFinite(more.ms782_intensity),
      tic: nullableFinite(more.tic),
      prominence: nullableFinite(more.prominence),
      physicalWidthSec: nullableFinite(more.physical_width_sec),
      adjustmentRange: adjustment
        ? {
            start_sec: nullableFinite(adjustment.start_sec),
            end_sec: nullableFinite(adjustment.end_sec),
          }
        : null,
      adjustmentOffsetSec: nullableFinite(more.adjustment_offset_sec),
    },
  };
}

function normalizeWindow(value, project) {
  const windowValue = value && typeof value === "object" ? value : {};
  const viewport = windowValue.viewport && typeof windowValue.viewport === "object"
    ? windowValue.viewport
    : {};
  const analysisStart = finite(viewport.analysis_start_min, project.analysisRange.start_min);
  const analysisEnd = Math.max(
    analysisStart,
    finite(viewport.analysis_end_min, project.analysisRange.end_min),
  );
  const start = Math.max(analysisStart, finite(viewport.start_min, analysisStart));
  const end = Math.min(analysisEnd, Math.max(start, finite(viewport.end_min, analysisEnd)));
  const trace = Array.isArray(windowValue.trace) ? windowValue.trace : [];
  const overlay = Array.isArray(windowValue.event_overlay) ? windowValue.event_overlay : [];
  const labels = Array.isArray(windowValue.label_event_tokens)
    ? windowValue.label_event_tokens.map(opaqueToken).filter(Boolean)
    : [];
  return {
    viewport: {
      start_min: start,
      end_min: end,
      analysis_start_min: analysisStart,
      analysis_end_min: analysisEnd,
    },
    trace: trace
      .map((point) => ({
        timeMin: finite(point?.time_min, Number.NaN),
        intensity: nonNegative(point?.intensity, Number.NaN),
      }))
      .filter((point) => Number.isFinite(point.timeMin) && Number.isFinite(point.intensity))
      .sort((left, right) => left.timeMin - right.timeMin)
      .slice(0, 20_000),
    eventOverlay: overlay
      .map(normalizeReviewEvent)
      .filter((event) => event.eventToken)
      .sort((left, right) => left.apexTimeMin - right.apexTimeMin)
      .slice(0, 5_000),
    labelEventTokens: labels.slice(0, 200),
  };
}

export function normalizeWorkspace(value) {
  const payload = value && typeof value === "object" ? value : {};
  const project = normalizeProject(payload.project);
  const review = normalizeReview(payload.review);
  const events = (Array.isArray(payload.events) ? payload.events : [])
    .map(normalizeReviewEvent)
    .filter((event) => event.eventToken)
    .sort((left, right) => left.sequence - right.sequence || left.apexTimeMin - right.apexTimeMin)
    .slice(0, 5_000);
  const byToken = new Map(events.map((event) => [event.eventToken, event]));
  const rawSelection = payload.selection && typeof payload.selection === "object"
    ? payload.selection
    : {};
  const selectedRaw = rawSelection.event ? normalizeReviewEvent(rawSelection.event) : null;
  const selected = selectedRaw?.eventToken
    ? byToken.get(selectedRaw.eventToken) || selectedRaw
    : null;
  const selectedIndex = selected
    ? events.findIndex((event) => event.eventToken === selected.eventToken)
    : -1;
  const evidence = normalizeEvidence(rawSelection);
  return {
    project,
    review,
    filters: normalizeFilters(payload.filters, review),
    events,
    selection: {
      event: selected,
      index: selected ? (selectedIndex >= 0 ? selectedIndex : null) : null,
      total: boundedInteger(rawSelection.total, events.length),
      previousEventToken: opaqueToken(rawSelection.previous_event_token),
      nextEventToken: opaqueToken(rawSelection.next_event_token),
      nextUnreviewedEventToken: opaqueToken(rawSelection.next_unreviewed_event_token),
      ...evidence,
    },
    window: normalizeWindow(payload.window, project),
    history: {
      canUndo: Boolean(payload.history?.can_undo),
      canRedo: Boolean(payload.history?.can_redo),
    },
  };
}

function normalizedWorkspaceValue(value) {
  if (
    value
    && typeof value === "object"
    && value.project?.analysisRange
    && Array.isArray(value.events)
    && Array.isArray(value.window?.trace)
    && Array.isArray(value.window?.eventOverlay)
  ) {
    return value;
  }
  return normalizeWorkspace(value);
}

function scaleFraction(value, maximum, logScale) {
  if (maximum <= 0) return 0;
  if (logScale) return Math.log1p(Math.max(0, value)) / Math.log1p(maximum);
  return Math.max(0, value) / maximum;
}

function markerPath(shape, x, y, radius) {
  const r = radius;
  if (shape === "triangle") {
    return `M ${x} ${y - r} L ${x + r} ${y + r} L ${x - r} ${y + r} Z`;
  }
  if (shape === "diamond") {
    return `M ${x} ${y - r} L ${x + r} ${y} L ${x} ${y + r} L ${x - r} ${y} Z`;
  }
  if (shape === "cross") {
    const arm = r * 0.72;
    return `M ${x - arm} ${y - arm} L ${x + arm} ${y + arm} M ${x + arm} ${y - arm} L ${x - arm} ${y + arm}`;
  }
  return "";
}

export function buildPlotGeometry(workspace, { logScale = false, layout = PLOT_LAYOUT } = {}) {
  const normalized = normalizedWorkspaceValue(workspace);
  const content = layout.content;
  const inset = layout.safeGap + layout.markerExtent;
  const data = {
    left: content.left + inset,
    top: content.top + inset,
    right: content.right - inset,
    bottom: content.bottom - inset,
  };
  const viewport = normalized.window.viewport;
  const span = Math.max(Number.EPSILON, viewport.end_min - viewport.start_min);
  const values = [
    ...normalized.window.trace.map((point) => point.intensity),
    ...normalized.window.eventOverlay.map((event) => event.apexIntensity),
  ];
  const observedMaximum = Math.max(0, ...values);
  const scaleMaximum = observedMaximum <= 0
    ? 1
    : logScale
      ? Math.expm1(Math.log1p(observedMaximum) / (1 - layout.topHeadroom))
      : observedMaximum / (1 - layout.topHeadroom);
  const xForTime = (time) => {
    const fraction = Math.max(0, Math.min(1, (time - viewport.start_min) / span));
    return data.left + fraction * (data.right - data.left);
  };
  const yForSignal = (intensity) => {
    const fraction = Math.max(0, Math.min(1, scaleFraction(intensity, scaleMaximum, logScale)));
    return data.bottom - fraction * (data.bottom - data.top);
  };
  const tracePoints = normalized.window.trace.map((point) => ({
    x: xForTime(point.timeMin),
    y: yForSignal(point.intensity),
    ...point,
  }));
  const markers = normalized.window.eventOverlay.map((event) => {
    const x = xForTime(event.apexTimeMin);
    const y = yForSignal(event.apexIntensity);
    const radius = layout.markerExtent;
    return {
      event,
      x,
      y,
      radius,
      path: markerPath(event.marker.shape, x, y, radius),
      bounds: {
        left: x - radius,
        top: y - radius,
        right: x + radius,
        bottom: y + radius,
      },
    };
  });
  return {
    layout,
    content,
    data,
    observedMaximum,
    scaleMaximum,
    headroomFraction: observedMaximum > 0
      ? (yForSignal(observedMaximum) - data.top) / (data.bottom - data.top)
      : layout.topHeadroom,
    tracePoints,
    tracePath: tracePoints.length
      ? tracePoints.map((point, index) => `${index ? "L" : "M"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ")
      : "",
    markers,
    xForTime,
    yForSignal,
  };
}

function rectanglesOverlap(left, right, gap = 4) {
  return !(
    left.right + gap <= right.left
    || right.right + gap <= left.left
    || left.bottom + gap <= right.top
    || right.bottom + gap <= left.top
  );
}

export function placePlotLabels(geometry, labelTokens, selectedToken = "", maximum = PLOT_LABEL_LIMIT) {
  const allowed = new Set((Array.isArray(labelTokens) ? labelTokens : []).map(opaqueToken));
  const selected = opaqueToken(selectedToken);
  const candidates = geometry.markers
    .filter(({ event }) => event.eventToken === selected || allowed.has(event.eventToken))
    .sort((left, right) => (
      Number(right.event.eventToken === selected) - Number(left.event.eventToken === selected)
      || left.x - right.x
    ));
  const selectedMarker = candidates.find(({ event }) => event.eventToken === selected) || null;
  const placements = [];
  for (const marker of candidates) {
    if (placements.length >= Math.max(0, Number(maximum) || 0) && marker.event.eventToken !== selected) {
      continue;
    }
    const text = `${marker.event.apexTimeMin.toFixed(3)} min`;
    const width = Math.max(70, 16 + text.length * 6.4);
    const height = 24;
    if (
      selectedMarker
      && marker.event.eventToken !== selected
      && Math.abs(marker.x - selectedMarker.x) < width + 24
      && Math.abs(marker.y - selectedMarker.y) < height * 3
    ) {
      continue;
    }
    const safeLeft = geometry.content.left + geometry.layout.safeGap + 1;
    const safeTop = geometry.content.top + geometry.layout.safeGap + 1;
    const safeRight = geometry.content.right - geometry.layout.safeGap - 1;
    const safeBottom = geometry.content.bottom - geometry.layout.safeGap - 1;
    const clampBox = (left, top) => {
      const x = Math.max(safeLeft, Math.min(left, safeRight - width));
      const y = Math.max(safeTop, Math.min(top, safeBottom - height));
      return { left: x, top: y, right: x + width, bottom: y + height };
    };
    const candidateBoxes = [
      clampBox(marker.x - width / 2, marker.y - marker.radius - height - 8),
      clampBox(marker.x - width / 2, marker.y + marker.radius + 8),
      clampBox(marker.x + marker.radius + 10, marker.y - height / 2),
      clampBox(marker.x - marker.radius - width - 10, marker.y - height / 2),
    ];
    const box = candidateBoxes.find((candidate) => (
      !rectanglesOverlap(candidate, geometry.layout.legend, geometry.layout.safeGap)
      && !placements.some((placement) => rectanglesOverlap(candidate, placement.box))
    ));
    if (!box) continue;
    placements.push({
      event: marker.event,
      marker,
      text,
      selected: marker.event.eventToken === selected,
      box,
      anchorX: Math.max(box.left + 8, Math.min(marker.x, box.right - 8)),
      anchorY: box.bottom <= marker.y ? box.bottom : box.top >= marker.y ? box.top : marker.y,
    });
  }
  return placements;
}

function fixtureEvent(index, status = "unreviewed", origin = "automatic", overrides = {}) {
  const meta = STATUS_META[status];
  const time = 31.6 + index * 0.22;
  const intensity = 900_000 + ((index * 37) % 9) * 185_000;
  return {
    event_token: `fixture-event-${index + 1}`,
    action_token: `fixture-action-${index + 1}`,
    sequence: index + 1,
    apex_time_min: time,
    apex_time_sec: time * 60,
    apex_intensity: intensity,
    status,
    status_label: meta.label,
    origin,
    origin_label: ORIGIN_LABELS[origin],
    marker: { shape: meta.shape, code: meta.code, dash: origin === "automatic" ? [] : [4, 2] },
    apex_modified: origin === "manual_adjusted",
    can_restore_automatic_apex: origin === "manual_adjusted",
    ...overrides,
  };
}

function fixtureTrace(start, end, events, points = 240) {
  return Array.from({ length: points }, (_, index) => {
    const time = start + (index / (points - 1)) * (end - start);
    let intensity = 150_000 + 28_000 * (1 + Math.sin(index * 0.31));
    for (const event of events) {
      const distance = (time - event.apex_time_min) / 0.028;
      intensity += event.apex_intensity * Math.exp(-0.5 * distance * distance);
    }
    return { time_min: time, intensity: Math.max(0, intensity) };
  });
}

function rawFixtureWorkspace(id) {
  let events = [
    fixtureEvent(0, "accepted"),
    fixtureEvent(1, "unreviewed"),
    fixtureEvent(2, "pending"),
    fixtureEvent(3, "rejected"),
    fixtureEvent(4, "unreviewed"),
    fixtureEvent(5, "accepted"),
    fixtureEvent(6, "unreviewed"),
  ];
  let selectedIndex = 1;
  const visualId = REVIEW_SAVE_FIXTURE_IDS.includes(id) || EVENT_EDIT_FIXTURE_IDS.includes(id)
    ? "review-unreviewed-auto"
    : id;
  if (visualId === "review-no-selection") selectedIndex = -1;
  if (visualId === "review-accepted-auto") selectedIndex = 0;
  if (visualId === "review-rejected-auto") selectedIndex = 3;
  if (visualId === "review-pending-auto") selectedIndex = 2;
  if (visualId === "review-manual") {
    events[1] = fixtureEvent(1, "accepted", "manual_adjusted", {
      apex_time_min: 31.842,
      apex_time_sec: 1910.52,
      apex_modified: true,
      can_restore_automatic_apex: true,
    });
  }
  if (visualId === "review-highest") {
    events[1] = fixtureEvent(1, "unreviewed", "automatic", { apex_intensity: 9_850_000 });
  }
  if (visualId === "review-edge") {
    events[1] = fixtureEvent(1, "unreviewed", "automatic", {
      apex_time_min: 31.5,
      apex_time_sec: 1890,
    });
  }
  if (visualId === "review-dense") {
    const statuses = ["unreviewed", "accepted", "rejected", "pending"];
    events = Array.from({ length: 22 }, (_, index) => fixtureEvent(
      index,
      statuses[index % statuses.length],
      index === 9 ? "manual_added" : "automatic",
      {
        apex_time_min: 32.18 + index * 0.013,
        apex_time_sec: (32.18 + index * 0.013) * 60,
        apex_intensity: 650_000 + (index % 6) * 180_000,
      },
    ));
    selectedIndex = 9;
  }
  if (visualId === "long-chinese-copy") {
    events[1] = fixtureEvent(1, "pending", "manual_adjusted", {
      status_label: "待补充交叉批次证据后再作最终判断",
      origin_label: "人工调整并保留原始自动峰顶作为对照来源",
      apex_modified: true,
      can_restore_automatic_apex: true,
    });
  }
  const selected = selectedIndex >= 0 ? events[selectedIndex] : null;
  const start = visualId === "review-dense" ? 32.1 : 31.5;
  const end = visualId === "review-dense" ? 32.55 : 33.5;
  const reviewed = events.filter((event) => event.status !== "unreviewed").length;
  const counts = Object.fromEntries(
    ["unreviewed", "accepted", "rejected", "pending"].map((status) => [
      status,
      events.filter((event) => event.status === status).length,
    ]),
  );
  const manualCounts = {
    manual_added: events.filter((event) => event.origin === "manual_added").length,
    manual_adjusted: events.filter((event) => event.origin === "manual_adjusted").length,
  };
  return {
    project: {
      display_name: visualId === "long-chinese-copy"
        ? "Lin MPP 阴离子模式脂质组学多批次复核与跨年度长期随访审阅项目：质控样本、实验样本、空白对照与重复进样联合分析；涵盖华东中心、华南中心及海外合作实验室 Phase Two Review and Long-term Quality Follow-up"
        : "Lin− MPP 审阅项目",
      analysis_range: { start_min: 0.5, end_min: 96 },
      event_count: events.length,
    },
    review: {
      total: events.length,
      reviewed,
      ...counts,
    },
    filters: REVIEW_FILTER_VALUES.map((value) => ({
      value,
      label: value === "all"
        ? "全部事件"
        : STATUS_META[value]?.label || ORIGIN_LABELS[value],
      count: value === "all" ? events.length : counts[value] ?? manualCounts[value],
    })),
    events,
    selection: {
      event: selected,
      index: selectedIndex,
      total: events.length,
      previous_event_token: selectedIndex > 0 ? events[selectedIndex - 1].event_token : null,
      next_event_token: selectedIndex >= 0 && selectedIndex < events.length - 1
        ? events[selectedIndex + 1].event_token
        : null,
      next_unreviewed_event_token: events.find((event, index) => (
        index > selectedIndex && event.status === "unreviewed"
      ))?.event_token || null,
      core_evidence: selected
        ? {
            pc34_intensity: selected.apex_intensity,
            measured_mz: 760.5853,
            mass_error_ppm: 0.3,
            quality: visualId === "long-chinese-copy"
              ? {
                  level: "attention",
                  label: "需要结合相邻扫描与同批次质控样本共同判断",
                  notes: [
                    "该峰附近存在低强度肩峰，建议对照前后扫描中的同位素分布后再确认。",
                    "本事件来自人工调整；原始自动峰顶仍可恢复，当前审阅结论不会因此自动改变。",
                  ],
                }
              : { level: "ok", label: "质量良好", notes: [] },
          }
        : null,
      more_evidence: selected
        ? {
            scan_number: `scan=${34_180 + Math.max(0, selectedIndex)}`,
            ms782_intensity: 184_203,
            tic: 12_840_000,
            prominence: 812_400,
            physical_width_sec: 1.28,
            adjustment_range: {
              start_sec: selected.apex_time_sec - 1.08,
              end_sec: selected.apex_time_sec + 1.26,
            },
            adjustment_offset_sec: selected.origin === "automatic" ? null : 0.18,
          }
        : null,
    },
    window: {
      viewport: {
        start_min: start,
        end_min: end,
        analysis_start_min: 0.5,
        analysis_end_min: 96,
      },
      trace: fixtureTrace(start, end, events),
      event_overlay: events,
      label_event_tokens: events.filter((_, index) => index % 3 === 0).map((event) => event.event_token),
    },
    history: visualId === "undo-empty"
      ? { can_undo: false, can_redo: false }
      : visualId === "undo-redo-ready"
        ? { can_undo: true, can_redo: true }
        : { can_undo: true, can_redo: false },
  };
}

export function fixtureWorkspace(id) {
  if (!WORKBENCH_FIXTURE_IDS.includes(id)) return null;
  return normalizeWorkspace(rawFixtureWorkspace(id));
}

export function fixtureEventEdit(id) {
  if (!EVENT_EDIT_FIXTURE_IDS.includes(id)) return null;
  const adjust = id.startsWith("adjust-") || id === "edit-out-of-range";
  const preview = id.endsWith("-preview");
  const before = adjust ? { timeMin: 31.82, intensity: 1_085_000 } : null;
  const allowedInterval = adjust
    ? { startMin: 31.76, endMin: 31.9 }
    : { startMin: 0.5, endMin: 96 };
  if (preview) {
    const candidate = {
      timeMin: adjust ? 31.842 : 32.04,
      intensity: adjust ? 1_240_000 : 1_115_000,
      offsetSec: adjust ? 1.32 : 0.18,
    };
    return {
      state: "preview",
      mode: adjust ? "adjust" : "add",
      token: `fixture-preview-${adjust ? "adjust" : "add"}`,
      before,
      candidate,
      change: { before, after: { timeMin: candidate.timeMin, intensity: candidate.intensity } },
      allowedInterval,
      error: "",
      busy: false,
      hoverTimeMin: null,
    };
  }
  return {
    state: id === "edit-out-of-range" ? "error" : "aiming",
    mode: adjust ? "adjust" : "add",
    token: `fixture-aim-${adjust ? "adjust" : "add"}`,
    before,
    candidate: null,
    change: null,
    allowedInterval,
    error: id === "edit-out-of-range"
      ? "点击位置不在允许的调整区间内。请在高亮范围中重新选择。"
      : "",
    busy: false,
    hoverTimeMin: null,
  };
}

export function workspaceRequestBody(workspace, {
  selectedEventToken = null,
  statusFilter = "all",
  startMin = null,
  endMin = null,
  pointBudget = 2_000,
  maximumLabels = PLOT_LABEL_LIMIT,
} = {}) {
  const normalized = normalizedWorkspaceValue(workspace);
  const viewport = normalized.window.viewport;
  const hasStart = startMin !== null && startMin !== undefined && startMin !== "";
  const hasEnd = endMin !== null && endMin !== undefined && endMin !== "";
  const start = hasStart && Number.isFinite(Number(startMin))
    ? Number(startMin)
    : viewport.start_min;
  const end = hasEnd && Number.isFinite(Number(endMin))
    ? Number(endMin)
    : viewport.end_min;
  return {
    start_min: Math.max(viewport.analysis_start_min, Math.min(start, viewport.analysis_end_min)),
    end_min: Math.min(viewport.analysis_end_min, Math.max(end, viewport.analysis_start_min)),
    point_budget: Math.min(20_000, Math.max(32, Math.round(Number(pointBudget) || 2_000))),
    status_filter: REVIEW_FILTER_VALUES.includes(statusFilter) ? statusFilter : "all",
    selected_event_token: opaqueToken(selectedEventToken) || null,
    maximum_labels: Math.min(100, Math.max(0, Math.round(Number(maximumLabels) || 0))),
  };
}
