// Feature extraction shares the existing native path and background-job boundary.
export function installFeatures({ get, post, selectPath, openDialog, closeDialog, canOpen }) {
  const el = (id) => document.getElementById(id);
  let overview = null, source = null, job = null, busy = false, cancelAllowed = false;
  const error = (text = '') => { el('featureError').textContent = text; el('featureError').hidden = !text; };
  function controls() {
    el('featureSource').disabled = busy;
    el('featureQc').disabled = busy;
    el('featureStart').disabled = busy || !source || !overview?.accepted;
    el('featureResults').disabled = busy;
    el('featureExport').disabled = busy || !el('featureResults').value;
    el('featureCancel').hidden = !busy;
    el('featureCancel').disabled = !cancelAllowed;
    el('featureClose').disabled = busy;
    el('featureProgressRegion').hidden = !busy;
  }
  function summary() {
    const row = overview?.results.find((r) => r.result_id === el('featureResults').value);
    const qc = row?.qc_intervals.length ? row.qc_intervals.map(([a, b]) => `${a / 6e10}–${b / 6e10}`).join('、') + ' min' : '无';
    el('featureSummary').textContent = row
      ? `${row.events.toLocaleString()} 个事件 × ${row.features.toLocaleString()} 个 feature；缺失 ${row.missing_fraction === null ? '不适用' : (row.missing_fraction * 100).toFixed(1) + '%'}。${row.current ? '与当前审阅事件一致。' : '事件已变化，这是此前的提取结果。'} QC 排除：${qc}。`
      : '暂无提取结果。完成后自动保存在项目中，重新打开项目仍可查看和导出。';
  }
  async function refresh(selectedId) {
    overview = await get('/api/features');
    el('featureAccepted').textContent = `${overview.accepted.toLocaleString()} 个已保留事件（排除下方 QC 段后提取）`;
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
    el('featureSourceName').textContent = '尚未选择';
    el('featureQc').value = '';
    el('featureMessage').textContent = '';
    el('featureAccepted').textContent = '正在读取项目…';
    controls(); openDialog('feature');
    el('featureSource').focus({ preventScroll: true });
    el('featureDialog').querySelector('.modal__body').scrollTop = 0;
    try { await refresh(); } catch (e) { error(e.message); }
  }
  async function poll() {
    try {
      const response = await get(`/api/jobs/${encodeURIComponent(job)}`);
      const state = response.job;
      cancelAllowed = state.cancellable;
      if (['queued', 'running', 'cancelling'].includes(state.state)) {
        const phases = { reading: '正在读取并核验原始质心谱', extracting: '正在提取 feature，首次运行需编译计算代码', validating: '正在核验与保存矩阵' };
        const message = state.state === 'cancelling' ? '正在取消…' : phases[state.phase] || '正在处理…';
        if (el('featureProgressText').textContent !== message) el('featureProgressText').textContent = message;
        if (state.phase === 'reading') el('featureProgress').value = state.progress.fraction;
        else el('featureProgress').removeAttribute('value');
        controls(); window.setTimeout(poll, 800); return;
      }
      busy = false; job = null; cancelAllowed = false;
      if (state.state === 'succeeded') {
        el('featureMessage').textContent = state.result?.export?.message || 'Feature 提取完成，结果已保存在项目中。';
        try { await refresh(state.result?.feature?.result_id); }
        catch (e) { error(`操作已完成，但结果列表刷新失败。请关闭后重新打开：${e.message}`); }
      } else if (state.state === 'cancelled') {
        el('featureMessage').textContent = '已取消，原有结果保持不变。';
      } else { error(state.error?.message || '操作未完成。'); }
      controls(); el('featureClose').focus();
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
    busy = true; controls(); el('featureMessage').textContent = '';
    try { begin(await post('/api/features/extract', { source_token: source.selectionToken, binding: overview.binding, qc_intervals: intervals })); }
    catch (e) { busy = false; error(e.message); controls(); }
  });
  el('featureResults').addEventListener('change', () => { summary(); controls(); });
  el('featureExport').addEventListener('click', async () => {
    try {
      const target = await selectPath('feature_export_parent');
      if (!target) return;
      error(); busy = true; controls();
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
