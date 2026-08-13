import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function asset(name) {
  return readFile(fileURLToPath(new URL(name, root)), "utf8");
}

test("HTML declares one Chinese-first application shell with accessible dialogs", async () => {
  const html = await asset("index.html");
  assert.match(html, /<html lang="zh-CN">/);
  assert.match(html, /<meta name="viewport"/);
  assert.equal((html.match(/<main\b/g) || []).length, 1);
  assert.match(html, /<dialog id="createDialog"[^>]+aria-modal="true"/);
  assert.match(html, /<dialog id="openDialog"[^>]+aria-modal="true"/);
  assert.match(html, /<dialog id="rangeDialog"[^>]+aria-modal="true"/);
  assert.match(html, /<dialog id="exportDialog"[^>]+aria-modal="true"/);
  assert.equal((html.match(/class="modal__surface[^"]*" tabindex="-1"/g) || []).length, 4);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /<progress id="analysisProgress"/);
  assert.doesNotMatch(html, /type="file"/i);
});

test("review workbench exposes stable semantic QA hooks", async () => {
  const html = await asset("index.html");
  for (const hook of [
    "workbench",
    "project-name",
    "previous-event",
    "next-event",
    "event-filter",
    "scale-linear",
    "scale-log",
    "toggle-labels",
    "review-segmented",
    "review-accept",
    "review-reject",
    "review-pending",
    "review-clear",
    "core-evidence",
    "evidence-toggle",
    "more-evidence",
    "review-note",
    "review-error",
    "restore-automatic",
    "add-event",
    "adjust-apex",
    "edit-mode-bar",
    "edit-allowed-range",
    "edit-allowed-hit",
    "edit-position-readout",
    "edit-candidate",
    "edit-change",
    "edit-apply",
    "edit-cancel",
    "edit-error",
    "undo",
    "redo",
    "plot-svg",
    "plot-content",
    "plot-legend",
    "change-range",
    "export-review-results",
    "export-audit-package",
    "range-dialog",
    "range-start",
    "range-end",
    "range-submit-preview",
    "range-progress",
    "range-old",
    "range-new",
    "impact-reusable",
    "impact-moved-out",
    "impact-reconfirm",
    "impact-newly-detected",
    "impact-retained-manual",
    "range-apply",
    "range-cancel",
    "range-error",
    "export-dialog",
    "export-include-pending",
    "export-choose-target",
    "export-target-name",
    "export-submit",
    "export-progress",
    "export-result",
    "export-cancel",
    "export-error",
  ]) {
    assert.match(html, new RegExp(`data-qa="${hook}"`), hook);
  }
  assert.match(html, /id="reviewSegmented"[^>]+role="radiogroup"/);
  assert.match(html, /id="reviewError"[^>]+role="alert"[^>]+data-qa="review-error"/);
  assert.match(html, /id="eventList"[^>]+role="listbox"/);
  assert.match(html, /id="eventLayer"[^>]+clip-path="url\(#plotContentClip\)"/);
  assert.match(html, /id="editOverlayLayer"[^>]+clip-path="url\(#plotContentClip\)"/);
  assert.match(html, /id="editError"[^>]+role="alert"[^>]+data-qa="edit-error"/);
  assert.match(html, /id="signalPlot"[^>]+role="img"[^>]+tabindex="-1"[^>]+data-qa="plot-svg"/);
  assert.match(html, /id="editPositionValue"[^>]+aria-live="polite"[^>]+data-qa="edit-position-readout"/);
  assert.doesNotMatch(html, /type="range"|id="editPosition"/);
});

test("design tokens are a single shared source for the approved family palette", async () => {
  const tokens = await asset("tokens.css");
  const css = await asset("app.css");
  for (const color of [
    "#f6f7f9",
    "#ffffff",
    "#1b1f27",
    "#667085",
    "#d7dce3",
    "#067647",
    "#b42318",
    "#b54708",
    "#2e90fa",
  ]) {
    assert.ok(tokens.includes(color), `missing token ${color}`);
  }
  assert.match(css, /:focus-visible/);
  assert.match(css, /\.skip-link:focus-visible\s*\{[^}]*outline:\s*3px solid var\(--color-focus\)/s);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}/i);
  assert.match(
    css,
    /\.interaction-hint\s*\{[^}]*color:\s*var\(--color-text-muted\)/s,
  );
  assert.match(
    css,
    /\.event-row\[aria-selected="true"\] \.event-row__copy small\s*\{[^}]*color:\s*var\(--color-text\)/s,
  );
  assert.match(
    css,
    /\.selected-event-panel:not\(\[hidden\]\)\s*\{[^}]*margin-top:\s*var\(--space-7\)[^}]*padding-top:\s*var\(--space-6\)/s,
  );
  assert.match(
    css,
    /\.toolbar-window label,[\s\S]*?\.toolbar-field\s*\{[^}]*font-size:\s*var\(--font-size-secondary\)/s,
  );
});

