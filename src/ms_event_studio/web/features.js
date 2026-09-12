// Feature extraction shares the existing native path and background-job boundary.
export function installFeatures({ get, post, selectPath, openDialog, closeDialog, canOpen }) {
  const el = (id) => document.getElementById(id);
  let overview = null, source = null, job = null, busy = false, cancelAllowed = false, operation = 'extract';
  const error = (text = '') => { el('featureError').textContent = text; el('featureError').hidden = !text; };
  function controls() {
    const hasResult = Boolean(overview?.results.some((r) => r.result_id === el('featureResults').value));
    el('featureSource').disabled = busy;
    el('featureQc').disabled = busy;
    el('featureStart').disabled = busy || !source || !overview?.accepted;
    el('featureResults').disabled = busy;
    el('featureExport').disabled = busy || !hasResult;
    el('featureExport').classList.toggle('button--primary', hasResult);
    el('featureExport').classList.toggle('button--secondary', !hasResult);
    el('featureStart').classList.toggle('button--primary', !hasResult);
    el('featureStart').classList.toggle('button--secondary', hasResult);
    el('featureStart').textContent = hasResult ? '重新提取' : '开始提取';
    el('featureCancel').hidden = !busy || operation === 'export';
    el('featureCancel').disabled = !cancelAllowed;
    el('featureClose').disabled = busy;
    el('featureProgressRegion').hidden = !busy;
    el('featureFooterHint').textContent = busy ? (operation === 'export' ? '正在导出 ZIP，请稍候。' : '正在提取，可随时取消。')
      : hasResult ? '结果已随项目保存；导出当前选中结果。' : '结果随项目自动保存，重开后可继续导出。';
  }
  function summary() {
    const row = overview?.results.find((r) => r.result_id === el('featureResults').value);
    el('featureEmpty').hidden = Boolean(row);
    el('featureEmpty').textContent = !overview ? '正在读取项目结果…' : !overview.accepted
      ? '尚无可提取事件，请先返回工作区保留事件。' : '尚未提取。选择原始文件后点击「开始提取」。';
    el('featureResultDetails').hidden = !row;
    el('featureResultPicker').hidden = !overview?.results.length;
    if (!row) return;
    const qc = row.qc_intervals.length ? row.qc_intervals.map(([a, b]) => `${a / 6e10}–${b / 6e10}`).join('、') + ' min' : '无';
    el('featureEventCount').textContent = row.events.toLocaleString();
    el('featureFeatureCount').textContent = row.features.toLocaleString();
    el('featureMissing').textContent = row.missing_fraction === null ? '不适用' : (row.missing_fraction * 100).toFixed(1) + '%';
    el('featureSummary').textContent = row.current ? '对应当前审阅事件。' : '事件已更新。这是旧结果，建议重新提取。';
    el('featureSummary').classList.toggle('feature-result-status--stale', !row.current);
    el('featureSavedQc').textContent = `此结果排除的 QC：${qc}。`;
  }
  async function refresh(selectedId) {
    overview = await get('/api/features');
    el('featureAccepted').textContent = overview.accepted ? `${overview.accepted.toLocaleString()} 个已保留事件可用于提取。` : '请先在工作区保留需要提取的事件。';
    const select = el('featureResults');
    const previous = selectedId || select.value;
    select.replaceChildren();
    for (const [i, row] of overview.results.entries()) {
      const option = document.createElement('option');
      option.value = row.result_id;
      option.textContent = `${i === 0 ? '最近结果' : '此前结果 ' + i} · ${row.events} × ${row.features}${row.current ? '' : ' · 事件已变化'}`;
      select.append(option);
    }
    if (overview.results.some((r) => r.result_id === previous)) select.value = previous;
    summary(); controls();
    if (overview.unavailable) error(`${overview.unavailable} 个历史结果无法读取；其余结果及新提取仍可使用。`);
  }
  async function open() {
    if (!canOpen()) return;
    overview = null; source = null; error();
    el('featureResults').replaceChildren();
    summary();
    el('featureSourceName').textContent = '尚未选择';
    el('featureQc').value = '';
    el('featureMessage').textContent = '';
    el('featureAccepted').textContent = '正在读取项目…';
    controls(); openDialog('feature');
    el('featureSource').focus({ preventScroll: true });
    el('featureDialog').querySelector('.modal__body').scrollTop = 0;
    try { await refresh(); } catch (e) { error(e.message); el('featureEmpty').textContent = '读取失败，请关闭后重新打开。'; }
  }
  async function poll() {
    try {
      const response = await get(`/api/jobs/${encodeURIComponent(job)}`);
      const state = response.job;
      cancelAllowed = state.cancellable;
      if (['queued', 'running', 'cancelling'].includes(state.state)) {
        const phases = { reading: '正在读取原始 MS 数据…', extracting: '正在提取 feature，首次运行可能稍久…', validating: '正在保存并检查矩阵…' };
        const message = state.state === 'cancelling' ? '正在取消…' : operation === 'export' ? '正在导出 ZIP…' : phases[state.phase] || '正在准备提取…';
        if (el('featureProgressText').textContent !== message) el('featureProgressText').textContent = message;
        if (state.phase === 'reading') el('featureProgress').value = state.progress.fraction;
        else el('featureProgress').removeAttribute('value');
        controls(); window.setTimeout(poll, 800); return;
      }
      busy = false; job = null; cancelAllowed = false;
      let newResultReady = false;
      if (state.state === 'succeeded') {
        el('featureMessage').textContent = state.result?.export
          ? `已导出 ${state.result.export.display_name}。`
          : '提取完成。结果已保留，可点击「导出 ZIP…」单独保存。';
        try {
          await refresh(state.result?.feature?.result_id);
          newResultReady = operation === 'extract' && el('featureResults').value === state.result?.feature?.result_id;
        } catch (e) {
          overview = null; el('featureResults').replaceChildren(); summary();
          el('featureEmpty').textContent = '结果列表读取失败，请关闭后重新打开。';
          error(`操作已完成，但结果列表刷新失败。请关闭后重新打开：${e.message}`);
        }
      } else if (state.state === 'cancelled') {
        el('featureMessage').textContent = '已取消，原有结果保持不变。';
      } else { error(state.error?.message || '操作未完成。'); }
      controls();
      (newResultReady && !el('featureExport').disabled ? el('featureExport') : el('featureClose')).focus();
    } catch (e) {
      // An uncertain poll is not proof the server job stopped. Keep polling and
      // preserve the cancel capability instead of enabling a duplicate run.
      error(`暂时无法读取进度：${e.message}`);
      if (job) window.setTimeout(poll, 2000);
      else controls();
    }
  }
  function begin(response) {
    job = response.job.job_id; cancelAllowed = response.job.cancellable;
    controls();
    (cancelAllowed ? el('featureCancel') : el('featureProgressRegion')).focus();
    poll();
  }
  el('openFeatures').addEventListener('click', open);
  el('featureSource').addEventListener('click', async () => {
    try {
      const selected = await selectPath('source_file');
      if (selected) { source = selected; el('featureSourceName').textContent = selected.displayName; error(); }
    } catch (e) { error(e.message); }
    controls();
  });
  el('featureStart').addEventListener('click', async () => {
    error();
    const text = el('featureQc').value.trim();
    const intervals = [];
    for (const line of text ? text.split(/\n/) : []) {
      const match = line.trim().match(/^(\d+(?:\.\d+)?)\s*[-–,，]\s*(\d+(?:\.\d+)?)$/);
      if (!match || Number(match[2]) < Number(match[1])) { error('QC 每行填写一段起止分钟数，例如 0–2；无 QC 可留空。'); return; }
      intervals.push([match[1], match[2]]);
    }
    operation = 'extract'; busy = true; controls(); el('featureMessage').textContent = '';
    try { begin(await post('/api/features/extract', { source_token: source.selectionToken, binding: overview.binding, qc_intervals: intervals })); }
    catch (e) { busy = false; error(e.message); controls(); }
  });
  el('featureResults').addEventListener('change', () => { summary(); controls(); });
  el('featureExport').addEventListener('click', async () => {
    try {
      const target = await selectPath('feature_export_parent');
      if (!target) return;
      operation = 'export'; error(); busy = true; el('featureMessage').textContent = ''; controls();
      begin(await post('/api/features/export', { result_id: el('featureResults').value, target_token: target.selectionToken }));
    } catch (e) { busy = false; error(e.message); controls(); }
  });
  el('featureCancel').addEventListener('click', async () => {
    if (!job || !cancelAllowed) return;
    try { await post(`/api/jobs/${encodeURIComponent(job)}/cancel`, {}); cancelAllowed = false; controls(); }
    catch (e) { error(e.message); }
  });
  function close() {
    if (busy) return;
    closeDialog('feature', { force: true }); el('openFeatures').focus();
  }
  el('featureClose').addEventListener('click', close);
  el('featureDialog').addEventListener('cancel', (event) => { event.preventDefault(); close(); });
}
