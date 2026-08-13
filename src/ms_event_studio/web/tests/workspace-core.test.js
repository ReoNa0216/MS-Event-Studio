import assert from "node:assert/strict";
import test from "node:test";

import {
  EVENT_EDIT_FIXTURE_IDS,
  PLOT_LAYOUT,
  REVIEW_FILTER_VALUES,
  REVIEW_FIXTURE_IDS,
  REVIEW_SAVE_FIXTURE_IDS,
  RESPONSIVE_FIXTURE_IDS,
  WORKBENCH_FIXTURE_IDS,
  buildPlotGeometry,
  eventEditAimBody,
  eventEditApplyBody,
  eventEditCancelBody,
  eventEditDefaultTime,
  eventEditFocusViewport,
  eventEditHitGeometry,
  eventEditKeyboardStep,
  eventEditPreviewBody,
  eventEditTimeFromHitX,
  fixtureEventEdit,
  fixtureWorkspace,
  formatAdjustmentRange,
  normalizeWorkspace,
  normalizeEventEditAim,
  normalizeEventEditPreview,
  PLOT_LABEL_LIMIT,
  placePlotLabels,
  plotTimeFromClientPoint,
  restoreAutomaticApexBody,
  reviewDecisionBody,
  reviewHistoryBody,
  workspaceRequestBody,
} from "../workspace-core.js";

