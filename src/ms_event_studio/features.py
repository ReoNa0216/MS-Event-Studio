"""Single-run Studio adapter for the shared HRGC implementation.

The selected-scan reader adapts benchmark_io.build_raw_cache from the verified
research source (SHA256 5b72ffd864af55474b1e76f5894e8ad4e92124c65505293872f9fd9d93302c04).
The complete raw hash binds it to the Studio's already validated scan summary.
No calling, review writes, float32 conversion or numerical HRGC changes occur.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .canonical import content_sha256, json_value
from .errors import CancelledError
from .paths import resolve_project_path
from .project import open_project
from .timebase import minutes_to_ns


class FeatureError(ValueError):
    """Messages deliberately safe for the product UI."""


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def artifact(project, role):
    record = next(row for row in project.manifest['artifacts'] if row['role'] == role)
    return resolve_project_path(project.project_dir, record['path'])


def snapshot(project):
    """One review read; immutable project artifacts were validated by open_project."""
    import pandas as pd
    from .review import ReviewStore
    from flame_ms_core.export import _machine_row
    project = open_project(project.project_dir)
    source = json.loads(artifact(project, 'input_manifest').read_text('utf-8'))['source_fingerprint']
    protocol = json.loads(artifact(project, 'detector_protocol').read_text('utf-8'))
    automatic = pd.read_parquet(artifact(project, 'automatic_events')).to_dict('records')
    by_id = {str(row['auto_event_id']): row for row in automatic}
    with ReviewStore.open(resolve_project_path(project.project_dir, project.manifest['review']['path']),
                          project_id=project.manifest['project_id']) as store:
        reviews = store.list_events()
    interval = project.manifest['analysis_range']
    rows = []
    for review in reviews:
        if review.get('generation_state') == 'stale':
            continue
        if not interval['start_ns'] <= review['current_apex_time_ns'] <= interval['end_ns']:
            continue
        auto = by_id.get(str(review.get('auto_event_id') or review.get('original_auto_event_id')))
        rows.append(_machine_row(review, auto, source_sha256=source['sha256'],
                                 detector_version=protocol['detector_version'],
                                 parameter_hash=protocol['parameter_hash']))
    rows.sort(key=lambda row: (row['current_apex_time_ns'], row['event_id']))
    binding = content_sha256({'manifest': sha256(project.project_dir / 'ms_event_project.json'), 'events': rows})
    return json_value(dict(binding=binding, reviews=reviews, automatic=automatic, source=source,
                           protocol=protocol, interval=interval, rows=rows,
                           project_id=project.manifest['project_id'], settings=project.manifest['scientific_settings']))


def qc_intervals(value):
    if not isinstance(value, list) or len(value) > 100:
        raise FeatureError('QC 时间段格式无效。')
    result = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(x, str) for x in pair):
            raise FeatureError('QC 时间段请填写起止分钟数。')
        try:
            start, end = map(minutes_to_ns, pair)
        except (ValueError, ArithmeticError) as exc:
            raise FeatureError('QC 时间段请填写有效的分钟数。') from exc
        if start < 0 or end < start:
            raise FeatureError('QC 时间段终点不能早于起点。')
        result.append([int(start), int(end)])
    return result


def selected_events(package, scans, intervals):
    import numpy as np
    events = package.events.copy()
    accepted = events.status.eq('accepted')
    excluded = np.zeros(len(events), dtype=bool)
    for start, end in intervals:
        excluded |= events.current_apex_time_ns.between(start, end).to_numpy()
    reasons = np.where(~accepted, 'not_accepted', np.where(excluded, 'explicit_qc_interval', 'included'))
    policy = events[['event_id', 'status', 'current_apex_time_ns']].copy()
    policy['event_version'] = policy.event_id.map(package.event_versions)
    policy['inclusion_reason'] = reasons
    # Verify both immutable and reviewed peak identities against the raw-bound table.
    for row in events.to_dict('records'):
        pairs = [('current_scan_row_index', 'current_scan_id', 'current_spectrum_index', 'current_apex_time_ns', 'current_apex_intensity')]
        if row['origin'] != 'manual_added':
            pairs.append(('scan_row_index', 'scan_id', 'spectrum_index', 'scan_time_ns', 'apex_intensity'))
        for ordinal, scan_id, index, time, intensity in pairs:
            n = int(row[ordinal])
            if n < 0 or n >= len(scans):
                raise FeatureError('事件峰顶超出原始扫描范围。')
            raw = scans.iloc[n]
            if (str(raw.scan_id) != str(row[scan_id]) or int(raw.spectrum_index) != int(row[index])
                    or int(raw.scan_time_ns) != int(row[time])
                    or float(raw.primary_marker_max_intensity) != float(row[intensity])):
                raise FeatureError('事件峰顶与原始数据不一致，请重新检查项目。')
    events = events.loc[accepted & ~excluded].copy()
    if events.empty:
        raise FeatureError('没有可提取事件，请先保留细胞事件并检查 QC 时间段。')
    events['event_version'] = events.event_id.map(package.event_versions)
    events['apex_scan_ordinal'] = events.current_scan_row_index.astype('int64')
    events['apex_scan_id'] = events.current_scan_id.astype('int64')
    # Original fields (including manual nulls) remain losslessly in source_events.
    keep = ['event_id', 'event_version', 'apex_scan_ordinal', 'apex_scan_id', 'source_sha256',
            'generation_id', 'revision', 'origin', 'status', 'current_spectrum_index',
            'current_apex_time_ns', 'current_apex_intensity']
    return events[keep].reset_index(drop=True), policy


def read_centroids(source, scans, events, source_identity, progress=lambda *_: None):
    """Read only needed full spectra, but hash all bytes and retain physical rows."""
    import numpy as np
    from flame_feature_core import RunInput
    ordinals = np.unique(events.apex_scan_ordinal.to_numpy()[:, None] + [-1, 0, 1])
    if ordinals[0] < 0 or ordinals[-1] >= len(scans):
        raise FeatureError('事件峰顶缺少前后相邻扫描，无法按当前 HRGC 规则提取。')
    chosen = scans.iloc[ordinals]
    lengths = chosen.array_length.to_numpy(np.int64)
    offsets = np.r_[0, np.cumsum(lengths)]
    mz = np.empty(int(offsets[-1]), dtype=np.float64)
    intensity = np.empty_like(mz)
    lookup = {int(n): i for i, n in enumerate(ordinals)}
    seen = set()
    scan_re = re.compile(rb'id:\s*scanId=(\d+)')
    index_re = re.compile(rb'index:\s*(\d+)')
    time_re = re.compile(rb'scan start time,\s*([0-9.eE+\-]+),\s*minute')
    array_re = re.compile(rb'binary:\s*\[(\d+)\]\s*(.*)')
    digest = hashlib.sha256()
    source = Path(source)
    before = source.stat()
    if before.st_size != source_identity['size_bytes']:
        raise FeatureError('所选 MS 文件与项目来源不一致。')
    ordinal, current_id, current_index, current_time, mode = -1, None, None, None, None
    read, last_progress = 0, 0
    with source.open('rb', buffering=8*1024*1024) as stream:
        for line in stream:
            digest.update(line)
            read += len(line)
            if read - last_progress >= 8*1024*1024:
                progress('reading', read / max(1, before.st_size))
                last_progress = read
            stripped = line.strip()
            if stripped == b'spectrum:':
                ordinal += 1
                current_id = current_index = current_time = mode = None
            if ordinal not in lookup:
                continue
            if stripped.startswith(b'binary:'):
                match = array_re.fullmatch(stripped)
                i = lookup[ordinal]
                if match is None or mode not in ('mz', 'intensity') or (ordinal, mode) in seen:
                    raise FeatureError('原始质心数组格式无效。')
                values = np.fromstring(match[2].decode('ascii'), sep=' ', dtype=np.float64)
                if len(values) != lengths[i] or int(match[1]) != lengths[i]:
                    raise FeatureError('原始质心数组长度与项目不一致。')
                expected = scans.iloc[ordinal]
                if (current_id != int(expected.scan_id) or current_index != int(expected.spectrum_index)
                        or current_time != int(expected.scan_time_ns)):
                    raise FeatureError('原始谱的物理扫描身份与项目不一致。')
                lo, hi = offsets[i:i+2]
                (mz if mode == 'mz' else intensity)[lo:hi] = values
                seen.add((ordinal, mode))
                mode = None
            elif match := scan_re.search(stripped):
                current_id = int(match[1])
            elif match := index_re.fullmatch(stripped):
                current_index = int(match[1])
            elif match := time_re.search(stripped):
                current_time = minutes_to_ns(match[1].decode('ascii'))
            elif b'm/z array' in stripped:
                mode = 'mz'
            elif b'intensity array' in stripped:
                mode = 'intensity'
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise FeatureError('读取期间 MS 文件发生变化。')
    if digest.hexdigest() != source_identity['sha256']:
        raise FeatureError('所选 MS 文件与项目来源不一致。')
    if ordinal + 1 != len(scans) or len(seen) != 2 * len(ordinals):
        raise FeatureError('原始扫描不完整，无法提取。')
    return RunInput(run_id=source_identity['sha256'], source_sha256=source_identity['sha256'],
                    scan_ordinal=ordinals, scan_id=chosen.scan_id.to_numpy(np.int64),
                    time_seconds=chosen.scan_time_ns.to_numpy(np.int64) / 1e9,
                    offsets=offsets, mz=mz, intensity=intensity, events=events)


def _worker(project_path, source_path, saved, intervals, staging, connection):
    """Spawned process can be terminated during Numba work without touching review."""
    try:
        import numpy as np
        import pandas as pd
        import anndata
        from flame_feature_core import extract_dataset
        from flame_ms_core.export import export_machine_contract
        from flame_ms_core.exchange import read_event_package
        def progress(phase, fraction=0):
            connection.send(('progress', phase, fraction))
        project = open_project(project_path)
        if snapshot(project)['binding'] != saved['binding']:
            raise FeatureError('事件已变化，请重新打开提取窗口。')
        staging = Path(staging)
        package_path = staging / 'source_events'
        export_machine_contract(saved['reviews'], saved['automatic'], package_path,
            source_fingerprint=saved['source'], detector_version=saved['protocol']['detector_version'],
            parameter_hash=saved['protocol']['parameter_hash'], generation_id=saved['protocol']['generation_id'],
            analysis_start_ns=saved['interval']['start_ns'], analysis_end_ns=saved['interval']['end_ns'],
            scientific_settings=saved['settings'])
        package = read_event_package(package_path)
        if package.manifest['schema'] != 'ms-event-machine-contract-v2':
            raise FeatureError('Feature 提取需要正式审阅事件。')
        scans = pd.read_parquet(artifact(project, 'scan_summary'))
        events, policy = selected_events(package, scans, intervals)
        progress('reading')
        raw = read_centroids(source_path, scans, events, saved['source'], progress)
        progress('extracting')
        output = staging / 'result'
        record = extract_dataset([raw], dataset_id=saved['project_id'], output=output)
        progress('validating')
        policy.to_parquet(output / 'event_inclusion.parquet', index=False)
        os.rename(package_path, output / 'source_events')
        data = anndata.read_h5ad(output / 'native_matrix.h5ad')
        rows = pd.read_parquet(output / 'event_rows.parquet')
        if (data.X.dtype != np.dtype('float64') or list(data.obs.event_id) != list(events.event_id)
                or list(data.obs.event_version) != list(events.event_version)
                or list(rows.event_id) != list(events.event_id)
                or list(rows.event_version) != list(events.event_version)
                or data.n_obs != len(events)):
            raise FeatureError('结果事件身份检查失败，未保存矩阵。')
        record.update(interface='ms-event-studio-feature-v1', project_id=saved['project_id'],
                      event_binding=saved['binding'], source_event_manifest_sha256=package.manifest_sha256,
                      qc_intervals_ns=intervals, inclusion_policy='accepted excluding explicit QC intervals; Unknown retained',
                      missing_fraction=float(np.isnan(data.X).sum()/data.X.size) if data.X.size else None,
                      adapter_version='ms-studio-feature-adapter-v1',
                      adapter_sha256=sha256(__file__) if Path(__file__).is_file() else None)
        record['artifacts'] = {p.relative_to(output).as_posix(): {'sha256': sha256(p), 'bytes': p.stat().st_size}
                               for p in sorted(output.rglob('*')) if p.is_file() and p.name != 'execution_record.json'}
        (output / 'execution_record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        connection.send(('complete', record))
    except BaseException as exc:
        message = str(exc) if isinstance(exc, FeatureError) else 'Feature 提取失败，请查看诊断日志；原有结果未改变。'
        import traceback
        connection.send(('error', message, traceback.format_exc()))
    finally:
        connection.close()


def result_root(project):
    return resolve_project_path(project.project_dir, 'features')


def run_extraction(project, source, saved, intervals, cancelled, progress):
    import logging
    # Work outside the project so sharing can never pick up a partial matrix.
    with tempfile.TemporaryDirectory(prefix='ms-feature-') as staging:
        ctx = multiprocessing.get_context('spawn')
        parent, child = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_worker, args=(str(project.project_dir), str(source), saved, intervals, staging, child))
        process.start()
        child.close()
        record = None
        try:
            while True:
                if cancelled():
                    raise CancelledError('Feature extraction cancelled')
                if parent.poll(.2):
                    try:
                        message = parent.recv()
                    except EOFError:
                        break
                    if message[0] == 'progress':
                        progress(message[1], message[2])
                    elif message[0] == 'error':
                        logging.getLogger(__name__).error('%s', message[2])
                        raise FeatureError(message[1])
                    else:
                        record = message[1]
                        break
                elif not process.is_alive():
                    break
            if record is None:
                raise FeatureError('计算进程意外结束，原有结果未改变。')
            if cancelled():
                raise CancelledError('Feature extraction cancelled')
            if snapshot(project)['binding'] != saved['binding']:
                raise FeatureError('提取期间事件发生变化，请重新提取。')
            root = result_root(project)
            root.mkdir(exist_ok=True)
            identity = uuid.uuid4().hex
            # Same-volume staging permits a final atomic rename even if OS temp
            # and the project are on different drives.
            with tempfile.TemporaryDirectory(prefix='.feature-publish-', dir=project.project_dir.parent) as publish:
                temp = Path(publish) / 'result'
                shutil.copytree(Path(staging) / 'result', temp)
                if cancelled():
                    raise CancelledError('Feature extraction cancelled')
                if snapshot(project)['binding'] != saved['binding']:
                    raise FeatureError('提取期间事件发生变化，请重新提取。')
                os.rename(temp, root / identity)
            return result_view(identity, record, saved['binding'])
        finally:
            if process.is_alive():
                process.terminate()
            process.join()
            parent.close()


def result_view(identity, record, binding):
    return dict(result_id=identity, events=record['events'], features=record['features'],
                missing_fraction=record['missing_fraction'], current=record['event_binding'] == binding,
                qc_intervals=record['qc_intervals_ns'], method='HRGC', elapsed_seconds=record['elapsed_seconds'])


def read_result(project, identity, verify=False):
    if not isinstance(identity, str) or re.fullmatch('[0-9a-f]{32}', identity) is None:
        raise FeatureError('提取结果不存在。')
    folder = resolve_project_path(project.project_dir, f'features/{identity}')
    record = json.loads((folder / 'execution_record.json').read_text('utf-8'))
    if (not isinstance(record, dict) or record.get('interface') != 'ms-event-studio-feature-v1' or record.get('complete') is not True
            or record.get('status') != 'passed' or record.get('project_id') != project.manifest['project_id']):
        raise FeatureError('提取结果不完整或不属于当前项目。')
    if verify:
        for name, info in record['artifacts'].items():
            path = resolve_project_path(folder, name)
            if path.stat().st_size != info['bytes'] or sha256(path) != info['sha256']:
                raise FeatureError('提取结果校验失败，文件可能已被改动。')
    return folder, record


def overview(project):
    saved = snapshot(project)
    results = []
    unavailable = 0
    root = result_root(project)
    if root.exists():
        for folder in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime_ns, reverse=True):
            try:
                _, record = read_result(project, folder.name)
                results.append(result_view(folder.name, record, saved['binding']))
            except (OSError, ValueError, KeyError, TypeError):
                import logging
                logging.getLogger(__name__).warning('Unreadable feature result %s', folder.name, exc_info=True)
                unavailable += 1
    return dict(binding=saved['binding'], accepted=sum(r['status'] == 'accepted' for r in saved['rows']),
                results=results, unavailable=unavailable)


def export_result(project, identity, parent):
    import zipfile
    folder, record = read_result(project, identity, verify=True)
    parent = Path(parent).resolve(strict=True)
    if parent == project.project_dir or project.project_dir in parent.parents:
        raise FeatureError('请将 feature 结果导出到项目文件夹以外。')
    name = f'features-{identity[:12]}.zip'
    destination = parent / name
    with tempfile.TemporaryDirectory(prefix='.feature-export-', dir=parent) as work:
        temp = Path(work) / name
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for item in ['execution_record.json', *record['artifacts']]:
                archive.write(resolve_project_path(folder, item), item)
        read_result(project, identity, verify=True)
        # hard-link publication is atomic and refuses any existing target.
        os.link(temp, destination)
    return dict(display_name=name, message='Feature 矩阵及事件溯源已导出。')
