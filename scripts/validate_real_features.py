"""Real-data HRGC regression in a new, explicitly disposable project copy.

Engineering selection is opt-in and recorded in the copied review database.
It is a load-test fixture, never a human annotation or accuracy reference.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import time

import anndata as ad
import numpy as np

from ms_event_studio.analysis_export import export_analysis
from ms_event_studio.features import read_result, run_extraction, sha256, snapshot
from ms_event_studio.project import (CreateProjectRequest, create_project,
                                    inspect_project_source, open_project)
from ms_event_studio.review import ReviewStore


def tree(root):
    return {p.relative_to(root).as_posix(): sha256(p) for p in root.rglob('*')
            if p.is_file() and not p.name.endswith(('-wal', '-shm', '-journal'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--engineering-selection', action='store_true')
    parser.add_argument('--project-name', default='MS')
    args = parser.parse_args()
    root = args.output.resolve()
    if args.source and (root == args.source.resolve() or args.source.resolve() in root.parents):
        raise ValueError('Test output must be outside the original project')
    root.mkdir(parents=True, exist_ok=True)
    if args.project_name in {'.', '..'} or Path(args.project_name).name != args.project_name:
        raise ValueError('project-name must be a folder name')
    target = root / args.project_name
    if target.exists():
        raise FileExistsError(target)
    source_before = tree(args.source) if args.source else None
    started = time.monotonic()
    if args.source:
        shutil.copytree(args.source, target)
        project = open_project(target)
    else:
        prepared = inspect_project_source(args.raw)
        project = create_project(CreateProjectRequest(args.raw, target,
            root.name + ' — 工程测试，非人工真值', str(prepared.start_ns / 60e9),
            str(prepared.end_ns / 60e9)), prepared_source=prepared)
    creation_seconds = time.monotonic() - started
    saved = snapshot(project)
    original_statuses = dict(Counter(r['status'] for r in saved['rows']))
    if args.engineering_selection:
        with ReviewStore.open(target / project.manifest['review']['path'],
                              project_id=project.manifest['project_id']) as store:
            updates = [(r['event_id'], r['revision']) for r in store.list_events()
                       if r.get('generation_state') != 'stale' and r['status'] == 'unreviewed']
            store.set_status_bulk(updates, 'accepted', actor='engineering-regression',
                session_id='real-feature-load-test',
                reason='工程规模测试：选择真实检出事件以覆盖完整计算负载；非人工审核、非标签真值。')
        saved = snapshot(project)
    review_before = sha256(target / project.manifest['review']['path'])
    last = [None, None]
    def progress(phase, fraction):
        current = [phase, int(fraction * 10)]
        if current != last:
            print(root.name, phase, current[1], flush=True)
            last[:] = current
    started = time.monotonic()
    result = run_extraction(project, args.raw, saved, [], lambda: False, progress)
    extraction_seconds = time.monotonic() - started
    folder, record = read_result(project, result['result_id'], verify=True)
    matrix = ad.read_h5ad(folder / 'native_matrix.h5ad')
    assert matrix.n_obs == sum(r['status'] == 'accepted' for r in saved['rows'])
    assert snapshot(project)['binding'] == saved['binding']
    assert sha256(target / project.manifest['review']['path']) == review_before
    started = time.monotonic()
    exported = export_analysis(project, root, binding=saved['binding'],
                               result_id=result['result_id'], include_pending=False, handoff=True)
    export_seconds = time.monotonic() - started
    assert sha256(target / project.manifest['review']['path']) == review_before
    assert snapshot(open_project(target))['binding'] == saved['binding']
    assert source_before is None or tree(args.source) == source_before
    assert sha256(args.raw) == saved['source']['sha256']
    values = matrix.X
    report = dict(source_project=str(args.source), raw=str(args.raw.resolve()),
        original_statuses=original_statuses, engineering_selection=args.engineering_selection,
        test_statuses=dict(Counter(r['status'] for r in saved['rows'])),
        source_files_unchanged=True, source_tree=source_before,
        review_unchanged_by_extraction_and_export=True, raw_sha256=saved['source']['sha256'],
        matrix_sha256=sha256(folder / 'native_matrix.h5ad'), result=result,
        matrix_path=str(folder / 'native_matrix.h5ad'), zip=str(root / exported['display_name']),
        events=matrix.n_obs, features=matrix.n_vars,
        all_nan_rows=int(np.isnan(values).all(axis=1).sum()),
        all_nan_columns=int(np.isnan(values).all(axis=0).sum()),
        zero_filled_constant_columns=int((np.ptp(np.nan_to_num(values), axis=0) == 0).sum()),
        timings=dict(project_copy_or_create=creation_seconds, extraction=extraction_seconds,
                     zip_export=export_seconds),
        code_commit=__import__('subprocess').check_output(
            ['git', '-C', str(Path(__file__).resolve().parents[1]), 'rev-parse', 'HEAD'], text=True).strip())
    (root / 'ms-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('events', 'features', 'timings', 'engineering_selection')}), flush=True)


if __name__ == '__main__':
    main()