test("review fixtures are explicit and canonical", () => {
  assert.deepEqual(REVIEW_FIXTURE_IDS, [
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
  for (const id of REVIEW_FIXTURE_IDS) {
    const workspace = fixtureWorkspace(id);
    assert.ok(workspace, id);
    assert.equal(workspace.window.trace.every((point, index, rows) => (
      index === 0 || rows[index - 1].timeMin <= point.timeMin
    )), true);
  }
  assert.equal(fixtureWorkspace("not-allowed"), null);
});

test("manual fixture keeps its status and source separate", () => {
  const workspace = fixtureWorkspace("review-manual");
  assert.equal(workspace.selection.event.status, "accepted");
  assert.equal(workspace.selection.event.origin, "manual_adjusted");
  assert.equal(workspace.selection.event.canRestoreAutomaticApex, true);
});

test("save fixtures reuse the canonical workbench without issuing persistence claims", () => {
  assert.deepEqual(REVIEW_SAVE_FIXTURE_IDS, ["save-in-progress", "save-failed"]);
  assert.deepEqual(WORKBENCH_FIXTURE_IDS, [
    ...REVIEW_FIXTURE_IDS,
    ...REVIEW_SAVE_FIXTURE_IDS,
    ...EVENT_EDIT_FIXTURE_IDS,
    ...RESPONSIVE_FIXTURE_IDS,
  ]);
  for (const id of REVIEW_SAVE_FIXTURE_IDS) {
    const workspace = fixtureWorkspace(id);
    assert.ok(workspace, id);
    assert.equal(workspace.selection.event.status, "unreviewed");
    assert.ok(workspace.selection.event.actionToken.startsWith("fixture-action-"));
  }
});

test("responsive fixtures freeze history semantics and long Chinese boundary copy", () => {
  assert.deepEqual(RESPONSIVE_FIXTURE_IDS, [
    "undo-empty",
    "undo-redo-ready",
    "long-chinese-copy",
  ]);
  assert.deepEqual(fixtureWorkspace("undo-empty").history, {
    canUndo: false,
    canRedo: false,
  });
  assert.deepEqual(fixtureWorkspace("undo-redo-ready").history, {
    canUndo: true,
    canRedo: true,
  });
  const workspace = fixtureWorkspace("long-chinese-copy");
  assert.ok([...workspace.project.displayName].length >= 107);
  assert.match(workspace.project.displayName, /[\u4e00-\u9fff]/u);
  assert.match(workspace.project.displayName, /[A-Za-z]/u);
  assert.ok(workspace.selection.event.statusLabel.length >= 10);
  assert.ok(workspace.selection.event.originLabel.length >= 10);
  assert.equal(workspace.selection.core.quality.level, "attention");
  assert.equal(workspace.selection.core.quality.notes.length, 2);
  assert.ok(workspace.selection.core.quality.notes.every((note) => note.length >= 20));
  assert.ok(workspace.selection.event.eventToken.startsWith("fixture-event-"));
  assert.ok(workspace.selection.event.actionToken.startsWith("fixture-action-"));
});

test("event edit fixtures expose all explicit aim, preview, and error states", () => {
  assert.deepEqual(EVENT_EDIT_FIXTURE_IDS, [
    "add-aim",
    "add-preview",
    "adjust-aim",
    "adjust-preview",
    "edit-out-of-range",
  ]);
  for (const id of EVENT_EDIT_FIXTURE_IDS) {
    assert.ok(fixtureWorkspace(id), id);
    const edit = fixtureEventEdit(id);
    assert.ok(edit.token, id);
    assert.ok(edit.allowedInterval, id);
  }
  assert.equal(fixtureEventEdit("add-aim").state, "aiming");
  assert.equal(fixtureEventEdit("adjust-preview").state, "preview");
  assert.equal(fixtureEventEdit("edit-out-of-range").state, "error");
  assert.equal(fixtureEventEdit("edit-out-of-range").mode, "adjust");
});

test("event edit API models keep only opaque and display-safe fields", () => {
  const event = fixtureWorkspace("review-unreviewed-auto").selection.event;
  assert.deepEqual(eventEditAimBody("add", event), { mode: "add" });
  assert.deepEqual(eventEditAimBody("adjust", event), {
    mode: "adjust",
    action_token: event.actionToken,
  });
  assert.deepEqual(eventEditPreviewBody("opaque-aim", 31.842), {
    aim_token: "opaque-aim",
    click_time_min: 31.842,
  });
  assert.deepEqual(eventEditApplyBody("opaque-preview", "峰形明确"), {
    preview_token: "opaque-preview",
    note: "峰形明确",
  });
  assert.deepEqual(eventEditCancelBody("opaque-edit"), { edit_token: "opaque-edit" });

  const aim = normalizeEventEditAim({
    aim_token: "opaque-aim",
    mode: "adjust",
    before: { time_min: 31.82, intensity: 100 },
    allowed_interval: { start_min: 31.7, end_min: 31.9 },
    revision: 9,
  });
  assert.deepEqual(aim, {
    token: "opaque-aim",
    mode: "adjust",
    before: { timeMin: 31.82, intensity: 100 },
    allowedInterval: { startMin: 31.7, endMin: 31.9 },
  });
  const preview = normalizeEventEditPreview({
    preview_token: "opaque-preview",
    mode: "adjust",
    candidate: { time_min: 31.842, intensity: 120, offset_sec: 1.32, scan_row_index: 7 },
    change: {
      before: { time_min: 31.82, intensity: 100 },
      after: { time_min: 31.842, intensity: 120 },
    },
    allowed_interval: { start_min: 31.7, end_min: 31.9 },
  });
  assert.equal(JSON.stringify(preview).includes("scan_row_index"), false);
  assert.equal(preview.candidate.offsetSec, 1.32);
});

test("manual additions render absent adjustment evidence without dereferencing null", () => {
  assert.equal(formatAdjustmentRange(null), "—");
  assert.equal(formatAdjustmentRange({ start_sec: null, end_sec: null }), "—");
  assert.equal(formatAdjustmentRange({ start_sec: 30.1, end_sec: 31.2 }), "30.100–31.200 s");
});

test("plot hit conversion rejects every point outside the content rectangle", () => {
  const svg = {
    getScreenCTM: () => ({ inverse: () => ({}) }),
    createSVGPoint: () => ({
      x: 0,
      y: 0,
      matrixTransform() { return { x: this.x, y: this.y }; },
    }),
  };
  const viewport = { start_min: 30, end_min: 40 };
  assert.equal(plotTimeFromClientPoint(svg, 62, 200, viewport).timeMin, 30);
  assert.equal(plotTimeFromClientPoint(svg, 980, 200, viewport).timeMin, 40);
  for (const [x, y] of [[61, 200], [981, 200], [500, 25], [500, 543]]) {
    assert.equal(plotTimeFromClientPoint(svg, x, y, viewport), null);
  }
});

test("narrow edit intervals get a separate hit target mapped inside canonical bounds", () => {
  const interval = { startMin: 0.499833333333, endMin: 0.500166666667 };
  const viewport = { start_min: 0, end_min: 2 };
  const hit = eventEditHitGeometry(interval, viewport, PLOT_LAYOUT.content, 24);
  assert.ok(hit);
  assert.ok(hit.visibleRight - hit.visibleLeft < 1);
  assert.equal(hit.expanded, true);
  assert.equal(hit.hitRight - hit.hitLeft, 24);
  assert.equal(eventEditTimeFromHitX(hit.hitLeft - 0.01, hit), null);
  const left = eventEditTimeFromHitX(hit.hitLeft, hit);
  const middle = eventEditTimeFromHitX((hit.hitLeft + hit.hitRight) / 2, hit);
  const right = eventEditTimeFromHitX(hit.hitRight, hit);
  assert.ok(left > interval.startMin);
  assert.ok(right < interval.endMin);
  assert.ok(middle > left && middle < right);
  assert.ok(Math.abs(middle - 0.5) < 1e-12);
});

test("keyboard edit position stays within the exact interval with a fine dynamic step", () => {
  const interval = { startMin: 0.499833333333, endMin: 0.500166666667 };
  assert.equal(eventEditDefaultTime(interval, { timeMin: 0.5 }), 0.5);
  assert.equal(eventEditDefaultTime(interval, { timeMin: 0.2 }), interval.startMin);
  assert.equal(eventEditDefaultTime(interval), 0.5);
  const step = eventEditKeyboardStep(interval);
  assert.ok(step > 0);
  assert.ok(step <= (interval.endMin - interval.startMin) / 100 + Number.EPSILON);
});

test("adjustment focus viewport exposes local morphology without changing the allowed interval", () => {
  const centered = eventEditFocusViewport(
    { startMin: 2.543391, endMin: 2.54685 },
    { start_min: 2.034, end_min: 3.034, analysis_start_min: 0, analysis_end_min: 10 },
  );
  assert.ok(Math.abs(centered.startMin - 2.5351205) < 1e-12);
  assert.ok(Math.abs(centered.endMin - 2.5551205) < 1e-12);
  assert.deepEqual(
    eventEditFocusViewport(
      { startMin: 0.001, endMin: 0.004 },
      { start_min: 0, end_min: 1, analysis_start_min: 0, analysis_end_min: 10 },
    ),
    { startMin: 0, endMin: 0.02 },
  );
  assert.deepEqual(
    eventEditFocusViewport(
      { startMin: 9.996, endMin: 9.999 },
      { start_min: 9, end_min: 10, analysis_start_min: 0, analysis_end_min: 10 },
    ),
    { startMin: 9.98, endMin: 10 },
  );
});

test("review mutation bodies are closed, opaque, and preserve the operation note", () => {
  const event = fixtureWorkspace("review-unreviewed-auto").selection.event;
  assert.deepEqual(reviewDecisionBody(event, "accepted", "峰形明确"), {
    action_token: event.actionToken,
    decision: "keep",
    note: "峰形明确",
  });
  assert.deepEqual(reviewDecisionBody(event, "rejected", "背景干扰"), {
    action_token: event.actionToken,
    decision: "exclude",
    note: "背景干扰",
  });
  assert.equal(reviewDecisionBody(event, "pending").decision, "pending");
  assert.equal(reviewDecisionBody(event, "unreviewed").decision, "clear");
  assert.deepEqual(restoreAutomaticApexBody(event, "恢复位置"), {
    action_token: event.actionToken,
    note: "恢复位置",
  });
  assert.deepEqual(reviewHistoryBody("撤销判断"), { note: "撤销判断" });
  assert.throws(() => reviewDecisionBody({}, "accepted"), /操作凭据/);
  assert.throws(() => reviewDecisionBody(event, "unknown"), /审阅结论/);
});

test("workspace normalizer exposes only browser-safe keys", () => {
  const workspace = normalizeWorkspace({
    project: {
      display_name: "Project",
      analysis_range: { start_min: 1, end_min: 3 },
      event_count: 1,
      project_id: "must-not-pass",
      path: "C:\\secret",
    },
    review: { total: 1, reviewed: 0, unreviewed: 1 },
    events: [{
      event_token: "opaque-event",
      action_token: "opaque-action",
      event_id: "raw-event-id",
      revision: 17,
      current_apex_time_ns: 2_000_000_000,
      scan_row_index: 22,
      apex_time_min: 2,
      apex_time_sec: 120,
      apex_intensity: 20,
      status: "unreviewed",
      origin: "automatic",
      marker: { shape: "triangle", color: "#123456", code: "U", dash: [] },
    }],
    selection: { event: { event_token: "opaque-event", status: "unreviewed" } },
    window: {
      viewport: { start_min: 1, end_min: 3, analysis_start_min: 1, analysis_end_min: 3 },
      trace: [{ time_min: 1, intensity: 2 }, { time_min: 2, intensity: 20 }],
      event_overlay: [],
      label_event_tokens: [],
      bucket_size: 64,
    },
    history: { can_undo: true, can_redo: false },
    schema: "internal",
  });
  const serialized = JSON.stringify(workspace);
  for (const forbidden of [
    "raw-event-id",
    "revision",
    "scan_row_index",
    "current_apex_time_ns",
    "project_id",
    "C:\\\\secret",
    "bucket_size",
    "schema",
  ]) {
    assert.equal(serialized.includes(forbidden), false, forbidden);
  }
  assert.equal(workspace.events[0].eventToken, "opaque-event");
  assert.equal(workspace.events[0].actionToken, "opaque-action");
});

test("filters preserve the frozen canonical order", () => {
  const workspace = fixtureWorkspace("review-unreviewed-auto");
  assert.deepEqual(workspace.filters.map((filter) => filter.value), REVIEW_FILTER_VALUES);
  assert.deepEqual(REVIEW_FILTER_VALUES, [
    "all",
    "unreviewed",
    "accepted",
    "rejected",
    "pending",
    "manual_added",
    "manual_adjusted",
  ]);
});

test("window request body is a strict browser-safe view request", () => {
  const workspace = fixtureWorkspace("review-unreviewed-auto");
  const body = workspaceRequestBody(workspace, {
    selectedEventToken: "opaque selection",
    statusFilter: "accepted",
    startMin: 31.5,
    endMin: 33.5,
    pointBudget: 3000,
    maximumLabels: 20,
  });
  assert.deepEqual(Object.keys(body), [
    "start_min",
    "end_min",
    "point_budget",
    "status_filter",
    "selected_event_token",
    "maximum_labels",
  ]);
  assert.equal(body.selected_event_token, "opaque selection");
  assert.equal(body.status_filter, "accepted");
});

test("null window overrides preserve the current viewport", () => {
  const workspace = fixtureWorkspace("review-unreviewed-auto");
  const body = workspaceRequestBody(workspace, {
    selectedEventToken: workspace.selection.nextEventToken,
    startMin: null,
    endMin: null,
  });
  assert.equal(body.start_min, workspace.window.viewport.start_min);
  assert.equal(body.end_min, workspace.window.viewport.end_min);
  assert.notEqual(body.end_min, 0);
});

test("all marker shapes stay inside the content boundary with a four-pixel gap", () => {
  for (const id of [
    "review-unreviewed-auto",
    "review-accepted-auto",
    "review-rejected-auto",
    "review-pending-auto",
    "review-highest",
    "review-edge",
    "review-dense",
  ]) {
    for (const logScale of [false, true]) {
      const geometry = buildPlotGeometry(fixtureWorkspace(id), { logScale });
      for (const marker of geometry.markers) {
        assert.ok(marker.bounds.left >= geometry.content.left + PLOT_LAYOUT.safeGap, `${id} left`);
        assert.ok(marker.bounds.right <= geometry.content.right - PLOT_LAYOUT.safeGap, `${id} right`);
        assert.ok(marker.bounds.top >= geometry.content.top + PLOT_LAYOUT.safeGap, `${id} top`);
        assert.ok(marker.bounds.bottom <= geometry.content.bottom - PLOT_LAYOUT.safeGap, `${id} bottom`);
      }
      assert.ok(geometry.headroomFraction >= 0.08 - Number.EPSILON, `${id} headroom`);
      assert.ok(geometry.headroomFraction <= 0.12 + Number.EPSILON, `${id} headroom`);
    }
  }
});

test("dense labels avoid collisions and remain clipped inside content", () => {
  const workspace = fixtureWorkspace("review-dense");
  workspace.window.labelEventTokens = workspace.window.eventOverlay.map((event) => event.eventToken);
  const geometry = buildPlotGeometry(workspace);
  const labels = placePlotLabels(
    geometry,
    workspace.window.labelEventTokens,
    workspace.selection.event.eventToken,
    30,
  );
  assert.ok(labels.length > 0);
  for (const label of labels) {
    assert.ok(label.box.left >= geometry.content.left + PLOT_LAYOUT.safeGap);
    assert.ok(label.box.right <= geometry.content.right - PLOT_LAYOUT.safeGap);
    assert.ok(label.box.top >= geometry.content.top + PLOT_LAYOUT.safeGap);
    assert.ok(label.box.bottom <= geometry.content.bottom - PLOT_LAYOUT.safeGap);
  }
  for (let left = 0; left < labels.length; left += 1) {
    for (let right = left + 1; right < labels.length; right += 1) {
      if (labels[left].selected || labels[right].selected) continue;
      const a = labels[left].box;
      const b = labels[right].box;
      assert.ok(a.right + 4 <= b.left || b.right + 4 <= a.left || a.bottom + 4 <= b.top || b.bottom + 4 <= a.top);
    }
  }
});

test("production label limit keeps large overlays readable without suppressing markers", () => {
  const workspace = fixtureWorkspace("review-dense");
  const template = workspace.window.eventOverlay[0];
  workspace.window.eventOverlay = Array.from({ length: 1414 }, (_, index) => ({
    ...template,
    eventToken: `massive-event-${index}`,
    apexTimeMin: 88 * index / 1413,
    apexIntensity: 1000 + (index % 37) * 140,
  }));
  workspace.window.viewport = {
    start_min: 0,
    end_min: 88,
    analysis_start_min: 0,
    analysis_end_min: 88,
  };
  workspace.window.labelEventTokens = Array.from(
    { length: PLOT_LABEL_LIMIT },
    (_, index) => `massive-event-${Math.round(index * 1413 / (PLOT_LABEL_LIMIT - 1))}`,
  );
  const geometry = buildPlotGeometry(workspace);
  const labels = placePlotLabels(
    geometry,
    workspace.window.labelEventTokens,
    "massive-event-706",
  );

  assert.equal(geometry.markers.length, 1414);
  assert.ok(labels.length > 0);
  assert.ok(labels.length <= PLOT_LABEL_LIMIT);
  assert.ok(labels.some((label) => label.selected));
  const placedTokens = labels.map((label) => label.event.eventToken);
  assert.ok(placedTokens.some((token) => Number(token.split("-").at(-1)) < 250));
  assert.ok(placedTokens.some((token) => Number(token.split("-").at(-1)) > 1150));
  for (let left = 0; left < labels.length; left += 1) {
    for (let right = left + 1; right < labels.length; right += 1) {
      const a = labels[left].box;
      const b = labels[right].box;
      assert.ok(a.right + 4 <= b.left || b.right + 4 <= a.left || a.bottom + 4 <= b.top || b.bottom + 4 <= a.top);
    }
  }
});

test("persistent callouts are deterministic and never depend on pointer position", () => {
  const workspace = fixtureWorkspace("review-dense");
  const selectedToken = workspace.selection.event.eventToken;
  const geometry = buildPlotGeometry(workspace);
  const first = placePlotLabels(
    geometry,
    [...workspace.window.labelEventTokens, selectedToken],
    selectedToken,
    PLOT_LABEL_LIMIT,
  );
  const second = placePlotLabels(
    geometry,
    [...workspace.window.labelEventTokens, selectedToken],
    selectedToken,
    PLOT_LABEL_LIMIT,
  );

  assert.deepEqual(second, first);
  assert.ok(first.length <= PLOT_LABEL_LIMIT);
  assert.ok(first.some((label) => label.event.eventToken === selectedToken && label.selected));
  assert.ok(first.every((label) => /^\d+\.\d{3}$/.test(label.text)));
});

test("highest and edge callouts avoid the legend and prioritize selection", () => {
  for (const id of ["review-highest", "review-edge"]) {
    const workspace = fixtureWorkspace(id);
    const geometry = buildPlotGeometry(workspace);
    const labels = placePlotLabels(
      geometry,
      workspace.window.labelEventTokens,
      workspace.selection.event.eventToken,
      30,
    );
    const selected = labels.find((label) => label.selected);
    assert.ok(selected, `${id} selected label`);
    assert.ok(
      selected.box.right + 4 <= PLOT_LAYOUT.legend.left
      || PLOT_LAYOUT.legend.right + 4 <= selected.box.left
      || selected.box.bottom + 4 <= PLOT_LAYOUT.legend.top
      || PLOT_LAYOUT.legend.bottom + 4 <= selected.box.top,
      `${id} legend separation`,
    );
    for (const label of labels) {
      if (label === selected) continue;
      assert.ok(
        selected.box.right + 4 <= label.box.left
        || label.box.right + 4 <= selected.box.left
        || selected.box.bottom + 4 <= label.box.top
        || label.box.bottom + 4 <= selected.box.top,
        `${id} selected separation`,
      );
    }
  }
});

test("event overlay remains independent from the decimated trace", () => {
  const workspace = fixtureWorkspace("review-highest");
  const selected = workspace.selection.event;
  workspace.window.trace = workspace.window.trace.filter((point) => (
    Math.abs(point.timeMin - selected.apexTimeMin) > 0.04
  ));
  const geometry = buildPlotGeometry(workspace);
  assert.equal(geometry.markers.some((marker) => marker.event.eventToken === selected.eventToken), true);
});
