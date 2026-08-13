import {
  API_ENDPOINTS,
  FIXTURE_IDS,
  PATH_ROLES,
  REQUEST_TOKEN_HEADER,
  allowedFixture,
  clampFraction,
  emptyCreateState,
  fixtureScenario,
  formatBytes,
  formatCount,
  formatMinute,
  formatRange,
  normalizeBootstrap,
  normalizeInspection,
  normalizeJob,
  normalizeProject,
  safeDisplayName,
  suggestedProjectName,
  validateAnalysisRange,
} from "./app-core.js";
import {
  REVIEW_FILTER_VALUES,
  PLOT_LAYOUT,
  WORKBENCH_FIXTURE_IDS,
  buildPlotGeometry,
  eventEditAimBody,
  eventEditApplyBody,
  eventEditCancelBody,
  eventEditDefaultTime,
  eventEditHitGeometry,
  eventEditKeyboardStep,
  eventEditPreviewBody,
  eventEditTimeFromHitX,
  fixtureEventEdit,
  fixtureWorkspace,
  formatAdjustmentRange,
  normalizeEventEditAim,
  normalizeEventEditPreview,
  normalizeWorkspace,
  PLOT_LABEL_LIMIT,
  placePlotLabels,
  plotTimeFromClientPoint,
  restoreAutomaticApexBody,
  reviewDecisionBody,
  reviewHistoryBody,
  workspaceRequestBody,
} from "./workspace-core.js";
import {
  RANGE_EXPORT_FIXTURE_IDS,
  auditExportBody,
  emptyExportFlow,
  emptyRangeFlow,
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
} from "./range-export-core.js";

const POLL_INTERVAL_MS = 350;
const DEFAULT_ERROR_MESSAGE = "操作未完成。请检查当前选择后重试。";

function emptyEventEditState() {
  return {
    state: "selected",
    mode: null,
    token: "",
    before: null,
    candidate: null,
    change: null,
    allowedInterval: null,
    error: "",
    busy: false,
    hoverTimeMin: null,
    keyboardTimeMin: null,
  };
}

const state = {
  ready: false,
  fixture: null,
  bootstrap: normalizeBootstrap(null),
  view: "loading",
  activeProject: null,
  create: emptyCreateState(),
  modal: null,
  pollGeneration: 0,
  pollTimer: null,
  toastTimer: null,
  openBusy: false,
  workspace: null,
  workspaceBusy: false,
  workspaceFilter: "all",
  workspaceScale: "linear",
  workspaceLabels: true,
  moreEvidenceExpanded: false,
  hoveredEventToken: "",
  reviewSaveState: "idle",
  reviewError: "",
  eventEdit: emptyEventEditState(),
  rangeFlow: emptyRangeFlow(),
  exportFlow: emptyExportFlow(),
  operationGeneration: 0,
  operationTimer: null,
};

const element = (id) => document.getElementById(id);

function publicState() {
  return {
    ready: state.ready,
    fixture: state.fixture,
    view: state.view,
    modal: state.modal,
    analysis: {
      state: state.create.analysisState,
      phase: state.create.phase,
      fraction: clampFraction(state.create.progress?.fraction),
      sourceSelected: Boolean(state.create.source),
      targetSelected: Boolean(state.create.target),
      inspectionReady: Boolean(state.create.inspection),
    },
    activeProject: state.activeProject
      ? {
          displayName: state.activeProject.displayName,
          analysisRange: { ...state.activeProject.analysisRange },
          eventCount: state.activeProject.eventCount,
        }
      : null,
    workbench: state.workspace
      ? {
          selectedEventKey: state.workspace.selection.event?.eventToken || null,
          status: state.workspace.selection.event?.status || null,
          source: state.workspace.selection.event?.origin || null,
          filter: state.workspaceFilter,
          scale: state.workspaceScale,
          labels: state.workspaceLabels,
           moreEvidenceExpanded: state.moreEvidenceExpanded,
          saving: state.reviewSaveState === "saving",
          saveState: state.reviewSaveState,
          canUndo: state.workspace.history.canUndo,
          canRedo: state.workspace.history.canRedo,
          eventIndex: state.workspace.selection.index,
          eventCount: state.workspace.selection.total,
          viewport: { ...state.workspace.window.viewport },
          editState: state.eventEdit.state,
          editMode: state.eventEdit.mode,
          editTokenPresent: Boolean(state.eventEdit.token),
          candidateTimeMin: state.eventEdit.candidate?.timeMin ?? null,
          editKeyboardTimeMin: state.eventEdit.keyboardTimeMin ?? null,
          allowedInterval: state.eventEdit.allowedInterval
            ? { ...state.eventEdit.allowedInterval }
            : null,
          rangeState: state.rangeFlow.state,
          rangePreviewPresent: Boolean(state.rangeFlow.previewToken),
          rangeOld: state.rangeFlow.oldRange ? { ...state.rangeFlow.oldRange } : null,
          rangeNew: state.rangeFlow.newRange ? { ...state.rangeFlow.newRange } : null,
          rangeImpacts: state.rangeFlow.impacts ? { ...state.rangeFlow.impacts } : null,
          rangeJobCancellable: state.rangeFlow.jobCancellable,
          exportState: state.exportFlow.state,
          exportKind: state.exportFlow.kind,
          exportTargetSelected: Boolean(state.exportFlow.target),
          exportIncludePending: state.exportFlow.includePending,
          exportJobCancellable: state.exportFlow.jobCancellable,
          exportResult: state.exportFlow.result ? { ...state.exportFlow.result } : null,
        }
      : null,
  };
}

let resolveReady;
const readyPromise = new Promise((resolve) => {
  resolveReady = resolve;
});

Object.defineProperty(window, "__MS_EVENT_STUDIO__", {
  configurable: false,
  enumerable: false,
  writable: false,
  value: Object.freeze({
    ready: readyPromise,
    fixtureIds: Object.freeze([...FIXTURE_IDS]),
    getState: publicState,
  }),
});

