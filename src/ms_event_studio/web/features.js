// Feature extraction shares the existing native path and background-job boundary.
export function installFeatures({ get, post, selectPath, openDialog, closeDialog, canOpen }) {
  const el = (id) => document.getElementById(id);
  let overview = null, source = null, job = null, busy = false, cancelAllowed = false, opening = 0, started = 0;
  const error = (text = '') => { el('featureError').textContent = text; el('featureError').hidden = !text; };
  function controls() {
    const hasResult = Boolean(overview?.results.length);
    const hasCurrent = Boolean(overview?.results[0]?.current);
    el('featureSource').disabled = busy;
    el('featureQc').disabled = busy;
    el('featureStart').disabled = busy || !overview?.accepted;
    el('featureStart').textContent = overview?.accepted && !source ? '定位原文件并提取…' : hasResult ? '重新提取' : '开始提取';
    el('featureCancel').hidden = !busy;
    el('featureCancel').disabled = !cancelAllowed;
    el('featureClose').disabled = busy;
    el('featureProgressRegion').hidden = !busy;
    el('featureFooterHint').textContent = busy ? (cancelAllowed ? '正在提取，可取消。' : '正在处理，请稍候。')
      : !source && overview?.accepted ? (hasCurrent ? '重新计算需要原始文件；当前有效矩阵仍可导出。' : '选择原始文件后开始提取，完成后自动保存。')
        : hasCurrent ? '当前有效矩阵可在「导出分析结果」中勾选导出。' : '提取完成后自动保存在项目中。';
  }
  function summary() {
    const row = overview?.results[0];
    el('featureEmpty').hidden = Boolean(row);
    el('featureEmpty').textContent = !overview ? '正在读取项目结果…' : !overview.accepted
      ? '尚无可提取事件，请先返回工作区保留事件。' : source
        ? '尚未提取。点击「开始提取」，完成后可导出矩阵。' : '尚未提取。先定位原始文件，再开始提取。';
    el('featureResultDetails').hidden = !row;
    if (!row) return;
    const qc = row.qc_intervals.length ? row.qc_intervals.map(([a, b]) => `${a / 6e10}–${b / 6e10}`).join('、') + ' min' : '无';
    el('featureEventCount').textContent = row.events.toLocaleString();
    el('featureFeatureCount').textContent = row.features.toLocaleString();
    el('featureMissing').textContent = row.missing_fraction === null ? '不适用' : (row.missing_fraction * 100).toFixed(1) + '%';
    el('featureSummary').textContent = row.current ? '对应当前审阅事件。' : '事件已更新。这是旧结果，建议重新提取。';
    el('featureSummary').classList.toggle('feature-result-status--stale', !row.current);
    el('featureSavedQc').textContent = `手动排除时间段：${qc}。`;
  }
  async function refresh(selectedId, generation = opening) {
    const response = await get('/api/features');
    if (generation !== opening || !el('featureDialog').open) return false;
    overview = response;
    if (!source) {
      const located = overview.source?.selection;
      source = located ? { selectionToken: located.selection_token, displayName: located.display_name, displayPath: located.display_path } : null;
      el('featureSourceName').textContent = source?.displayName || (overview.source?.name ? `待定位：${overview.source.name}` : '尚未定位原文件');
      el('featureSourcePath').textContent = source?.displayPath || overview.source?.display_path || '尚未记录本机路径';
      const help = { located: '已定位原始文件；提取时会核验文件内容。', unlocated: '本机尚未记录文件位置，请定位一次。',
        missing: '原始文件已移动或不可读取，请重新定位。', changed: '此位置的文件与项目记录不符，请重新定位原始文件。' };
      el('featureSourceHelp').textContent = help[overview.source?.status] || help.unlocated;
      el('featureSource').textContent = source ? '重新定位…' : '定位文件…';
    }
    el('featureAccepted').textContent = overview.accepted ? `${overview.accepted.toLocaleString()} 个已保留事件可用于提取。` : '请先在工作区保留需要提取的事件。';
    summary(); controls();
    if (overview.unavailable) error(`${overview.unavailable} 个历史结果无法读取；其余结果及新提取仍可使用。`);
    return true;
  }
  async function open() {
    if (!canOpen()) return;
    const generation = ++opening;
    overview = null; source = null; error();
    summary();
    el('featureSourceName').textContent = '正在定位…';
    el('featureSourcePath').textContent = '';
    el('featureSourceHelp').textContent = '正在定位原始文件…';
    el('featureQc').value = '';
    el('featureMessage').textContent = '';
    el('featureAccepted').textContent = '正在读取项目…';
    controls(); openDialog('feature');
    el('featureSource').focus({ preventScroll: true });
    el('featureDialog').querySelector('.modal__body').scrollTop = 0;
    try {
      await refresh(undefined, generation);
    } catch (e) {
      if (generation === opening && el('featureDialog').open) { error(e.message); el('featureEmpty').textContent = '读取失败，请关闭后重新打开。'; }
    }
  }
  async function poll(generation = opening) {
    try {
      const response = await get(`/api/jobs/${encodeURIComponent(job)}`);
      const state = response.job;
      cancelAllowed = state.cancellable;
      if (['queued', 'running', 'cancelling'].includes(state.state)) {
        const phases = { reading: '正在读取原始 MS 数据…', extracting: '正在提取 feature，首次运行可能稍久…', validating: '正在保存并检查矩阵…' };
        const message = state.state === 'cancelling' ? '正在取消…' : phases[state.phase] || '正在准备提取…';
        if (el('featureProgressText').textContent !== message) el('featureProgressText').textContent = message;
        el('featureElapsed').textContent = `已用 ${Math.floor((Date.now() - started) / 1000)} 秒`;
        controls(); window.setTimeout(poll, 800); return;
      }
      busy = false; job = null; cancelAllowed = false;
      if (state.state === 'succeeded') {
        el('featureMessage').textContent = '提取完成，结果已保存。';
        try {
          if (!await refresh(state.result?.feature?.result_id, generation)) return;
        } catch (e) {
          if (generation !== opening || !el('featureDialog').open) return;
          overview = null; summary();
          el('featureEmpty').textContent = '结果列表读取失败，请关闭后重新打开。';
          error(`操作已完成，但结果列表刷新失败。请关闭后重新打开：${e.message}`);
        }
      } else if (state.state === 'cancelled') {
        el('featureMessage').textContent = '已取消，原有结果保持不变。';
      } else { error(state.error?.message || '操作未完成。'); }
      controls();
      el('featureClose').focus();
    } catch (e) {
      // An uncertain poll is not proof the server job stopped. Keep polling and
      // preserve the cancel capability instead of enabling a duplicate run.
      error(`暂时无法读取进度：${e.message}`);
      if (job) window.setTimeout(poll, 2000);
      else controls();
    }
  }
  function begin(response) {
    started = Date.now();
    job = response.job.job_id; cancelAllowed = response.job.cancellable;
    controls();
    (cancelAllowed ? el('featureCancel') : el('featureProgressRegion')).focus();
    poll();
  }
  el('openFeatures').addEventListener('click', () => open());
  async function locateSource() {
    el('featureProgressText').textContent = '请选择原始 MS 文件…';
    el('featureElapsed').textContent = '';
    const selected = await selectPath('source_file');
    if (!selected) return false;
    source = selected; el('featureSourceName').textContent = selected.displayName;
    el('featureSourcePath').textContent = selected.displayPath || '';
    el('featureSourceHelp').textContent = '提取时会核验文件内容；成功后记住本机位置。';
    el('featureSource').textContent = '重新定位…'; error(); summary();
    return true;
  }
  el('featureSource').addEventListener('click', async () => {
    busy = true; controls();
    try {
      await locateSource();
    } catch (e) { error(e.message); }
    finally { busy = false; controls(); el('featureSource').focus(); }
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
    el('featureProgressText').textContent = '正在准备提取…';
    el('featureElapsed').textContent = '已用 0 秒';
    busy = true; controls(); el('featureMessage').textContent = '';
    try {
      if (!source && !await locateSource()) { busy = false; controls(); el('featureStart').focus(); return; }
      begin(await post('/api/features/extract', { source_token: source.selectionToken, binding: overview.binding, qc_intervals: intervals }));
    }
    catch (e) { busy = false; error(e.message); controls(); el('featureStart').focus(); }
  });
  el('featureCancel').addEventListener('click', async () => {
    if (!job || !cancelAllowed) return;
    try { await post(`/api/jobs/${encodeURIComponent(job)}/cancel`, {}); cancelAllowed = false; controls(); }
    catch (e) { error(e.message); }
  });
  function close() {
    if (busy) return;
    opening++;
    closeDialog('feature', { force: true });
    el('openFeatures').focus();
  }
  el('featureClose').addEventListener('click', close);
  el('featureDialog').addEventListener('cancel', (event) => { event.preventDefault(); close(); });
}
