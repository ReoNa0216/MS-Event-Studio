"""Compare a saved 0.1.0 real-project result with the installed 0.2.0 core.

Run only on a disposable project produced by validate_real_features.py under
this repository's build directory. Original projects and raw inputs are read-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import zipfile

import anndata as ad
import numpy as np
import pandas as pd

from ms_event_studio.analysis_export import export_analysis
from ms_event_studio.features import overview, read_result, run_extraction, sha256, snapshot
from ms_event_studio.project import open_project
from validate_real_features import tree


def check_archive(project, result_id, parent, name, handoff):
    folder, record = read_result(project, result_id, verify=True)
    result = export_analysis(project, parent, binding=snapshot(project)['binding'],
        result_id=result_id, include_pending=True, handoff=handoff, filename=name)
    archive = parent / result['display_name']
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for item in [*record['artifacts'], 'execution_record.json']:
            assert z.read('features/' + item) == (folder / item).read_bytes(), item
        if handoff:
            assert 'handoff_record.json' in z.namelist()
        else:
            assert 'analysis_record.json' in z.namelist()
    return str(archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    args = parser.parse_args()
    root = args.baseline.resolve(strict=True)
    build = Path(__file__).resolve().parents[1] / 'build'
    if not root.is_relative_to(build.resolve()):
        raise ValueError('Upgrade validation must run inside the disposable build directory')
    baseline = json.loads((root / 'ms-check.json').read_text('utf-8'))
    project = open_project(root / 'MS')
    saved = snapshot(project)
    old_id = baseline['result']['result_id']
    old_folder, old_record = read_result(project, old_id, verify=True)
    assert old_record['package_version'] == '0.1.0'
    assert sha256(old_folder / 'native_matrix.h5ad') == baseline['matrix_sha256']
    protected = tree(project.project_dir)
    old = ad.read_h5ad(old_folder / 'native_matrix.h5ad')
    # No raw is supplied to reopen/export: existing results stay usable offline.
    exports = {}
    for handoff in (False, True):
        kind = 'lma' if handoff else 'analysis'
        exports['old_' + kind] = check_archive(project, old_id, root, 'old-' + kind + '.zip', handoff)
    assert overview(open_project(project.project_dir))['results'][0]['current']
    start = time.monotonic()
    def progress(phase, fraction=0):
        if phase != 'reading' or fraction == 0:
            print(phase, fraction, flush=True)
    result = run_extraction(project, Path(baseline['raw']), saved, [], lambda: False, progress)
    new_folder, record = read_result(project, result['result_id'], verify=True)
    assert record['package_version'] == '0.2.0'
    assert record['method_id'] == 'B_owned_mz_center_v1'
    assert record['local_method_id'] == 'B_owned_v1'
    new = ad.read_h5ad(new_folder / 'native_matrix.h5ad')
    assert new.X.dtype == np.dtype('float64')
    np.testing.assert_array_equal(new.X, old.X, strict=True)
    pd.testing.assert_frame_equal(new.obs, old.obs, check_exact=True)
    pd.testing.assert_frame_equal(new.var, old.var, check_exact=True)
    for item in ('event_rows.parquet', 'feature_axis.parquet', 'quality_flags.parquet',
                 'runs/run_001/native_representatives.parquet'):
        pd.testing.assert_frame_equal(pd.read_parquet(new_folder / item), pd.read_parquet(old_folder / item), check_exact=True)
    assert new.uns['method'] == record['method_id'] and new.uns['package_version'] == '0.2.0'
    project = open_project(project.project_dir)
    assert snapshot(project)['binding'] == saved['binding']
    assert read_result(project, old_id, verify=True)[1] == old_record
    for handoff in (False, True):
        kind = 'lma' if handoff else 'analysis'
        exports['new_' + kind] = check_archive(project, result['result_id'], root, 'new-' + kind + '.zip', handoff)
    for name, digest in protected.items():
        assert sha256(project.project_dir / name) == digest, name
    assert tree(Path(baseline['source_project'])) == baseline['source_tree']
    report = dict(status='passed', package_version=record['package_version'], method_id=record['method_id'],
        local_method_id=record['local_method_id'], events=new.n_obs, features=new.n_vars,
        values_nan_obs_var_representatives_equal=True, protected_source_and_old_results_unchanged=True,
        old_result_id=old_id, new_result_id=result['result_id'], exports=exports,
        old_matrix_sha256=sha256(old_folder / 'native_matrix.h5ad'),
        new_matrix_sha256=sha256(new_folder / 'native_matrix.h5ad'), elapsed_seconds=time.monotonic()-start)
    (root / 'upgrade-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