class ApiError extends Error {
  constructor(message, { code = "", status = 0 } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function errorMessageFor(status, code) {
  if (status === 503 || code === "native_dialog_unavailable") {
    return "当前无法打开系统选择窗口。请从桌面应用中重试。";
  }
  if (status === 409) return "当前状态已经变化，请重新打开此步骤后再试。";
  if (status === 400 || status === 422) return "当前填写内容无法使用，请检查后重试。";
  if (status >= 500) return "本地服务暂时无法完成操作，请稍后重试。";
  return DEFAULT_ERROR_MESSAGE;
}

async function apiRequest(path, { method = "GET", body } = {}) {
  if (typeof path !== "string" || !path.startsWith("/api/")) {
    throw new ApiError("请求地址不受支持。", { code: "invalid_endpoint" });
  }
  const upperMethod = String(method).toUpperCase();
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (upperMethod !== "GET") {
    if (!state.bootstrap.requestToken) {
      throw new ApiError("应用尚未准备好，请稍后重试。", { code: "missing_request_token" });
    }
    headers[REQUEST_TOKEN_HEADER] = state.bootstrap.requestToken;
  }

  let response;
  try {
    response = await fetch(path, {
      method: upperMethod,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
    });
  } catch (_error) {
    throw new ApiError("无法连接本地服务，请重新启动应用。", { code: "connection_failed" });
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    if (response.ok) {
      throw new ApiError("本地服务返回了无法读取的结果。", {
        code: "invalid_response",
        status: response.status,
      });
    }
  }

  if (!response.ok) {
    const code = typeof payload?.error?.code === "string"
      ? payload.error.code
      : typeof payload?.code === "string"
        ? payload.code
        : "";
    const serverMessage = typeof payload?.error?.message === "string"
      ? visibleMessage(payload.error.message, "")
      : "";
    throw new ApiError(serverMessage || errorMessageFor(response.status, code), {
      code,
      status: response.status,
    });
  }
  if (!payload || typeof payload !== "object") {
    throw new ApiError("本地服务没有返回可用结果。", { code: "empty_response" });
  }
  return payload;
}

function post(path, body) {
  return apiRequest(path, { method: "POST", body });
}

function setText(id, value) {
  const node = element(id);
  if (node) node.textContent = String(value ?? "");
}

function showToast(message, tone = "info", { persistent = false } = {}) {
  const toast = element("toast");
  if (!toast) return;
  if (state.toastTimer !== null) {
    window.clearTimeout(state.toastTimer);
    state.toastTimer = null;
  }
  setText("toastMessage", message);
  toast.dataset.tone = tone;
  toast.setAttribute("role", tone === "error" ? "alert" : "status");
  toast.setAttribute("aria-live", tone === "error" ? "assertive" : "polite");
  toast.hidden = false;
  if (!persistent) {
    state.toastTimer = window.setTimeout(() => {
      toast.hidden = true;
      state.toastTimer = null;
    }, 2600);
  }
}

function clearPoll() {
  state.pollGeneration += 1;
  if (state.pollTimer !== null) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function busyCreateState() {
  return ["running", "cancelling", "creating"].includes(state.create.analysisState);
}

function setView(view) {
  state.view = view === "project" ? "project" : "welcome";
  document.body.dataset.view = state.view;
  element("loadingView").hidden = true;
  element("welcomeView").hidden = state.view !== "welcome";
  element("projectView").hidden = state.view !== "project";
  const hasProject = state.view === "project" && Boolean(state.activeProject);
  element("projectContext").hidden = !hasProject;
  element("headerActions").hidden = !hasProject;
  if (hasProject) {
    renderActiveProject();
    if (state.workspace) renderWorkspace();
  }
}

function renderActiveProject() {
  const project = state.activeProject;
  if (!project) return;
  element("projectContext").dataset.longName = [...project.displayName].length >= 80
    ? "true"
    : "false";
  setText("headerProjectName", project.displayName);
  setText("headerProjectRange", formatRange(project.analysisRange));
  setText("projectRange", formatRange(project.analysisRange));
  setText("projectEventCount", formatCount(project.eventCount));
}

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
function svgElement(name, attributes = {}) {
  const node = document.createElementNS(SVG_NAMESPACE, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function workspaceNumber(value, digits = 3) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (Math.abs(number) >= 1_000_000) return number.toExponential(3);
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function selectedWorkspaceEvent() {
  return state.workspace?.selection.event || null;
}

function reviewSaving() {
  return state.reviewSaveState === "saving";
}

function eventEditActive() {
  return state.eventEdit.state !== "selected";
}

function projectOperationActive() {
  return state.rangeFlow.state !== "closed" || state.exportFlow.state !== "closed";
}

function pendingWorkbenchOperation() {
  return state.workspaceBusy
    || reviewSaving()
    || state.eventEdit.busy
    || ["calculating", "applying"].includes(state.rangeFlow.state)
    || state.exportFlow.state === "exporting";
}

function workbenchBusy() {
  return pendingWorkbenchOperation() || eventEditActive() || projectOperationActive();
}

function clearReviewFeedback() {
  if (reviewSaving()) return;
  state.reviewSaveState = "idle";
  state.reviewError = "";
}

function visibleMessage(value, fallback) {
  const text = String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (text || fallback).slice(0, 240);
}

function renderWorkspaceHeader() {
  if (!state.workspace) return;
  const { project } = state.workspace;
  state.activeProject = {
    displayName: project.displayName,
    analysisRange: { ...project.analysisRange },
    eventCount: project.eventCount,
  };
  element("projectContext").dataset.longName = [...project.displayName].length >= 80
    ? "true"
    : "false";
  setText("headerProjectName", project.displayName);
  setText("headerProjectRange", formatRange(project.analysisRange));
}

function renderWorkspaceProgress() {
  const review = state.workspace.review;
  const fraction = review.total ? review.reviewed / review.total : 0;
  setText("reviewedCount", review.reviewed);
  setText("reviewTotal", review.total);
  setText("reviewProgressPercent", `${Math.round(fraction * 100)}%`);
  element("reviewProgressBar").value = fraction;
  element("reviewProgressBar").textContent = `${Math.round(fraction * 100)}%`;
  element("previousEvent").disabled = workbenchBusy() || !state.workspace.selection.previousEventToken;
  element("nextEvent").disabled = workbenchBusy() || !state.workspace.selection.nextEventToken;
}

function renderReviewDecision(event) {
  const unavailable = workbenchBusy() || !event.actionToken;
  document.querySelectorAll("#reviewSegmented [role='radio']").forEach((button) => {
    button.setAttribute("aria-checked", String(button.dataset.decision === event.status));
    button.disabled = unavailable;
  });
  element("clearReview").disabled = unavailable || event.status === "unreviewed";
}

function renderReviewFeedback() {
  const saving = reviewSaving();
  const failed = state.reviewSaveState === "error" && Boolean(state.reviewError);
  element("reviewSaveFeedback").hidden = !saving;
  setText("reviewError", failed ? state.reviewError : "");
  element("reviewError").hidden = !failed;
}

function renderCoreEvidence() {
  const { core, more } = state.workspace.selection;
  setText("evidencePc34", workspaceNumber(core.pc34Intensity));
  setText("evidenceMz", core.measuredMz === null ? "—" : core.measuredMz.toFixed(6));
  setText("evidencePpm", core.massErrorPpm === null ? "—" : `${core.massErrorPpm > 0 ? "+" : ""}${core.massErrorPpm.toFixed(2)} ppm`);
  setText("evidenceQuality", core.quality.label);
  const notes = element("qualityNotes");
  notes.replaceChildren(...core.quality.notes.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
  notes.hidden = core.quality.notes.length === 0;
  setText("evidenceScan", more.scanNumber);
  setText("evidenceMs782", workspaceNumber(more.ms782Intensity));
  setText("evidenceTic", workspaceNumber(more.tic));
  setText("evidenceProminence", workspaceNumber(more.prominence));
  setText("evidenceWidth", more.physicalWidthSec === null ? "—" : `${more.physicalWidthSec.toFixed(3)} s`);
  setText("evidenceRange", formatAdjustmentRange(more.adjustmentRange));
  element("evidenceToggle").setAttribute("aria-expanded", String(state.moreEvidenceExpanded));
  element("moreEvidence").hidden = !state.moreEvidenceExpanded;
}

function renderWorkspaceSelection() {
  const event = selectedWorkspaceEvent();
  element("noSelectionCard").hidden = Boolean(event);
  element("selectedEventPanel").hidden = !event;
  if (!event) {
    renderReviewFeedback();
    return;
  }
  setText("selectedApexTime", event.apexTimeMin.toFixed(3));
  setText("selectedStatusBadge", event.statusLabel);
  element("selectedStatusBadge").dataset.eventStatus = event.status;
  setText("selectedOrigin", `${event.originLabel}${event.apexModified ? " · 峰顶已调整" : ""}`);
  renderReviewDecision(event);
  renderCoreEvidence();
  element("restoreAutomaticSection").hidden = !event.canRestoreAutomaticApex;
  element("restoreAutomatic").disabled = workbenchBusy()
    || !event.actionToken
    || !event.canRestoreAutomaticApex;
  element("reviewNote").disabled = pendingWorkbenchOperation();
  renderReviewFeedback();
}

function renderFilterOptions() {
  const select = element("eventFilter");
  const labels = new Map(state.workspace.filters.map((row) => [row.value, row]));
  Array.from(select.options).forEach((option) => {
    const filter = labels.get(option.value);
    if (!filter) {
      option.hidden = true;
      return;
    }
    option.hidden = false;
    option.textContent = `${filter.label} · ${filter.count}`;
  });
  select.value = state.workspaceFilter;
}

function createEventListButton(event) {
  const selected = event.eventToken === selectedWorkspaceEvent()?.eventToken;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "event-row";
  button.setAttribute("role", "option");
  button.setAttribute("aria-selected", String(selected));
  if (selected) button.setAttribute("aria-current", "true");
  button.dataset.qa = "event-row";
  button.dataset.eventStatus = event.status;
  button.dataset.eventKey = event.eventToken;
  button.disabled = workbenchBusy();
  const marker = document.createElement("span");
  marker.className = "event-row__marker";
  marker.setAttribute("aria-hidden", "true");
  const copy = document.createElement("span");
  copy.className = "event-row__copy";
  const time = document.createElement("strong");
  time.textContent = `${event.apexTimeMin.toFixed(3)} min`;
  const status = document.createElement("small");
  status.textContent = `${event.statusLabel} · ${event.originLabel}`;
  copy.append(time, status);
  button.append(marker, copy);
  button.addEventListener("click", () => selectWorkspaceEvent(event.eventToken));
  return button;
}

function renderEventList() {
  const events = state.workspace.window.eventOverlay;
  element("eventList").replaceChildren(...events.map(createEventListButton));
  setText("visibleEventCount", `${events.length} 个`);
}

function renderPlotGrid(geometry) {
  const horizontal = [0.2, 0.4, 0.6, 0.8].map((fraction) => {
    const y = geometry.content.top + fraction * (geometry.content.bottom - geometry.content.top);
    return `M ${geometry.content.left} ${y} H ${geometry.content.right}`;
  });
  const vertical = [0.2, 0.4, 0.6, 0.8].map((fraction) => {
    const x = geometry.content.left + fraction * (geometry.content.right - geometry.content.left);
    return `M ${x} ${geometry.content.top} V ${geometry.content.bottom}`;
  });
  element("plotGrid").replaceChildren(svgElement("path", { d: [...horizontal, ...vertical].join(" ") }));
}

function createMarkerShape(marker) {
  if (marker.event.marker.shape === "circle") {
    return svgElement("circle", {
      class: "event-marker-shape",
      cx: marker.x,
      cy: marker.y,
      r: marker.radius,
      "data-shape": "circle",
    });
  }
  return svgElement("path", {
    class: "event-marker-shape",
    d: marker.path,
    "data-shape": marker.event.marker.shape,
  });
}

function renderPlotMarkers(geometry) {
  const selectedToken = selectedWorkspaceEvent()?.eventToken || "";
  const eventLayer = element("eventLayer");
  const disabled = workbenchBusy();
  eventLayer.replaceChildren();
  geometry.markers.forEach((marker) => {
    const group = svgElement("g", {
      class: "event-marker",
      role: "button",
      tabindex: disabled ? "-1" : "0",
      "aria-label": `${marker.event.apexTimeMin.toFixed(3)} 分钟，${marker.event.statusLabel}`,
      "aria-disabled": String(disabled),
      "data-selected": marker.event.eventToken === selectedToken,
      "data-event-status": marker.event.status,
      "data-event-key": marker.event.eventToken,
      "data-qa": "plot-marker",
    });
    const hit = svgElement("circle", {
      class: "event-marker-hit",
      cx: marker.x,
      cy: marker.y,
      r: 16,
    });
    const shape = createMarkerShape(marker);
    group.append(hit, shape);
    const choose = () => {
      if (!disabled) selectWorkspaceEvent(marker.event.eventToken);
    };
    group.addEventListener("click", choose);
    group.addEventListener("mouseenter", () => {
      state.hoveredEventToken = marker.event.eventToken;
      renderPlotLabels(geometry);
    });
    group.addEventListener("mouseleave", () => {
      if (state.hoveredEventToken === marker.event.eventToken) {
        state.hoveredEventToken = "";
        renderPlotLabels(geometry);
      }
    });
    group.addEventListener("focus", () => {
      state.hoveredEventToken = marker.event.eventToken;
      renderPlotLabels(geometry);
    });
    group.addEventListener("blur", () => {
      if (state.hoveredEventToken === marker.event.eventToken) {
        state.hoveredEventToken = "";
        renderPlotLabels(geometry);
      }
    });
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        choose();
      }
    });
    eventLayer.append(group);
  });
}

function renderPlotLabels(geometry) {
  const labelLayer = element("labelLayer");
  labelLayer.replaceChildren();
  if (!state.workspaceLabels) return;
  const placements = placePlotLabels(
    geometry,
    [
      ...state.workspace.window.labelEventTokens,
      selectedWorkspaceEvent()?.eventToken || "",
      state.hoveredEventToken,
    ],
    state.hoveredEventToken || selectedWorkspaceEvent()?.eventToken || "",
    PLOT_LABEL_LIMIT,
  );
  placements.forEach((placement) => {
    const group = svgElement("g", {
      class: "plot-callout",
      "data-qa": "plot-label",
      "data-event-key": placement.event.eventToken,
      "data-label-left": placement.box.left,
      "data-label-top": placement.box.top,
      "data-label-right": placement.box.right,
      "data-label-bottom": placement.box.bottom,
    });
    group.append(
      svgElement("line", {
        x1: placement.anchorX,
        y1: placement.anchorY,
        x2: placement.marker.x,
        y2: placement.marker.y - placement.marker.radius,
      }),
      svgElement("line", {
        x1: placement.anchorX,
        y1: placement.anchorY,
        x2: placement.marker.x,
        y2: placement.marker.y - placement.marker.radius,
      }),
      svgElement("rect", {
        x: placement.box.left,
        y: placement.box.top,
        width: placement.box.right - placement.box.left,
        height: placement.box.bottom - placement.box.top,
        rx: 5,
      }),
    );
    const text = svgElement("text", {
      x: placement.box.left + 8,
      y: placement.box.top + 16,
    });
    text.textContent = placement.text;
    group.append(text);
    labelLayer.append(group);
  });
}

function renderPlotLegend() {
  const legend = element("plotLegend");
  legend.replaceChildren(svgElement("rect", { x: 0, y: 0, width: 306, height: 30, rx: 6 }));
  const entries = [
    ["unreviewed", "triangle", "未审阅"],
    ["accepted", "circle", "已保留"],
    ["rejected", "cross", "已排除"],
    ["pending", "diamond", "待定"],
  ];
  entries.forEach(([status, shape, label], index) => {
    const x = 12 + index * 74;
    const group = svgElement("g", { "data-event-status": status });
    const marker = { event: { marker: { shape } }, x, y: 15, radius: 4.5 };
    marker.path = shape === "triangle"
      ? `M ${x} 10.5 L ${x + 4.5} 19.5 L ${x - 4.5} 19.5 Z`
      : shape === "diamond"
        ? `M ${x} 10.5 L ${x + 4.5} 15 L ${x} 19.5 L ${x - 4.5} 15 Z`
        : `M ${x - 3.2} 11.8 L ${x + 3.2} 18.2 M ${x + 3.2} 11.8 L ${x - 3.2} 18.2`;
    group.append(createMarkerShape(marker));
    const text = svgElement("text", { x: x + 9, y: 19 });
    text.textContent = label;
    group.append(text);
    legend.append(group);
  });
}

function renderSignalPlot() {
  const geometry = buildPlotGeometry(state.workspace, { logScale: state.workspaceScale === "log" });
  renderPlotGrid(geometry);
  const trace = geometry.tracePath
    ? svgElement("path", { class: "trace-line", d: geometry.tracePath })
    : null;
  element("traceLayer").replaceChildren(...(trace ? [trace] : []));
  renderPlotMarkers(geometry);
  renderPlotLabels(geometry);
  renderPlotLegend();
  element("plotEmpty").hidden = state.workspace.window.eventOverlay.length > 0;
  const viewport = state.workspace.window.viewport;
  setText("plotWindowSummary", `${viewport.start_min.toFixed(3)}–${viewport.end_min.toFixed(3)} min · ${state.workspaceScale === "log" ? "对数" : "线性"}`);
}

function formatEditPoint(point) {
  if (!point) return "—";
  return `${point.timeMin.toFixed(3)} min · ${workspaceNumber(point.intensity)}`;
}

function renderEventEditOverlay(geometry) {
  const edit = state.eventEdit;
  const overlay = element("editAllowedOverlay");
  const hit = element("editAllowedHit");
  const aimLine = element("editAimLine");
  const candidate = element("editCandidateMarker");
  const setHidden = (node, hidden) => node.toggleAttribute("hidden", hidden);
  const interval = edit.allowedInterval;
  if (!eventEditActive() || !interval) {
    setHidden(overlay, true);
    setHidden(hit, true);
    setHidden(aimLine, true);
    setHidden(candidate, true);
    return;
  }
  const viewport = state.workspace.window.viewport;
  const hitGeometry = eventEditHitGeometry(interval, viewport, geometry.data);
  if (hitGeometry) {
    overlay.setAttribute("x", hitGeometry.visibleLeft);
    overlay.setAttribute("width", Math.max(0, hitGeometry.visibleRight - hitGeometry.visibleLeft));
    hit.setAttribute("x", hitGeometry.hitLeft);
    hit.setAttribute("width", hitGeometry.hitRight - hitGeometry.hitLeft);
    setHidden(overlay, false);
    setHidden(hit, false);
  } else {
    setHidden(overlay, true);
    setHidden(hit, true);
  }
  const hasHover = edit.hoverTimeMin !== null && edit.hoverTimeMin !== "";
  const hover = hasHover ? Number(edit.hoverTimeMin) : Number.NaN;
  if (Number.isFinite(hover) && ["aiming", "error"].includes(edit.state)) {
    const x = geometry.xForTime(hover);
    aimLine.setAttribute("x1", x);
    aimLine.setAttribute("x2", x);
    setHidden(aimLine, false);
  } else {
    setHidden(aimLine, true);
  }
  if (edit.candidate) {
    candidate.setAttribute(
      "transform",
      `translate(${geometry.xForTime(edit.candidate.timeMin)} ${geometry.yForSignal(edit.candidate.intensity)})`,
    );
    setHidden(candidate, false);
  } else {
    setHidden(candidate, true);
  }
}

function renderEventEdit() {
  const edit = state.eventEdit;
  const active = eventEditActive();
  const modeBar = element("editModeBar");
  modeBar.hidden = !active;
  modeBar.dataset.state = active ? edit.state : "selected";
  if (!active) {
    renderEventEditOverlay(buildPlotGeometry(state.workspace, {
      logScale: state.workspaceScale === "log",
    }));
    return;
  }
  const modeName = edit.mode === "adjust" ? "重新定位峰顶" : "添加遗漏峰";
  const titleByState = {
    aiming: `${modeName}：在图中选择位置`,
    preview: `${modeName}：确认候选峰`,
    saving: `正在应用${modeName}`,
    error: `${modeName}需要处理`,
  };
  setText("editModeTitle", titleByState[edit.state] || modeName);
  setText(
    "editModeDescription",
    edit.state === "preview"
      ? "候选只是一项预览；选择“应用候选”后才会修改项目。"
      : edit.state === "saving"
        ? "正在重新校验候选并保存，请稍候。"
        : "可在高亮范围内单击，或用方向键微调位置并按 Enter 生成预览。",
  );
  const interval = edit.allowedInterval;
  setText(
    "editAllowedRange",
    interval ? `${interval.startMin.toFixed(6)}–${interval.endMin.toFixed(6)} min` : "正在准备…",
  );
  const position = element("editPosition");
  const hasKeyboardTime = edit.keyboardTimeMin !== null && edit.keyboardTimeMin !== "";
  const keyboardTime = hasKeyboardTime ? Number(edit.keyboardTimeMin) : Number.NaN;
  const keyboardReady = Boolean(interval) && Number.isFinite(keyboardTime);
  element("editPositionFact").hidden = !interval;
  if (interval) {
    position.min = String(interval.startMin);
    position.max = String(interval.endMin);
    const step = eventEditKeyboardStep(interval);
    position.step = step > 0 ? String(step) : "any";
    position.value = String(keyboardReady ? keyboardTime : interval.startMin);
  }
  position.disabled = !keyboardReady
    || !["aiming", "error"].includes(edit.state)
    || edit.busy
    || !edit.token;
  setText("editPositionValue", keyboardReady ? `${keyboardTime.toFixed(6)} min` : "—");
  const hasCandidate = Boolean(edit.candidate);
  element("editCandidateFact").hidden = !hasCandidate;
  element("editChangeFact").hidden = !hasCandidate;
  setText("editCandidate", hasCandidate
    ? `${formatEditPoint(edit.candidate)} · 偏移 ${edit.candidate.offsetSec >= 0 ? "+" : ""}${edit.candidate.offsetSec.toFixed(3)} s`
    : "");
  if (hasCandidate) {
    const before = edit.change?.before;
    setText("editChange", before
      ? `${before.timeMin.toFixed(3)} → ${edit.change.after.timeMin.toFixed(3)} min`
      : `新增于 ${edit.change?.after?.timeMin.toFixed(3)} min`);
  } else {
    setText("editChange", "");
  }
  element("applyEventEdit").disabled = edit.state !== "preview" || edit.busy || !edit.token;
  element("cancelEventEdit").disabled = edit.busy;
  const failed = edit.state === "error" && Boolean(edit.error);
  setText("editError", failed ? edit.error : "");
  element("editError").hidden = !failed;
  renderEventEditOverlay(buildPlotGeometry(state.workspace, {
    logScale: state.workspaceScale === "log",
  }));
}

function renderWorkspaceControls() {
  const viewport = state.workspace.window.viewport;
  const busy = workbenchBusy();
  element("windowStart").value = viewport.start_min.toFixed(3);
  element("windowWidth").value = Math.max(0, viewport.end_min - viewport.start_min).toFixed(3);
  element("scaleLinear").setAttribute("aria-pressed", String(state.workspaceScale === "linear"));
  element("scaleLog").setAttribute("aria-pressed", String(state.workspaceScale === "log"));
  element("toggleLabels").setAttribute("aria-pressed", String(state.workspaceLabels));
  element("undoAction").disabled = busy || !state.workspace.history.canUndo;
  element("redoAction").disabled = busy || !state.workspace.history.canRedo;
  ["windowStart", "windowWidth", "previousWindow", "nextWindow", "eventFilter"].forEach((id) => {
    element(id).disabled = busy;
  });
  ["scaleLinear", "scaleLog", "toggleLabels", "evidenceToggle"].forEach((id) => {
    element(id).disabled = busy;
  });
  element("headerNew").disabled = busy;
  element("changeRange").disabled = busy;
  element("openExport").disabled = busy;
  const editMode = state.eventEdit.mode;
  element("addEvent").disabled = busy;
  element("addEvent").setAttribute("aria-pressed", String(editMode === "add"));
  element("adjustApex").disabled = busy || !selectedWorkspaceEvent()?.actionToken;
  element("adjustApex").setAttribute("aria-pressed", String(editMode === "adjust"));
}

function renderWorkspace() {
  if (!state.workspace) return;
  const busy = workbenchBusy();
  element("projectView").setAttribute("aria-busy", String(pendingWorkbenchOperation()));
  element("projectView").dataset.editActive = String(eventEditActive());
  renderWorkspaceHeader();
  renderWorkspaceProgress();
  renderWorkspaceSelection();
  renderFilterOptions();
  renderWorkspaceControls();
  renderEventList();
  renderSignalPlot();
  renderEventEdit();
  element("workspaceStatus").parentElement.dataset.saveState = state.reviewSaveState;
  if (reviewSaving()) setText("workspaceStatus", "正在保存审阅…");
  else if (state.reviewSaveState === "error") setText("workspaceStatus", "审阅未保存；原状态已恢复");
  else setText("workspaceStatus", state.workspaceBusy ? "正在更新窗口…" : "已准备好");
}

async function loadActiveWorkspace() {
  state.workspaceBusy = true;
  try {
    const workspace = normalizeWorkspace(await apiRequest(API_ENDPOINTS.workspace));
    state.workspace = workspace;
    state.workspaceFilter = "all";
    state.workspaceScale = "linear";
    state.workspaceLabels = true;
    state.moreEvidenceExpanded = false;
    state.reviewSaveState = "idle";
    state.reviewError = "";
    state.eventEdit = emptyEventEditState();
    state.rangeFlow = emptyRangeFlow();
    state.exportFlow = emptyExportFlow();
    state.activeProject = {
      displayName: workspace.project.displayName,
      analysisRange: { ...workspace.project.analysisRange },
      eventCount: workspace.project.eventCount,
    };
    state.bootstrap.activeProject = state.activeProject;
    state.bootstrap.view = "project";
    setView("project");
    return true;
  } catch (error) {
    state.workspace = null;
    throw error;
  } finally {
    state.workspaceBusy = false;
    if (state.workspace) renderWorkspace();
  }
}

async function requestWorkspaceWindow(options = {}) {
  if (!state.workspace || workbenchBusy() || state.fixture) return;
  clearReviewFeedback();
  state.workspaceBusy = true;
  renderWorkspace();
  try {
    const body = workspaceRequestBody(state.workspace, {
      statusFilter: state.workspaceFilter,
      maximumLabels: state.workspaceLabels ? PLOT_LABEL_LIMIT : 0,
      ...options,
    });
    if (body.end_min < body.start_min) [body.start_min, body.end_min] = [body.end_min, body.start_min];
    state.workspace = normalizeWorkspace(await post(API_ENDPOINTS.workspaceWindow, body));
  } catch (error) {
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
  } finally {
    state.workspaceBusy = false;
    renderWorkspace();
  }
}

function selectWorkspaceEvent(eventToken) {
  if (!eventToken || workbenchBusy()) return;
  clearReviewFeedback();
  state.hoveredEventToken = "";
  if (state.fixture) {
    const event = state.workspace.events.find((row) => row.eventToken === eventToken);
    if (!event) return;
    state.workspace.selection.event = event;
    state.workspace.selection.index = state.workspace.events.findIndex((row) => row.eventToken === eventToken);
    const index = state.workspace.selection.index;
    state.workspace.selection.previousEventToken = state.workspace.events[index - 1]?.eventToken || "";
    state.workspace.selection.nextEventToken = state.workspace.events[index + 1]?.eventToken || "";
    renderWorkspace();
    return;
  }
  requestWorkspaceWindow({ selectedEventToken: eventToken });
}

function navigateWorkspaceEvent(direction) {
  const token = direction < 0
    ? state.workspace?.selection.previousEventToken
    : state.workspace?.selection.nextEventToken;
  if (token) selectWorkspaceEvent(token);
}

function shiftWorkspaceWindow(direction) {
  const viewport = state.workspace?.window.viewport;
  if (!viewport) return;
  const width = viewport.end_min - viewport.start_min;
  let start = viewport.start_min + direction * width;
  let end = viewport.end_min + direction * width;
  if (start < viewport.analysis_start_min) {
    start = viewport.analysis_start_min;
    end = start + width;
  }
  if (end > viewport.analysis_end_min) {
    end = viewport.analysis_end_min;
    start = end - width;
  }
  requestWorkspaceWindow({ startMin: start, endMin: end });
}

function applyWorkspaceInputs() {
  if (!state.workspace) return;
  const start = Number(element("windowStart").value);
  const width = Number(element("windowWidth").value);
  if (!Number.isFinite(start) || !Number.isFinite(width) || width <= 0) {
    showToast("请填写有效的窗口起点和宽度。", "error");
    renderWorkspaceControls();
    return;
  }
  requestWorkspaceWindow({ startMin: start, endMin: start + width });
}

function reviewFailureMessage(error) {
  if (error instanceof ApiError && error.status === 409) {
    return "审阅未保存，原状态已恢复。项目可能已在其他窗口更新，请重新打开项目后再试。";
  }
  const detail = error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE;
  return `审阅未保存，原状态已恢复。${detail}`;
}

async function performReviewMutation(path, buildBody) {
  if (!state.workspace || workbenchBusy() || state.fixture) return;
  const previousWorkspace = state.workspace;
  let body;
  try {
    body = buildBody();
  } catch (_error) {
    state.reviewSaveState = "error";
    state.reviewError = "当前事件暂时不能执行此操作。请选择事件后重试。";
    renderWorkspace();
    showToast(state.reviewError, "error");
    return;
  }

  state.reviewSaveState = "saving";
  state.reviewError = "";
  renderWorkspace();
  try {
    const payload = await post(path, body);
    if (payload.ok !== true || !payload.workspace || typeof payload.workspace !== "object") {
      throw new ApiError("本地服务没有返回更新后的工作区。", { code: "invalid_response" });
    }
    state.workspace = normalizeWorkspace(payload.workspace);
    state.hoveredEventToken = "";
    state.reviewSaveState = "idle";
    state.reviewError = "";
    element("reviewNote").value = "";
    renderWorkspace();
    showToast(visibleMessage(payload.message, "操作已保存。"), "success");
  } catch (error) {
    // No optimistic mutation is applied. Restoring this exact reference keeps
    // selection, row order, evidence and status identical after any failure.
    state.workspace = previousWorkspace;
    state.reviewSaveState = "error";
    state.reviewError = reviewFailureMessage(error);
    renderWorkspace();
    showToast(state.reviewError, "error");
  }
}

function submitReviewDecision(status) {
  const event = selectedWorkspaceEvent();
  if (!event) return;
  performReviewMutation(
    API_ENDPOINTS.reviewDecision,
    () => reviewDecisionBody(event, status, element("reviewNote").value),
  );
}

function restoreAutomaticApex() {
  const event = selectedWorkspaceEvent();
  if (!event?.canRestoreAutomaticApex) return;
  performReviewMutation(
    API_ENDPOINTS.restoreAutomaticApex,
    () => restoreAutomaticApexBody(event, element("reviewNote").value),
  );
}

function mutateReviewHistory(direction) {
  const path = direction === "redo" ? API_ENDPOINTS.reviewRedo : API_ENDPOINTS.reviewUndo;
  performReviewMutation(path, () => reviewHistoryBody(element("reviewNote").value));
}

function resetEventEdit() {
  state.eventEdit = emptyEventEditState();
}

function focusEnabledControl(id) {
  const target = element(id);
  if (!target || target.matches(":disabled, [aria-disabled='true']")) return;
  target.focus({ preventScroll: true });
}

function focusEventEditOrigin(mode) {
  focusEnabledControl(mode === "adjust" ? "adjustApex" : "addEvent");
}

function eventEditFailureMessage(error, fallback = "无法生成候选预览。请在允许区间内换一个位置重试。") {
  if (error instanceof ApiError && error.status === 409) {
    return "事件已经发生变化，未应用修改。请取消后重新选择事件。";
  }
  return error instanceof ApiError ? error.message : fallback;
}

async function beginEventEdit(mode) {
  if (!state.workspace || state.fixture || pendingWorkbenchOperation() || eventEditActive()) return;
  const event = selectedWorkspaceEvent();
  if (mode === "adjust" && !event) return;
  state.eventEdit = {
    ...emptyEventEditState(),
    state: "aiming",
    mode,
    busy: true,
  };
  renderWorkspace();
  try {
    const aim = normalizeEventEditAim(await post(
      API_ENDPOINTS.eventEditAim,
      eventEditAimBody(mode, event),
    ));
    state.eventEdit = {
      ...emptyEventEditState(),
      state: "aiming",
      mode: aim.mode,
      token: aim.token,
      before: aim.before,
      allowedInterval: aim.allowedInterval,
      keyboardTimeMin: eventEditDefaultTime(aim.allowedInterval, aim.before),
    };
  } catch (error) {
    state.eventEdit = {
      ...emptyEventEditState(),
      state: "error",
      mode,
      error: eventEditFailureMessage(error, "无法开始事件编辑，请重新选择事件后再试。"),
    };
  }
  renderWorkspace();
  focusEnabledControl(state.eventEdit.token ? "editPosition" : "cancelEventEdit");
}

function pointInBox(point, box) {
  return point.x >= box.left && point.x <= box.right
    && point.y >= box.top && point.y <= box.bottom;
}

function eventEditPointExcluded(event, point) {
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("#plotLegend, #eventLayer")) return true;
  if (pointInBox(point, PLOT_LAYOUT.legend)) return true;
  return Array.from(document.querySelectorAll("[data-qa='plot-label']")).some((node) => {
    try {
      const box = node.getBBox();
      return point.x >= box.x && point.x <= box.x + box.width
        && point.y >= box.y && point.y <= box.y + box.height;
    } catch (_error) {
      return false;
    }
  });
}

function plotEventPoint(event) {
  return plotTimeFromClientPoint(
    element("signalPlot"),
    event.clientX,
    event.clientY,
    state.workspace?.window.viewport,
  );
}

function eventEditTimeFromPoint(point) {
  if (!point || !state.eventEdit.allowedInterval || !state.workspace) return null;
  const geometry = buildPlotGeometry(state.workspace, { logScale: state.workspaceScale === "log" });
  const hit = eventEditHitGeometry(
    state.eventEdit.allowedInterval,
    state.workspace.window.viewport,
    geometry.data,
  );
  if (!hit) return null;
  if (!hit.expanded) {
    return point.timeMin >= hit.startMin && point.timeMin <= hit.endMin
      ? point.timeMin
      : null;
  }
  return eventEditTimeFromHitX(point.x, hit);
}

function updateEventEditAim(event) {
  if (!["aiming", "error"].includes(state.eventEdit.state) || state.eventEdit.busy) return;
  const point = plotEventPoint(event);
  state.eventEdit.hoverTimeMin = point && !eventEditPointExcluded(event, point)
    ? eventEditTimeFromPoint(point)
    : null;
  const geometry = buildPlotGeometry(state.workspace, { logScale: state.workspaceScale === "log" });
  renderEventEditOverlay(geometry);
}

async function previewEventEditAtTime(clickTimeMin) {
  const edit = state.eventEdit;
  if (!["aiming", "error"].includes(edit.state) || edit.busy || !edit.token || state.fixture) return;
  const time = Number(clickTimeMin);
  if (!Number.isFinite(time)) return;
  state.eventEdit.busy = true;
  state.eventEdit.error = "";
  renderWorkspace();
  try {
    const preview = normalizeEventEditPreview(await post(
      API_ENDPOINTS.eventEditPreview,
      eventEditPreviewBody(edit.token, time),
    ));
    state.eventEdit = {
      ...emptyEventEditState(),
      state: "preview",
      mode: preview.mode,
      token: preview.token,
      before: preview.change.before,
      candidate: preview.candidate,
      change: preview.change,
      allowedInterval: preview.allowedInterval,
    };
  } catch (error) {
    state.eventEdit.state = "error";
    state.eventEdit.busy = false;
    state.eventEdit.error = eventEditFailureMessage(error);
    state.eventEdit.hoverTimeMin = time;
    state.eventEdit.keyboardTimeMin = time;
  }
  renderWorkspace();
  focusEnabledControl(state.eventEdit.state === "preview" ? "applyEventEdit" : "cancelEventEdit");
}

function previewEventEdit(event) {
  const point = plotEventPoint(event);
  if (!point || eventEditPointExcluded(event, point)) return;
  const time = eventEditTimeFromPoint(point);
  if (time === null) return;
  previewEventEditAtTime(time);
}

function updateKeyboardEventEditPosition() {
  if (!["aiming", "error"].includes(state.eventEdit.state) || state.eventEdit.busy) return;
  const position = element("editPosition");
  const value = Number(position.value);
  const interval = state.eventEdit.allowedInterval;
  if (!Number.isFinite(value) || !interval) return;
  state.eventEdit.keyboardTimeMin = Math.max(interval.startMin, Math.min(interval.endMin, value));
  state.eventEdit.hoverTimeMin = state.eventEdit.keyboardTimeMin;
  setText("editPositionValue", `${state.eventEdit.keyboardTimeMin.toFixed(6)} min`);
  renderEventEditOverlay(buildPlotGeometry(state.workspace, {
    logScale: state.workspaceScale === "log",
  }));
}

function handleEventEditPositionKeydown(event) {
  if (event.key !== "Enter" || element("editPosition").disabled) return;
  event.preventDefault();
  previewEventEditAtTime(state.eventEdit.keyboardTimeMin);
}

async function applyEventEdit() {
  const edit = state.eventEdit;
  if (edit.state !== "preview" || edit.busy || !edit.token || state.fixture) return;
  const previousWorkspace = state.workspace;
  state.eventEdit.state = "saving";
  state.eventEdit.busy = true;
  state.eventEdit.error = "";
  renderWorkspace();
  let payload;
  let updatedWorkspace;
  try {
    payload = await post(
      API_ENDPOINTS.eventEditApply,
      eventEditApplyBody(edit.token, element("reviewNote").value),
    );
    if (payload.ok !== true || !payload.workspace || typeof payload.workspace !== "object") {
      throw new ApiError("本地服务没有返回更新后的工作区。", { code: "invalid_response" });
    }
    updatedWorkspace = normalizeWorkspace(payload.workspace);
  } catch (error) {
    state.workspace = previousWorkspace;
    state.eventEdit = {
      ...edit,
      state: "error",
      busy: false,
      error: `${eventEditFailureMessage(error, "候选未应用。")} 原状态已恢复，请取消后重新开始。`,
    };
    renderWorkspace();
    showToast(state.eventEdit.error, "error");
    focusEnabledControl("cancelEventEdit");
    return;
  }

  // A successful response means the scientific change is already committed.
  // From this point on, rendering failures must never masquerade as a failed save.
  state.workspace = updatedWorkspace;
  resetEventEdit();
  state.reviewSaveState = "idle";
  state.reviewError = "";
  state.hoveredEventToken = "";
  element("reviewNote").value = "";
  const successMessage = payload.outcome === "navigate_existing"
    ? "该位置已有事件，已为你选中；没有重复添加。"
    : edit.mode === "add" ? "遗漏峰已添加。" : "峰顶位置已更新。";
  try {
    renderWorkspace();
    showToast(successMessage, "success");
    focusEventEditOrigin(edit.mode);
  } catch (_renderError) {
    const committedWorkspace = updatedWorkspace;
    try {
      await loadActiveWorkspace();
      showToast("更改已保存，界面已刷新。", "success");
    } catch (_refreshError) {
      state.workspace = committedWorkspace;
      resetEventEdit();
      showToast("更改已保存，但界面刷新失败。请重新打开项目。", "error", { persistent: true });
    }
    focusEventEditOrigin(edit.mode);
  }
}

async function cancelEventEdit() {
  if (!eventEditActive() || state.eventEdit.busy) return;
  const mode = state.eventEdit.mode;
  if (state.fixture) {
    resetEventEdit();
    renderWorkspace();
    focusEventEditOrigin(mode);
    return;
  }
  const token = state.eventEdit.token;
  if (!token) {
    resetEventEdit();
    renderWorkspace();
    focusEventEditOrigin(mode);
    return;
  }
  state.eventEdit.busy = true;
  renderWorkspace();
  try {
    await post(API_ENDPOINTS.eventEditCancel, eventEditCancelBody(token));
  } catch (error) {
    showToast(eventEditFailureMessage(error, "编辑已在本页面取消。"), "error");
  } finally {
    resetEventEdit();
    renderWorkspace();
    focusEventEditOrigin(mode);
  }
}

function handleEventEditKeydown(event) {
  if (event.key !== "Escape" || !eventEditActive() || state.eventEdit.busy) return;
  event.preventDefault();
  cancelEventEdit();
}

function editableShortcutTarget(target) {
  return target instanceof Element && Boolean(target.closest(
    "input, textarea, select, [contenteditable='true'], [contenteditable='']",
  ));
}

function handleReviewShortcut(event) {
  if (
    event.defaultPrevented
    || event.repeat
    || event.ctrlKey
    || event.metaKey
    || event.altKey
    || state.view !== "project"
    || !state.workspace
    || editableShortcutTarget(event.target)
  ) return;
  const controlByKey = {
    a: element("reviewSegmented").querySelector("[data-decision='accepted']"),
    r: element("reviewSegmented").querySelector("[data-decision='rejected']"),
    p: element("reviewSegmented").querySelector("[data-decision='pending']"),
    u: element("undoAction"),
  };
  const control = controlByKey[String(event.key || "").toLowerCase()];
  if (!control || control.matches(":disabled")) return;
  event.preventDefault();
  control.click();
}

function createRecentButton(row, openHandler, className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.addEventListener("click", () => openHandler(row.projectToken));
  return button;
}

function renderRecentProjects() {
  const rows = state.bootstrap.recentProjects;
  const welcomeContainer = element("welcomeRecentList");
  const openContainer = element("openRecentList");
  welcomeContainer.replaceChildren();
  openContainer.replaceChildren();

  rows.forEach((row) => {
    const welcomeButton = createRecentButton(row, openProject, "recent-project-button");
    const welcomeName = document.createElement("span");
    welcomeName.textContent = row.displayName;
    welcomeButton.append(welcomeName);
    welcomeContainer.append(welcomeButton);

    const openButton = createRecentButton(row, openProject, "open-project-item");
    const icon = document.createElement("span");
    icon.className = "open-project-item__icon";
    icon.setAttribute("aria-hidden", "true");
    const copy = document.createElement("span");
    copy.className = "open-project-item__copy";
    const name = document.createElement("strong");
    name.textContent = row.displayName;
    const detail = document.createElement("span");
    detail.textContent = "最近打开";
    copy.append(name, detail);
    const action = document.createElement("span");
    action.className = "open-project-item__action";
    action.textContent = "打开";
    openButton.append(icon, copy, action);
    openContainer.append(openButton);
  });

  element("recentProjects").hidden = rows.length === 0;
  element("noRecentProjects").hidden = rows.length !== 0;
}

function renderApp() {
  state.activeProject = state.bootstrap.activeProject || state.activeProject;
  renderRecentProjects();
  setView(state.activeProject && state.bootstrap.view === "project" ? "project" : "welcome");
}

function openDialog(dialogName) {
  const dialog = element(`${dialogName}Dialog`);
  if (!dialog || dialog.open) return;
  state.modal = dialogName;
  dialog.showModal();
  focusDialog(dialogName);
}

function focusDialog(dialogName) {
  const dialog = element(`${dialogName}Dialog`);
  if (!dialog?.open) return;
  let target = null;
  if (dialogName === "create") {
    if (["running", "creating"].includes(state.create.analysisState)) {
      target = element("cancelAnalysis");
    } else if (state.create.analysisState === "cancelling") {
      target = dialog.querySelector(".modal__surface");
    } else if (["cancelled", "error"].includes(state.create.analysisState)) {
      target = element("retryAnalysis");
    } else if (state.create.analysisState === "ready") {
      const validation = currentRangeValidation();
      if (!element("projectName").value.trim()) target = element("projectName");
      else if (!validation.ok) target = element("rangeStart");
      else if (!state.create.target) target = element("selectTarget");
      else target = element("createProject");
    } else {
      target = element("selectSource");
    }
  } else if (dialogName === "open") {
    target = dialog.querySelector(".open-project-item") || element("browseProject");
  } else if (dialogName === "range") {
    target = state.rangeFlow.state === "preview"
      ? element("applyRange")
      : state.rangeFlow.state === "calculating" && state.rangeFlow.jobCancellable
        ? element("cancelRange")
        : state.rangeFlow.state === "applying"
          ? dialog.querySelector(".modal__surface")
          : element("rangeChangeStart");
  } else if (dialogName === "export") {
    target = state.exportFlow.state === "exporting"
      ? dialog.querySelector(".modal__surface")
      : state.exportFlow.state === "success"
        ? element("cancelExport")
        : state.exportFlow.target
          ? element("submitExport")
          : element("chooseExportTarget");
  }
  if (!target || target.hidden || target.matches(":disabled")) {
    target = dialog.querySelector(".modal__surface");
  }
  target?.focus({ preventScroll: true });
  if (dialogName === "create" && state.create.analysisState === "ready") {
    // Keep the next-step save-location context fully visible in compact
    // windows even when the final action itself lives in the fixed footer.
    element("selectTarget").closest(".target-picker")?.scrollIntoView({
      block: "end",
      inline: "nearest",
    });
  }
}

function closeDialog(dialogName, { force = false } = {}) {
  const dialog = element(`${dialogName}Dialog`);
  if (!dialog || !dialog.open) return true;
  if (dialogName === "create" && busyCreateState() && !force) {
    showToast("请先取消当前任务，或等待任务完成。", "error");
    return false;
  }
  if (dialogName === "range" && rangeFlowBusy() && !force) {
    showToast(state.rangeFlow.state === "applying" ? "请等待范围更新完成。" : "请先取消范围计算。", "error");
    return false;
  }
  if (dialogName === "export" && state.exportFlow.state === "exporting" && !force) {
    showToast("请等待导出完成。", "error");
    return false;
  }
  dialog.close();
  if (state.modal === dialogName) state.modal = null;
  return true;
}

function resetCreate() {
  clearPoll();
  state.create = emptyCreateState();
  element("projectName").value = "";
  element("rangeStart").value = "";
  element("rangeEnd").value = "";
  setText("rangeError", "");
  element("rangeError").hidden = true;
}

function openCreate({ reset = true } = {}) {
  if (reset) resetCreate();
  renderCreate();
  closeDialog("open", { force: true });
  openDialog("create");
}

function openOpen() {
  setText("openStatus", "项目原始路径不会显示在页面中。");
  renderRecentProjects();
  openDialog("open");
}

function clearOperationPoll() {
  state.operationGeneration += 1;
  if (state.operationTimer !== null) {
    window.clearTimeout(state.operationTimer);
    state.operationTimer = null;
  }
}

function operationRangeText(range) {
  return range
    ? formatRange({ start_min: range.startMin, end_min: range.endMin })
    : "—";
}

function rangeFlowBusy() {
  return ["calculating", "applying"].includes(state.rangeFlow.state);
}

function renderRangeFlow() {
  if (!state.workspace) return;
  const flow = state.rangeFlow;
  const calculating = flow.state === "calculating";
  const applying = flow.state === "applying";
  const busy = calculating || applying;
  const previewVisible = Boolean(flow.oldRange && flow.newRange && flow.impacts)
    && ["preview", "applying", "error"].includes(flow.state);
  const errorVisible = flow.state === "error" && Boolean(flow.error);
  const dialog = element("rangeDialog");
  dialog.setAttribute("aria-busy", String(busy));
  element("rangeFields").disabled = busy || ["preview", "applying"].includes(flow.state);
  setText("rangeCurrent", formatRange(state.workspace.project.analysisRange));
  element("rangeProgressRegion").hidden = !busy;
  element("rangeProgress").value = flow.fraction;
  element("rangeProgress").textContent = `${Math.round(flow.fraction * 100)}%`;
  setText("rangeProgressPercent", `${Math.round(flow.fraction * 100)}%`);
  setText("rangeProgressText", applying ? "正在安全更新项目范围…" : "正在计算范围变化…");
  element("rangePreviewPanel").hidden = !previewVisible;
  setText("rangeOld", operationRangeText(flow.oldRange));
  setText("rangeNew", operationRangeText(flow.newRange));
  setText("impactReusable", flow.impacts?.reusable ?? 0);
  setText("impactMovedOut", flow.impacts?.movedOut ?? 0);
  setText("impactReconfirm", flow.impacts?.reconfirm ?? 0);
  setText("impactNewlyDetected", flow.impacts?.newlyDetected ?? 0);
  setText("impactRetainedManual", flow.impacts?.retainedManual ?? 0);
  setText("rangeChangeError", errorVisible ? flow.error : "");
  element("rangeChangeError").hidden = !errorVisible;
  element("submitRangePreview").hidden = ["preview", "applying"].includes(flow.state);
  element("submitRangePreview").disabled = busy;
  element("applyRange").hidden = !["preview", "applying"].includes(flow.state);
  element("applyRange").disabled = flow.state !== "preview" || !flow.previewToken;
  element("applyRange").textContent = applying ? "正在应用…" : "应用新范围";
  element("cancelRange").disabled = applying || Boolean(flow.cancelBusy)
    || (calculating && !flow.jobCancellable);
  element("closeRange").disabled = applying || Boolean(flow.cancelBusy)
    || (calculating && !flow.jobCancellable);
  setText("rangeFooterHint", applying
    ? "正在完成范围更新，请保持应用打开。"
    : calculating
      ? "计算过程中可以安全取消。"
      : flow.state === "preview"
        ? "确认影响后再应用；取消不会改变项目。"
        : flow.state === "error"
          ? "项目保持原状态。请修正范围并重新计算。"
          : "填写范围后先计算影响预览。");
}

function exportKindCopy(kind) {
  return kind === "audit_package"
    ? {
        title: "导出完整审计数据包",
        overview: "复核与归档数据",
        help: "包含完整事件与操作记录，适合复核、归档或下游程序读取。",
        target: "选择文件夹…",
        submit: "导出完整数据包",
      }
    : {
        title: "导出审阅结果",
        overview: "审阅结果",
        help: "适合继续分析或交付已确认的审阅结果。",
        target: "选择文件…",
        submit: "导出审阅结果",
      };
}

function renderExportFlow() {
  if (!state.workspace) return;
  const flow = state.exportFlow;
  const exporting = flow.state === "exporting";
  const success = flow.state === "success" && Boolean(flow.result);
  const errorVisible = flow.state === "error" && Boolean(flow.error);
  const audit = flow.kind === "audit_package";
  const copy = exportKindCopy(flow.kind);
  element("exportDialog").setAttribute("aria-busy", String(exporting));
  setText("exportTitle", copy.title);
  setText("exportOverviewTitle", copy.overview);
  setText("exportKindHelp", copy.help);
  element("reviewExportKind").setAttribute("aria-checked", String(!audit));
  element("auditExportKind").setAttribute("aria-checked", String(audit));
  element("reviewExportKind").disabled = exporting;
  element("auditExportKind").disabled = exporting;
  element("includePendingField").hidden = audit;
  element("includePending").checked = !audit && flow.includePending;
  element("includePending").disabled = exporting;
  setText("exportCurrentRange", formatRange(state.workspace.project.analysisRange));
  setText("exportStatusFilter", audit
    ? "全部事件与审阅记录"
    : flow.includePending ? "已保留和待定" : "仅已保留");
  const rows = audit
    ? state.workspace.review.total
    : estimatedReviewRows(state.workspace.review, flow.includePending);
  setText("exportEstimatedRows", audit ? `${formatCount(rows)} 个事件` : `预计 ${formatCount(rows)} 行`);
  setText("exportTargetName", flow.target?.displayName || "尚未选择");
  element("chooseExportTarget").textContent = copy.target;
  element("chooseExportTarget").disabled = exporting || success;
  element("exportNote").disabled = exporting || success;
  element("exportProgressRegion").hidden = !exporting;
  element("exportProgress").value = flow.fraction;
  element("exportProgress").textContent = `${Math.round(flow.fraction * 100)}%`;
  setText("exportProgressPercent", `${Math.round(flow.fraction * 100)}%`);
  element("exportResultPanel").hidden = !success;
  if (flow.result) {
    setText("exportResultTitle", `${flow.result.displayName} 已导出`);
    setText("exportResultMessage", `${flow.result.message} 共 ${formatCount(flow.result.rowCount)} 行。`);
  }
  setText("exportError", errorVisible ? flow.error : "");
  element("exportError").hidden = !errorVisible;
  element("submitExport").disabled = exporting || success || !flow.target;
  element("submitExport").textContent = exporting ? "正在导出…" : copy.submit;
  element("cancelExport").disabled = exporting;
  element("closeExport").disabled = exporting;
  element("cancelExport").textContent = success ? "完成" : "取消";
  setText("exportFooterHint", exporting
    ? "正在写入所选目标，请保持应用打开。"
    : success
      ? "导出已完成，可以安全关闭此窗口。"
      : errorVisible
        ? "项目没有改变。请选择新的保存位置后重试。"
        : "选择保存目标后即可导出。");
}

function openRangeFlow() {
  if (!state.workspace || workbenchBusy()) return;
  clearOperationPoll();
  state.rangeFlow = { ...emptyRangeFlow(), state: "input" };
  const range = state.workspace.project.analysisRange;
  element("rangeChangeStart").value = formatMinute(range.start_min);
  element("rangeChangeEnd").value = formatMinute(range.end_min);
  element("rangeNote").value = "";
  renderRangeFlow();
  renderWorkspace();
  openDialog("range");
}

function setExportKind(kind) {
  if (state.exportFlow.state === "exporting") return;
  const normalized = kind === "audit_package" ? "audit_package" : "review_results";
  if (state.exportFlow.kind !== normalized) {
    state.exportFlow.target = null;
    state.exportFlow.error = "";
    state.exportFlow.result = null;
  }
  state.exportFlow.kind = normalized;
  state.exportFlow.includePending = normalized === "review_results"
    ? state.exportFlow.includePending
    : false;
  state.exportFlow.state = "input";
  renderExportFlow();
}

function openExportFlow(kind = "review_results") {
  if (!state.workspace || workbenchBusy()) return;
  clearOperationPoll();
  state.exportFlow = { ...emptyExportFlow(), state: "input", kind };
  element("exportNote").value = "";
  renderExportFlow();
  renderWorkspace();
  openDialog("export");
}

function scheduleOperationPoll(kind) {
  const generation = state.operationGeneration;
  const flow = kind === "range" ? state.rangeFlow : state.exportFlow;
  if (!flow.jobId || state.fixture) return;
  if (state.operationTimer !== null) window.clearTimeout(state.operationTimer);
  state.operationTimer = window.setTimeout(
    () => pollOperationJob(kind, generation),
    POLL_INTERVAL_MS,
  );
}

function operationFailure(job, fallback) {
  return visibleMessage(job.error?.message, fallback);
}

async function applyRangeOperationJob(response) {
  const job = normalizeOperationJob(response);
  state.rangeFlow.jobId = job.jobId || state.rangeFlow.jobId;
  state.rangeFlow.jobCancellable = job.cancellable;
  state.rangeFlow.fraction = job.fraction;
  if (state.rangeFlow.cancelRequested) {
    if (job.active) {
      renderRangeFlow();
      if (!state.rangeFlow.cancelIssued && job.cancellable) {
        await performRangeCancellation({ pending: true });
      } else {
        scheduleOperationPoll("range");
      }
      return;
    }
    if (job.succeeded && job.result?.range_preview) {
      const preview = normalizeRangePreview(job.result.range_preview);
      state.rangeFlow.previewToken = preview.previewToken;
      state.rangeFlow.state = "preview";
      state.rangeFlow.cancelIssued = false;
      await performRangeCancellation({ pending: true });
      return;
    }
    finishRangeCancellation();
    return;
  }
  if (job.active) {
    renderRangeFlow();
    scheduleOperationPoll("range");
    return;
  }
  state.rangeFlow.jobId = "";
  state.rangeFlow.jobCancellable = false;
  if (job.succeeded && state.rangeFlow.state === "calculating") {
    const preview = normalizeRangePreview(job.result?.range_preview);
    state.rangeFlow = {
      ...state.rangeFlow,
      state: "preview",
      previewToken: preview.previewToken,
      oldRange: preview.oldRange,
      newRange: preview.newRange,
      impacts: preview.impacts,
      error: "",
      fraction: 1,
    };
    renderRangeFlow();
    focusEnabledControl("applyRange");
    return;
  }
  if (job.succeeded && state.rangeFlow.state === "applying") {
    const updatedWorkspace = normalizeWorkspace(job.result?.workspace);
    state.workspace = updatedWorkspace;
    state.rangeFlow = emptyRangeFlow();
    closeDialog("range", { force: true });
    renderWorkspace();
    showToast("分析范围已更新。", "success");
    focusEnabledControl("changeRange");
    return;
  }
  if (job.cancelled) {
    state.rangeFlow = { ...emptyRangeFlow(), state: "input" };
    renderRangeFlow();
    return;
  }
  state.rangeFlow.state = "error";
  state.rangeFlow.previewToken = "";
  state.rangeFlow.error = operationFailure(job, "范围操作未完成，项目保持原状态。请重试。");
  renderRangeFlow();
  focusEnabledControl("submitRangePreview");
}

function applyExportOperationJob(response) {
  const job = normalizeOperationJob(response);
  state.exportFlow.jobId = job.jobId || state.exportFlow.jobId;
  state.exportFlow.jobCancellable = job.cancellable;
  state.exportFlow.fraction = job.fraction;
  if (job.active) {
    renderExportFlow();
    scheduleOperationPoll("export");
    return;
  }
  state.exportFlow.jobId = "";
  state.exportFlow.jobCancellable = false;
  if (job.succeeded) {
    state.exportFlow.state = "success";
    state.exportFlow.result = normalizeExportResult(job.result?.export);
    state.exportFlow.error = "";
    state.exportFlow.fraction = 1;
    renderExportFlow();
    focusEnabledControl("cancelExport");
    return;
  }
  state.exportFlow.state = "error";
  state.exportFlow.target = null;
  state.exportFlow.result = null;
  state.exportFlow.error = operationFailure(job, "导出未完成。请选择新的保存位置后重试。");
  renderExportFlow();
  focusEnabledControl("chooseExportTarget");
}

async function pollOperationJob(kind, generation) {
  state.operationTimer = null;
  const flow = kind === "range" ? state.rangeFlow : state.exportFlow;
  const jobId = flow.jobId;
  if (!jobId || generation !== state.operationGeneration || state.fixture) return;
  try {
    const response = await apiRequest(API_ENDPOINTS.job(jobId));
    if (generation !== state.operationGeneration || jobId !== flow.jobId) return;
    if (kind === "range") await applyRangeOperationJob(response);
    else applyExportOperationJob(response);
  } catch (error) {
    if (generation !== state.operationGeneration) return;
    flow.jobId = "";
    flow.jobCancellable = false;
    flow.state = "error";
    flow.error = error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE;
    if (kind === "range") renderRangeFlow();
    else {
      flow.target = null;
      renderExportFlow();
    }
  }
}

async function submitRangePreview() {
  if (state.fixture || !["input", "error"].includes(state.rangeFlow.state)) return;
  const valid = validateRangeInput(
    element("rangeChangeStart").value,
    element("rangeChangeEnd").value,
  );
  if (!valid.ok) {
    state.rangeFlow.state = "error";
    state.rangeFlow.error = valid.message;
    renderRangeFlow();
    focusEnabledControl("rangeChangeStart");
    return;
  }
  clearOperationPoll();
  state.rangeFlow = { ...emptyRangeFlow(), state: "calculating" };
  renderRangeFlow();
  try {
    const response = await post(
      API_ENDPOINTS.rangePreview,
      rangePreviewBody(valid.startText, valid.endText),
    );
    await applyRangeOperationJob(response);
  } catch (error) {
    state.rangeFlow.state = "error";
    state.rangeFlow.error = error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE;
    renderRangeFlow();
  }
}

async function applyRangeChange() {
  const token = state.rangeFlow.previewToken;
  if (state.fixture || state.rangeFlow.state !== "preview" || !token) return;
  clearOperationPoll();
  state.rangeFlow.state = "applying";
  state.rangeFlow.jobCancellable = false;
  state.rangeFlow.fraction = 0;
  renderRangeFlow();
  try {
    const response = await post(
      API_ENDPOINTS.rangeApply,
      rangeApplyBody(token, element("rangeNote").value),
    );
    state.rangeFlow.previewToken = "";
    await applyRangeOperationJob(response);
  } catch (error) {
    state.rangeFlow.state = "error";
    state.rangeFlow.error = error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE;
    renderRangeFlow();
  }
}

async function cancelRangeFlow() {
  const flow = state.rangeFlow;
  if (flow.state === "closed" || flow.state === "applying" || flow.cancelBusy) return;
  if (state.fixture) {
    state.rangeFlow = emptyRangeFlow();
    closeDialog("range", { force: true });
    renderWorkspace();
    return;
  }
  await performRangeCancellation();
}

function finishRangeCancellation() {
  clearOperationPoll();
  state.rangeFlow = emptyRangeFlow();
  closeDialog("range", { force: true });
  renderWorkspace();
  focusEnabledControl("changeRange");
}

async function performRangeCancellation({ pending = false } = {}) {
  const flow = state.rangeFlow;
  const action = rangeCancelAction(flow);
  if (action === "blocked") return;
  if (action === "close") {
    finishRangeCancellation();
    return;
  }
  if (action === "wait_for_job") {
    flow.cancelRequested = true;
    flow.cancelBusy = true;
    renderRangeFlow();
    return;
  }
  if (action === "wait_terminal") {
    flow.cancelBusy = true;
    renderRangeFlow();
    scheduleOperationPoll("range");
    return;
  }
  if (!pending && flow.cancelBusy) return;
  flow.cancelRequested = true;
  flow.cancelBusy = true;
  renderRangeFlow();
  try {
    if (action === "cancel_job") {
      const response = await post(API_ENDPOINTS.cancelJob(flow.jobId), {});
      const job = normalizeOperationJob(response);
      flow.cancelIssued = true;
      flow.jobId = job.jobId || flow.jobId;
      flow.jobCancellable = job.cancellable;
      flow.fraction = job.fraction;
      if (job.active) {
        renderRangeFlow();
        scheduleOperationPoll("range");
        return;
      }
      if (job.succeeded && job.result?.range_preview) {
        const preview = normalizeRangePreview(job.result.range_preview);
        flow.state = "preview";
        flow.previewToken = preview.previewToken;
        flow.cancelIssued = false;
        await performRangeCancellation({ pending: true });
        return;
      }
    } else if (action === "cancel_preview") {
      await post(API_ENDPOINTS.rangeCancel, rangeCancelBody(flow.previewToken));
    }
    finishRangeCancellation();
  } catch (error) {
    flow.cancelRequested = false;
    flow.cancelIssued = false;
    flow.cancelBusy = false;
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
    renderRangeFlow();
    if (flow.state === "calculating" && flow.jobId) scheduleOperationPoll("range");
  }
}

async function chooseExportTarget() {
  if (state.fixture || !["input", "error"].includes(state.exportFlow.state)) return;
  const role = state.exportFlow.kind === "audit_package"
    ? PATH_ROLES.auditExport
    : PATH_ROLES.reviewExport;
  element("chooseExportTarget").disabled = true;
  try {
    const selection = await selectPath(role);
    if (!selection) return;
    state.exportFlow.target = selection;
    state.exportFlow.state = "input";
    state.exportFlow.error = "";
  } catch (error) {
    state.exportFlow.state = "error";
    state.exportFlow.error = error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE;
  } finally {
    renderExportFlow();
  }
}

async function submitExport() {
  const flow = state.exportFlow;
  if (state.fixture || !["input", "error"].includes(flow.state) || !flow.target) return;
  clearOperationPoll();
  flow.state = "exporting";
  flow.error = "";
  flow.result = null;
  flow.fraction = 0;
  renderExportFlow();
  try {
    const audit = flow.kind === "audit_package";
    const response = await post(
      audit ? API_ENDPOINTS.exportAuditPackage : API_ENDPOINTS.exportReviewResults,
      audit
        ? auditExportBody(flow.target.selectionToken, element("exportNote").value)
        : reviewExportBody(
            flow.target.selectionToken,
            flow.includePending,
            element("exportNote").value,
          ),
    );
    applyExportOperationJob(response);
  } catch (error) {
    flow.state = "error";
    flow.error = error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE;
    renderExportFlow();
  }
}

function closeExportFlow() {
  if (state.exportFlow.state === "exporting") {
    showToast("请等待导出完成。", "error");
    return;
  }
  clearOperationPoll();
  state.exportFlow = emptyExportFlow();
  closeDialog("export", { force: true });
  renderWorkspace();
  focusEnabledControl("openExport");
}

function stateCopy() {
  const kind = state.create.jobKind;
  const map = {
    idle: {
      title: "尚未分析源文件",
      message: "选择文件后会自动检查时间范围和扫描数量。",
      badge: "等待选择",
    },
    running: {
      title: "正在分析源文件",
      message: "源文件保持只读，可以随时安全取消。",
      badge: "分析中",
    },
    cancelling: {
      title: kind === "creation" ? "正在取消创建" : "正在安全取消",
      message: "正在结束后台工作，请稍候。",
      badge: "取消中",
    },
    cancelled: {
      title: "已取消分析",
      message: "没有创建项目，也没有更改源文件。",
      badge: "已取消",
    },
    error: {
      title: "无法分析这个文件",
      message: "请确认文件完整且格式正确，然后重新分析。",
      badge: "需要处理",
    },
    ready: {
      title: "源文件可用于新建项目",
      message: "请确认分析范围、项目名称和保存位置。",
      badge: "可创建",
    },
    creating: {
      title: "正在创建项目",
      message: "正在整理事件和项目文件，请保持应用打开。",
      badge: "创建中",
    },
  };
  return map[state.create.analysisState] || map.error;
}

function setStep(name, { current = false, complete = false } = {}) {
  const item = element("createSteps").querySelector(`[data-step="${name}"]`);
  if (!item) return;
  if (current) item.setAttribute("aria-current", "step");
  else item.removeAttribute("aria-current");
  item.dataset.complete = complete ? "true" : "false";
}

function renderSteps() {
  const ready = Boolean(state.create.inspection);
  const targetReady = Boolean(state.create.target);
  const busyCreating = state.create.analysisState === "creating";
  setStep("source", { current: !ready, complete: ready });
  setStep("range", { current: ready && !targetReady && !busyCreating, complete: ready && targetReady });
  setStep("target", { current: ready && (targetReady || busyCreating), complete: false });
}

function currentRangeValidation() {
  return validateAnalysisRange(
    element("rangeStart").value,
    element("rangeEnd").value,
    state.create.inspection?.availableRange,
  );
}

function canCreate() {
  const projectName = element("projectName").value.trim();
  return state.create.analysisState === "ready"
    && Boolean(state.create.inspection?.inspectionToken)
    && Boolean(state.create.target?.selectionToken)
    && Boolean(projectName)
    && currentRangeValidation().ok;
}

function renderCreateFooter() {
  const analysisState = state.create.analysisState;
  const hints = {
    idle: "选择源文件即可开始。",
    running: "分析时可以安全取消。",
    cancelling: "正在结束后台工作…",
    cancelled: "可以重新分析，或选择其他文件。",
    error: "源文件没有发生变化。",
    ready: state.create.target ? "信息完整，可以创建项目。" : "请选择项目保存位置。",
    creating: "项目准备完成前请保持应用打开。",
  };
  setText("createFooterHint", hints[analysisState] || hints.error);
  element("createProject").disabled = !canCreate();
  element("createProject").textContent = analysisState === "creating" ? "正在创建…" : "创建项目";
  const closeDisabled = busyCreateState();
  element("closeCreate").disabled = closeDisabled;
  element("cancelCreate").disabled = closeDisabled;
}

function renderCreate() {
  const create = state.create;
  const copy = stateCopy();
  const card = element("analysisCard");
  card.dataset.state = create.analysisState;
  card.setAttribute(
    "aria-busy",
    ["running", "cancelling", "creating"].includes(create.analysisState) ? "true" : "false",
  );
  setText("sourceDisplayName", create.source?.displayName || "尚未选择文件");
  setText("analysisTitle", copy.title);
  setText("analysisMessage", copy.message);
  setText("analysisBadge", copy.badge);

  const progressVisible = ["running", "cancelling", "creating"].includes(create.analysisState);
  element("progressRegion").hidden = !progressVisible;
  const fraction = clampFraction(create.progress?.fraction);
  const percent = Math.round(fraction * 100);
  element("analysisProgress").value = fraction;
  element("analysisProgress").textContent = `${percent}%`;
  setText("analysisPercent", `${percent}%`);
  setText("analysisPhase", create.phase || (create.analysisState === "creating" ? "正在创建项目" : "正在检查源文件"));
  const totalText = create.progress?.totalBytes > 0 ? ` / ${formatBytes(create.progress.totalBytes)}` : "";
  setText("analysisBytes", `已读取 ${formatBytes(create.progress?.bytesRead || 0)}${totalText}`);
  setText("analysisScans", `${formatCount(create.progress?.parsedSpectra || 0, "0")} 个扫描`);

  const ready = create.analysisState === "ready" && Boolean(create.inspection);
  element("inspectionSummary").hidden = !ready;
  if (create.inspection) {
    setText("availableRange", formatRange(create.inspection.availableRange));
    setText("availableScans", formatCount(create.inspection.scanCount));
    setText("sourceSize", formatBytes(create.inspection.sizeBytes));
  }

  const showCancel = ["running", "cancelling", "creating"].includes(create.analysisState);
  const showRetry = ["cancelled", "error"].includes(create.analysisState);
  element("analysisActions").hidden = !(showCancel || showRetry);
  element("cancelAnalysis").hidden = !showCancel;
  element("cancelAnalysis").disabled = create.analysisState === "cancelling";
  element("cancelAnalysis").textContent = create.analysisState === "cancelling" ? "正在取消…" : create.analysisState === "creating" ? "取消创建" : "取消分析";
  element("retryAnalysis").hidden = !showRetry;

  const fieldsEnabled = create.analysisState === "ready" && Boolean(create.inspection);
  element("projectFields").disabled = !fieldsEnabled;
  setText("targetDisplayName", create.target?.displayName || "尚未选择位置");
  element("selectSource").disabled = busyCreateState();
  element("selectSource").textContent = create.source ? "重新选择…" : "选择文件…";
  renderSteps();
  renderCreateFooter();
}

async function selectPath(role) {
  const result = await post(API_ENDPOINTS.selectPath, { role });
  if (result.cancelled) return null;
  if (result.role !== role || typeof result.selection_token !== "string" || !result.selection_token) {
    throw new ApiError("系统选择结果无法使用，请重新选择。", { code: "invalid_selection" });
  }
  return {
    selectionToken: result.selection_token,
    displayName: safeDisplayName(result.display_name, role === PATH_ROLES.source ? "MS 原始文件" : "项目文件夹"),
  };
}

async function chooseSource() {
  if (state.fixture || busyCreateState()) return;
  element("selectSource").disabled = true;
  try {
    const selection = await selectPath(PATH_ROLES.source);
    if (!selection) {
      showToast("已取消选择；当前内容保持不变。");
      return;
    }
    clearPoll();
    state.create = {
      ...emptyCreateState(),
      source: selection,
    };
    element("projectName").value = suggestedProjectName(selection.displayName);
    element("rangeStart").value = "";
    element("rangeEnd").value = "";
    renderCreate();
    await startInspection();
  } catch (error) {
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
  } finally {
    renderCreate();
  }
}

function schedulePoll() {
  const generation = state.pollGeneration;
  if (!state.create.jobId || state.fixture) return;
  if (state.pollTimer !== null) window.clearTimeout(state.pollTimer);
  state.pollTimer = window.setTimeout(() => pollJob(generation), POLL_INTERVAL_MS);
}

async function startInspection() {
  if (!state.create.source?.selectionToken || state.fixture) return;
  clearPoll();
  state.create.jobKind = "inspection";
  state.create.analysisState = "running";
  state.create.phase = "正在准备分析";
  state.create.progress = { fraction: 0, bytesRead: 0, totalBytes: 0, parsedSpectra: 0 };
  state.create.inspection = null;
  state.create.target = null;
  renderCreate();
  const generation = state.pollGeneration;
  try {
    const response = await post(API_ENDPOINTS.sourceInspections, {
      source_token: state.create.source.selectionToken,
    });
    if (generation !== state.pollGeneration) return;
    await applyJob(response);
  } catch (error) {
    if (generation !== state.pollGeneration) return;
    state.create.analysisState = "error";
    state.create.jobId = "";
    renderCreate();
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
  }
}

function inspectionIsUsable(inspection) {
  return Boolean(inspection.inspectionToken)
    && Number.isFinite(inspection.availableRange.start_min)
    && Number.isFinite(inspection.availableRange.end_min)
    && inspection.availableRange.start_min <= inspection.availableRange.end_min;
}

function applyInspectionJob(job) {
  state.create.jobId = job.jobId || state.create.jobId;
  state.create.analysisState = job.state;
  state.create.phase = job.phase;
  state.create.progress = job.progress;
  if (job.state === "ready") {
    const inspection = normalizeInspection(job.result);
    if (!inspectionIsUsable(inspection)) {
      state.create.analysisState = "error";
      state.create.jobId = "";
      renderCreate();
      return;
    }
    state.create.inspection = inspection;
    state.create.source.displayName = inspection.displayName;
    state.create.jobId = "";
    element("rangeStart").value = formatMinute(inspection.availableRange.start_min);
    element("rangeEnd").value = formatMinute(inspection.availableRange.end_min);
    if (!element("projectName").value.trim()) {
      element("projectName").value = suggestedProjectName(inspection.displayName);
    }
  }
  if (["cancelled", "error"].includes(job.state)) state.create.jobId = "";
  renderCreate();
  if (["running", "cancelling"].includes(job.state)) schedulePoll();
}

async function applyCreationJob(job) {
  state.create.jobId = job.jobId || state.create.jobId;
  state.create.phase = job.phase;
  state.create.progress = job.progress;
  if (["running", "cancelling"].includes(job.state)) {
    state.create.analysisState = job.state === "cancelling" ? "cancelling" : "creating";
    renderCreate();
    schedulePoll();
    return;
  }
  state.create.jobId = "";
  if (job.state === "ready" && job.result?.project) {
    const summary = normalizeProject(job.result.project);
    try {
      await loadActiveWorkspace();
      closeDialog("create", { force: true });
      showToast("项目已创建，可以开始审阅。", "success");
    } catch (error) {
      state.activeProject = summary;
      state.bootstrap.activeProject = summary;
      state.bootstrap.view = "project";
      state.create.analysisState = "ready";
      renderCreate();
      showToast("项目已创建，但工作区暂时无法读取。请重新打开项目。", "error", { persistent: true });
    }
    return;
  }
  state.create.analysisState = "ready";
  renderCreate();
  if (job.state === "cancelled") {
    showToast("已取消创建；没有发布新项目。");
  } else {
    showToast("项目创建未完成。请检查保存位置后重试。", "error");
  }
}

async function applyJob(response) {
  const job = normalizeJob(response);
  if (!job.jobId && ["running", "cancelling"].includes(job.state)) {
    state.create.analysisState = state.create.jobKind === "creation" ? "ready" : "error";
    renderCreate();
    showToast("后台任务没有返回可用状态，请重试。", "error");
    return;
  }
  if (state.create.jobKind === "creation") await applyCreationJob(job);
  else applyInspectionJob(job);
}

async function pollJob(generation) {
  state.pollTimer = null;
  const jobId = state.create.jobId;
  if (!jobId || generation !== state.pollGeneration || state.fixture) return;
  try {
    const response = await apiRequest(API_ENDPOINTS.job(jobId));
    if (generation !== state.pollGeneration || jobId !== state.create.jobId) return;
    await applyJob(response);
  } catch (error) {
    if (generation !== state.pollGeneration) return;
    const creation = state.create.jobKind === "creation";
    state.create.jobId = "";
    state.create.analysisState = creation && state.create.inspection ? "ready" : "error";
    renderCreate();
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
  }
}

async function cancelCurrentJob() {
  if (state.fixture || !state.create.jobId || state.create.analysisState === "cancelling") return;
  const jobId = state.create.jobId;
  state.create.analysisState = "cancelling";
  state.create.phase = "正在安全取消";
  renderCreate();
  try {
    const response = await post(API_ENDPOINTS.cancelJob(jobId), {});
    if (jobId !== state.create.jobId) return;
    await applyJob(response);
  } catch (error) {
    if (jobId !== state.create.jobId) return;
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
    schedulePoll();
  }
}

async function chooseTarget() {
  if (state.fixture || state.create.analysisState !== "ready") return;
  element("selectTarget").disabled = true;
  try {
    const selection = await selectPath(PATH_ROLES.target);
    if (!selection) {
      showToast("已取消选择；项目尚未创建。");
      return;
    }
    state.create.target = selection;
  } catch (error) {
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
  } finally {
    element("selectTarget").disabled = false;
    renderCreate();
  }
}

async function createProject() {
  if (state.fixture || !canCreate()) return;
  const validation = currentRangeValidation();
  const error = element("rangeError");
  if (!validation.ok) {
    error.textContent = validation.message;
    error.hidden = false;
    element("rangeStart").focus();
    return;
  }
  error.hidden = true;
  clearPoll();
  state.create.jobKind = "creation";
  state.create.analysisState = "creating";
  state.create.phase = "正在准备项目";
  state.create.progress = { fraction: 0, bytesRead: 0, totalBytes: 0, parsedSpectra: 0 };
  renderCreate();
  const generation = state.pollGeneration;
  try {
    const response = await post(API_ENDPOINTS.projects, {
      source_token: state.create.source.selectionToken,
      inspection_token: state.create.inspection.inspectionToken,
      target_token: state.create.target.selectionToken,
      display_name: element("projectName").value.trim(),
      analysis_start_min: String(validation.start),
      analysis_end_min: String(validation.end),
    });
    if (generation !== state.pollGeneration) return;
    await applyJob(response);
  } catch (requestError) {
    if (generation !== state.pollGeneration) return;
    state.create.analysisState = "ready";
    state.create.jobId = "";
    renderCreate();
    showToast(requestError instanceof ApiError ? requestError.message : DEFAULT_ERROR_MESSAGE, "error");
  }
}

async function openProject(projectToken) {
  if (state.fixture || state.openBusy || !projectToken) return;
  state.openBusy = true;
  element("openDialog").setAttribute("aria-busy", "true");
  setText("openStatus", "正在检查并打开项目…");
  document.querySelectorAll(".open-project-item, .recent-project-button, #browseProject").forEach((button) => {
    button.disabled = true;
  });
  try {
    const response = await post(API_ENDPOINTS.openProject, { project_token: projectToken });
    if (!response.project) throw new ApiError("项目结果无法读取。", { code: "missing_project" });
    const summary = normalizeProject(response.project);
    state.workspace = null;
    state.activeProject = summary;
    state.bootstrap.activeProject = summary;
    state.bootstrap.view = "project";
    setText("openStatus", "正在准备事件工作区…");
    await loadActiveWorkspace();
    closeDialog("open", { force: true });
    showToast("项目已打开。", "success");
  } catch (error) {
    const message = error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE;
    setText("openStatus", `项目未能完整打开：${message}`);
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
  } finally {
    state.openBusy = false;
    element("openDialog").setAttribute("aria-busy", "false");
    document.querySelectorAll(".open-project-item, .recent-project-button, #browseProject").forEach((button) => {
      button.disabled = false;
    });
  }
}

async function browseProject() {
  if (state.fixture || state.openBusy) return;
  state.openBusy = true;
  element("openDialog").setAttribute("aria-busy", "true");
  element("browseProject").disabled = true;
  setText("openStatus", "请在系统窗口中选择项目文件夹。");
  try {
    const selection = await selectPath(PATH_ROLES.open);
    if (!selection) {
      setText("openStatus", "已取消选择；当前项目保持不变。");
      return;
    }
    state.openBusy = false;
    await openProject(selection.selectionToken);
  } catch (error) {
    setText("openStatus", error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE);
    showToast(error instanceof ApiError ? error.message : DEFAULT_ERROR_MESSAGE, "error");
  } finally {
    state.openBusy = false;
    element("openDialog").setAttribute("aria-busy", "false");
    element("browseProject").disabled = false;
  }
}

function installEvents() {
  element("welcomeCreate").addEventListener("click", () => openCreate());
  element("welcomeOpen").addEventListener("click", openOpen);
  element("welcomeBrowse").addEventListener("click", openOpen);
  element("headerNew").addEventListener("click", openOpen);
  element("changeRange").addEventListener("click", openRangeFlow);
  element("openExport").addEventListener("click", () => openExportFlow("review_results"));
  element("openNewProject").addEventListener("click", () => openCreate());
  element("selectSource").addEventListener("click", chooseSource);
  element("selectTarget").addEventListener("click", chooseTarget);
  element("cancelAnalysis").addEventListener("click", cancelCurrentJob);
  element("retryAnalysis").addEventListener("click", startInspection);
  element("createProject").addEventListener("click", createProject);
  element("browseProject").addEventListener("click", browseProject);
  element("submitRangePreview").addEventListener("click", submitRangePreview);
  element("applyRange").addEventListener("click", applyRangeChange);
  element("cancelRange").addEventListener("click", cancelRangeFlow);
  element("closeRange").addEventListener("click", cancelRangeFlow);
  element("reviewExportKind").addEventListener("click", () => setExportKind("review_results"));
  element("auditExportKind").addEventListener("click", () => setExportKind("audit_package"));
  element("includePending").addEventListener("change", () => {
    state.exportFlow.includePending = element("includePending").checked;
    renderExportFlow();
  });
  element("chooseExportTarget").addEventListener("click", chooseExportTarget);
  element("submitExport").addEventListener("click", submitExport);
  element("cancelExport").addEventListener("click", closeExportFlow);
  element("closeExport").addEventListener("click", closeExportFlow);
  element("previousEvent").addEventListener("click", () => navigateWorkspaceEvent(-1));
  element("nextEvent").addEventListener("click", () => navigateWorkspaceEvent(1));
  element("previousWindow").addEventListener("click", () => shiftWorkspaceWindow(-1));
  element("nextWindow").addEventListener("click", () => shiftWorkspaceWindow(1));
  element("windowStart").addEventListener("change", applyWorkspaceInputs);
  element("windowWidth").addEventListener("change", applyWorkspaceInputs);
  element("eventFilter").addEventListener("change", () => {
    const value = element("eventFilter").value;
    state.workspaceFilter = REVIEW_FILTER_VALUES.includes(value) ? value : "all";
    requestWorkspaceWindow({
      statusFilter: state.workspaceFilter,
      selectedEventToken: selectedWorkspaceEvent()?.eventToken || null,
    });
  });
  element("scaleLinear").addEventListener("click", () => {
    state.workspaceScale = "linear";
    if (state.workspace) renderWorkspace();
  });
  element("scaleLog").addEventListener("click", () => {
    state.workspaceScale = "log";
    if (state.workspace) renderWorkspace();
  });
  element("toggleLabels").addEventListener("click", () => {
    state.workspaceLabels = !state.workspaceLabels;
    if (state.workspace) renderWorkspace();
  });
  element("evidenceToggle").addEventListener("click", () => {
    state.moreEvidenceExpanded = !state.moreEvidenceExpanded;
    if (state.workspace) renderWorkspaceSelection();
  });
  element("reviewSegmented").querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => submitReviewDecision(button.dataset.decision));
  });
  element("clearReview").addEventListener("click", () => submitReviewDecision("unreviewed"));
  element("restoreAutomatic").addEventListener("click", restoreAutomaticApex);
  element("undoAction").addEventListener("click", () => mutateReviewHistory("undo"));
  element("redoAction").addEventListener("click", () => mutateReviewHistory("redo"));
  element("addEvent").addEventListener("click", () => beginEventEdit("add"));
  element("adjustApex").addEventListener("click", () => beginEventEdit("adjust"));
  element("applyEventEdit").addEventListener("click", applyEventEdit);
  element("cancelEventEdit").addEventListener("click", cancelEventEdit);
  element("signalPlot").addEventListener("mousemove", updateEventEditAim);
  element("editPosition").addEventListener("input", updateKeyboardEventEditPosition);
  element("editPosition").addEventListener("keydown", handleEventEditPositionKeydown);
  element("signalPlot").addEventListener("mouseleave", () => {
    if (!eventEditActive()) return;
    state.eventEdit.hoverTimeMin = null;
    renderEventEditOverlay(buildPlotGeometry(state.workspace, {
      logScale: state.workspaceScale === "log",
    }));
  });
  element("signalPlot").addEventListener("click", previewEventEdit);
  document.addEventListener("keydown", handleEventEditKeydown);
  document.addEventListener("keydown", handleReviewShortcut);

  element("closeCreate").addEventListener("click", () => closeDialog("create"));
  element("cancelCreate").addEventListener("click", () => closeDialog("create"));
  element("closeOpen").addEventListener("click", () => closeDialog("open"));
  element("cancelOpen").addEventListener("click", () => closeDialog("open"));

  ["projectName", "rangeStart", "rangeEnd"].forEach((id) => {
    element(id).addEventListener("input", () => {
      if (id !== "projectName") element("rangeError").hidden = true;
      renderCreateFooter();
    });
  });

  ["create", "open", "range", "export"].forEach((name) => {
    const dialog = element(`${name}Dialog`);
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      if (name === "range") cancelRangeFlow();
      else if (name === "export") closeExportFlow();
      else closeDialog(name);
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      if (name === "range") cancelRangeFlow();
      else if (name === "export") closeExportFlow();
      else closeDialog(name);
    });
    dialog.addEventListener("close", () => {
      if (state.modal === name) state.modal = null;
    });
  });
}