test("welcome title follows the frozen LMA 20px hierarchy", async () => {
  const tokens = await asset("tokens.css");
  const css = await asset("app.css");
  assert.match(tokens, /--font-size-welcome:\s*20px/);
  assert.match(css, /\.welcome-card h1\s*\{[^}]*font-size:\s*var\(--font-size-welcome\)/s);
  assert.doesNotMatch(css, /\.welcome-card h1\s*\{[^}]*28px/s);
});

test("long project names get a compact complete header treatment", async () => {
  const js = await asset("app.js");
  const css = await asset("app.css");
  assert.match(js, /projectContext"\)\.dataset\.longName/);
  assert.match(css, /\.project-context\[data-long-name="true"\]/);
  assert.doesNotMatch(
    css,
    /project-context\[data-long-name="true"\][^{]*\{[^}]*text-overflow:\s*ellipsis/s,
  );
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*?\.project-context\s*\{\s*display:\s*none/s);
});

test("browser code never calls a direct native bridge or persistent browser storage", async () => {
  const js = await asset("app.js");
  assert.doesNotMatch(js, /pywebview/i);
  assert.doesNotMatch(js, /localStorage|sessionStorage|indexedDB/);
  assert.match(js, /post\(API_ENDPOINTS\.selectPath, \{ role \}\)/);
  assert.doesNotMatch(js, /post\(API_ENDPOINTS\.selectPath, \{ role, title \}\)/);
  assert.match(js, /card\.setAttribute\(\s*"aria-busy"/);
  assert.match(js, /function focusDialog\(dialogName\)/);
  assert.match(js, /target\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(js, /\["cancelled", "error"\]\.includes\(state\.create\.analysisState\)/);
  assert.match(js, /element\("selectTarget"\)\.closest\("\.target-picker"\)\?\.scrollIntoView/);
  assert.doesNotMatch(js, /document\.body\.focus/);
  assert.doesNotMatch(js, /style:\s*`--event-color/);
  assert.match(js, /async function loadActiveWorkspace\(\)/);
  assert.match(js, /await loadActiveWorkspace\(\)/);
  assert.match(js, /state\.reviewSaveState = "saving"/);
  assert.match(js, /state\.workspace = previousWorkspace/);
  assert.match(js, /reviewDecisionBody\(event, status, element\("reviewNote"\)\.value\)/);
  assert.match(js, /editableShortcutTarget\(event\.target\)/);
  assert.match(js, /plotTimeFromClientPoint\(/);
  assert.match(js, /target\?\.closest\("#plotLegend, #eventLayer"\)/);
  assert.match(js, /eventEditCancelBody\(token\)/);
  assert.match(js, /eventEditTimeFromHitX\(point\.x, hit\)/);
  assert.match(js, /element\("signalPlot"\)\.addEventListener\("keydown", handleEventEditPlotKeydown\)/);
  assert.match(js, /eventEditFocusViewport\(aim\.allowedInterval, returnViewport\)/);
  assert.match(js, /state\.workspace = await workspaceAtViewport\(focused\)/);
  assert.match(js, /allowedInterval: preview\.allowedInterval,[\s\S]*returnViewport: edit\.returnViewport/);
  assert.match(
    js,
    /const selectedToken = selectedWorkspaceEvent\(\)\?\.eventToken \|\| "";[\s\S]*placePlotLabels\([\s\S]*state\.workspace\.window\.labelEventTokens,[\s\S]*selectedToken,[\s\S]*PLOT_LABEL_LIMIT/,
  );
  assert.doesNotMatch(js, /hoveredEventToken/);
  assert.doesNotMatch(js, /localStorage|sessionStorage|indexedDB/);
});

test("plot labels follow the compact stable LMA treatment", async () => {
  const js = await asset("app.js");
  const css = await asset("app.css");
  const labelRenderer = js.slice(
    js.indexOf("function renderPlotLabels"),
    js.indexOf("function renderPlotLegend"),
  );
  assert.match(labelRenderer, /text\.textContent = placement\.text/);
  assert.doesNotMatch(labelRenderer, /mouseenter|mouseleave|focusin|focusout|svgElement\("rect"/);
  assert.match(css, /\.plot-callout text\s*\{[^}]*paint-order:\s*stroke/s);
  assert.match(css, /\.plot-callout text\s*\{[^}]*font-size:\s*var\(--font-size-compact\)/s);
  assert.doesNotMatch(css, /\.plot-callout rect/);
});

test("candidate preview removes aiming instructions and crosshair semantics", async () => {
  const js = await asset("app.js");
  const css = await asset("app.css");
  assert.match(js, /const aimState = \["aiming", "error"\]\.includes\(edit\.state\)/);
  assert.match(js, /element\("editPositionFact"\)\.hidden = !interval \|\| !aimState/);
  assert.match(js, /dataset\.editState = eventEditActive\(\)[\s\S]*state\.eventEdit\.state[\s\S]*"selected"/);
  assert.match(
    css,
    /\.project-view:is\(\[data-edit-state="aiming"\], \[data-edit-state="error"\]\) \.plot-frame\s*\{[^}]*cursor:\s*crosshair/s,
  );
  assert.doesNotMatch(css, /data-edit-active="true"[^}]*cursor:\s*crosshair/s);
});

test("committed event edits cannot fall back into the pre-response rollback branch", async () => {
  const js = await asset("app.js");
  const start = js.indexOf("async function applyEventEdit()");
  const end = js.indexOf("async function cancelEventEdit()", start);
  const body = js.slice(start, end);
  const rollback = body.indexOf("state.workspace = previousWorkspace;");
  const commit = body.indexOf("state.workspace = updatedWorkspace;");
  assert.ok(start >= 0 && end > start);
  assert.ok(rollback >= 0 && commit > rollback);
  assert.equal(body.slice(commit).includes("state.workspace = previousWorkspace;"), false);
  assert.match(body.slice(commit), /catch \(_renderError\)[\s\S]*await loadActiveWorkspace\(\)/);
  assert.match(body.slice(commit), /更改已保存，但界面刷新失败/);
});

test("normal visible HTML does not expose implementation vocabulary", async () => {
  const html = await asset("index.html");
  const blocked = [
    "人用",
    "machine contract",
    "SQLite",
    "snapshot",
    "快照",
    "bucket",
    "分桶",
    "EventID",
    "revision",
    "manifest",
    "stale",
    "immutable support",
  ];
  for (const term of blocked) {
    assert.equal(html.toLowerCase().includes(term.toLowerCase()), false, `visible term: ${term}`);
  }
});

test("all referenced local web assets exist by contract", async () => {
  const html = await asset("index.html");
  assert.match(html, /href="\.\/tokens\.css"/);
  assert.match(html, /href="\.\/app\.css"/);
  assert.match(html, /src="\.\/app\.js"/);
  for (const name of ["icons/ms-peak.svg", "icons/folder.svg", "icons/file-wave.svg"]) {
    const contents = await asset(name);
    assert.match(contents, /<svg/);
  }
});