function applyReviewFixture(id) {
  if (!WORKBENCH_FIXTURE_IDS.includes(id) && !RANGE_EXPORT_FIXTURE_IDS.includes(id)) return false;
  const workspace = fixtureWorkspace(id) || fixtureWorkspace("review-unreviewed-auto");
  if (!workspace) return false;
  state.fixture = id;
  state.workspace = workspace;
  state.workspaceFilter = "all";
  state.workspaceScale = "linear";
  state.workspaceLabels = true;
  state.moreEvidenceExpanded = id === "long-chinese-copy";
  state.reviewSaveState = id === "save-in-progress"
    ? "saving"
    : ["save-failed", "long-chinese-copy"].includes(id)
      ? "error"
      : "idle";
  state.reviewError = id === "long-chinese-copy"
    ? "这条审阅暂未保存，原有结论和峰顶位置均未改变。请确认项目没有在其他窗口中编辑，再重新提交本次判断。"
    : id === "save-failed"
      ? "审阅未保存，原状态已恢复。请检查项目是否在其他窗口打开，然后重试。"
      : "";
  state.eventEdit = fixtureEventEdit(id) || emptyEventEditState();
  const r5Fixture = fixtureRangeExport(id);
  state.rangeFlow = r5Fixture?.range || emptyRangeFlow();
  state.exportFlow = r5Fixture?.export || emptyExportFlow();
  state.activeProject = {
    displayName: workspace.project.displayName,
    analysisRange: { ...workspace.project.analysisRange },
    eventCount: workspace.project.eventCount,
  };
  state.bootstrap = normalizeBootstrap({
    app: { name: "MS Event Studio", language: "zh-CN" },
    view: "project",
    recent_projects: [],
    active_project: {
      display_name: workspace.project.displayName,
      analysis_range: workspace.project.analysisRange,
      event_count: workspace.project.eventCount,
    },
    request_token: "fixture-request",
  });
  document.body.dataset.fixture = id;
  setView("project");
  renderWorkspace();
  if (id === "long-chinese-copy") {
    element("reviewNote").placeholder = "可选：记录本次判断依据、相邻扫描对照结果、质控样本差异与需要后续确认的事项";
    element("reviewNote").value = "暂按待定处理；待补齐下一批次质控样本后，对照相邻扫描的峰形和质量误差再作最终判断。";
    state.exportFlow = {
      ...emptyExportFlow(),
      state: "success",
      kind: "review_results",
      target: {
        selectionToken: "fixture-long-export-target",
        displayName: "跨年度多批次脂质组学审阅结果（含待定事件与复核说明）.csv",
      },
      includePending: true,
      result: {
        kind: "review_results",
        displayName: "跨年度多批次脂质组学审阅结果（含待定事件与复核说明）.csv",
        rowCount: 7,
        message: "导出已完成；文件包含已保留与待定事件，以及供后续复核使用的中文说明。",
      },
    };
    element("exportNote").value = "供下一阶段跨批次复核、归档和结果交接使用；请保留本次判断说明。";
    renderExportFlow();
    openDialog("export");
  } else {
    element("reviewNote").placeholder = "可选：记录判断依据";
    element("reviewNote").value = "";
  }
  if (r5Fixture) {
    if (id.startsWith("range-")) {
      const inputRange = state.rangeFlow.newRange || { startMin: 2.25, endMin: 88.5 };
      element("rangeChangeStart").value = formatMinute(inputRange.startMin);
      element("rangeChangeEnd").value = formatMinute(inputRange.endMin);
      element("rangeNote").value = "缩小到重点信号区间";
      renderRangeFlow();
      openDialog("range");
    } else {
      element("exportNote").value = "阶段性复核导出";
      renderExportFlow();
      openDialog("export");
    }
  }
  return true;
}

function applyFixture(id) {
  const scenario = fixtureScenario(id);
  if (!scenario) return false;
  state.fixture = scenario.id;
  state.bootstrap = scenario.bootstrap;
  state.create = scenario.create;
  document.body.dataset.fixture = scenario.id;
  renderApp();

  if (scenario.id === "create-ready") {
    element("projectName").value = "Lin− MPP 审阅项目";
    element("rangeStart").value = formatMinute(scenario.create.inspection.availableRange.start_min);
    element("rangeEnd").value = formatMinute(scenario.create.inspection.availableRange.end_min);
  }
  renderCreate();
  if (scenario.dialog) openDialog(scenario.dialog);
  return true;
}

async function start() {
  installEvents();
  const requestedFixture = allowedFixture(new URLSearchParams(window.location.search).get("fixture"));
  try {
    if (requestedFixture && applyReviewFixture(requestedFixture)) {
      // Deterministic review fixtures are fully in-memory and issue no requests.
    } else if (!requestedFixture || !applyFixture(requestedFixture)) {
      state.bootstrap = normalizeBootstrap(await apiRequest(API_ENDPOINTS.bootstrap));
      renderApp();
      renderCreate();
      if (state.bootstrap.view === "project" && state.activeProject) {
        await loadActiveWorkspace();
      }
    }
  } catch (error) {
    state.bootstrap = normalizeBootstrap({
      app: { name: "MS Event Studio", language: "zh-CN" },
      view: "welcome",
      recent_projects: [],
      active_project: null,
      request_token: "",
    });
    renderApp();
    renderCreate();
    showToast(error instanceof ApiError ? error.message : "应用启动失败，请重新启动。", "error", { persistent: true });
  } finally {
    state.ready = true;
    resolveReady(publicState());
  }
}

start();
