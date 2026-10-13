"""Compare installed core releases on a disposable real single-run project.

Create the baseline with validate_real_features.py and the official 0.2.0 wheel.
Every Parquet column, local NPY, matrix value/NaN and physical identity is exact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

import anndata as ad
import numpy as np
import pandas as pd

from ms_event_studio.analysis_export import export_analysis
from ms_event_studio.features import overview, read_result, run_extraction, sha256, snapshot
from ms_event_studio.project import open_project
from validate_real_features import tree, measure_extraction


def check_archive(project, result_id, parent, name, handoff):
    folder, record = read_result(project, result_id, verify=True)
    result = export_analysis(project, parent, binding=snapshot(project)['binding'],
        result_id=result_id, include_pending=True, handoff=handoff, filename=name)
    archive = parent / result['display_name']
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for item in [*record['artifacts'], 'execution_record.json']:
            assert z.read('features/' + item) == (folder / item).read_bytes(), item
        assert ('handoff_record.json' if handoff else 'analysis_record.json') in z.namelist()
    return dict(path=str(archive), sha256=sha256(archive))


def equal_metadata(left, right, path='uns'):
    if isinstance(left, dict):
        assert left.keys() == right.keys(), path
        for key in left:
            equal_metadata(left[key], right[key], path + '/' + key)
    elif isinstance(left, list):
        assert len(left) == len(right), path
        for index, (a, b) in enumerate(zip(left, right)):
            equal_metadata(a, b, path + '/' + str(index))
    else:
        np.testing.assert_array_equal(left, right, err_msg=path, strict=True)


def scientific_runs(value):
    if isinstance(value, dict):
        return {key:scientific_runs(item) for key,item in value.items()
                if key not in ('preprocessing_seconds', 'elapsed_seconds_including_provenance', 'elapsed_seconds')}
    if isinstance(value, list):
        return [scientific_runs(item) for item in value]
    return value


def compare_results(old_folder, new_folder):
    old = ad.read_h5ad(old_folder / 'native_matrix.h5ad')
    new = ad.read_h5ad(new_folder / 'native_matrix.h5ad')
    assert new.X.dtype == np.dtype('float64')
    np.testing.assert_array_equal(new.X, old.X, strict=True)
    np.testing.assert_array_equal(np.isnan(new.X), np.isnan(old.X), strict=True)
    pd.testing.assert_frame_equal(new.obs, old.obs, check_exact=True)
    pd.testing.assert_frame_equal(new.var, old.var, check_exact=True)
    equal_metadata({k:v for k,v in old.uns.items() if k != 'package_version'},
                   {k:v for k,v in new.uns.items() if k != 'package_version'})
    parquet_names = {p.relative_to(old_folder).as_posix() for p in old_folder.rglob('*.parquet')}
    assert parquet_names == {p.relative_to(new_folder).as_posix() for p in new_folder.rglob('*.parquet')}
    tables = {}
    for name in sorted(parquet_names):
        left, right = pd.read_parquet(old_folder/name), pd.read_parquet(new_folder/name)
        pd.testing.assert_frame_equal(left, right, check_exact=True)
        tables[name] = dict(rows=len(right), columns=list(right.columns), sha256=sha256(new_folder/name))
    arrays = {p.relative_to(old_folder).as_posix() for p in old_folder.rglob('*.npy')}
    assert arrays == {p.relative_to(new_folder).as_posix() for p in new_folder.rglob('*.npy')}
    for name in arrays:
        np.testing.assert_array_equal(np.load(old_folder/name), np.load(new_folder/name), strict=True)
    return new, dict(parquet=tables, npy=sorted(arrays),
        exact_matrix_nan_axes_uns_and_all_provenance=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--candidate-version', default='0.2.1')
    parser.add_argument('--repetitions', type=int, default=2)
    parser.add_argument('--report-name', default='upgrade-check.json')
    args = parser.parse_args()
    root = args.baseline.resolve(strict=True)
    build = Path(__file__).resolve().parents[1] / 'build'
    if not root.is_relative_to(build.resolve()):
        raise ValueError('Upgrade validation must run inside the disposable build directory')
    if Path(args.report_name).name != args.report_name or args.repetitions < 1:
        raise ValueError('A report filename and positive repetitions are required')
    baseline = json.loads((root / 'ms-check.json').read_text('utf-8'))
    project = open_project(root / 'MS')
    saved = snapshot(project)
    old_id = baseline['result']['result_id']
    old_folder, old_record = read_result(project, old_id, verify=True)
    assert old_record['package_version'] == '0.2.0'
    assert sha256(old_folder / 'native_matrix.h5ad') == baseline['matrix_sha256']
    protected = tree(project.project_dir)
    exports = {}
    prefix = Path(args.report_name).stem + '-' + args.candidate_version.replace('.', '-')
    # Reopen/export saved 0.2.0 without supplying raw to either operation.
    for handoff in (False, True):
        kind = 'lma' if handoff else 'analysis'
        exports['old_' + kind] = check_archive(project, old_id, root, prefix+'-old-'+kind+'.zip', handoff)
    assert next(r for r in overview(open_project(project.project_dir))['results'] if r['result_id']==old_id)['current']
    measurements = []
    completed = []
    for repetition in range(args.repetitions):
        def progress(phase, fraction=0):
            if phase != 'reading' or fraction == 0:
                print(args.candidate_version, repetition+1, phase, fraction, flush=True)
        result, resources = measure_extraction(lambda: run_extraction(
            project, Path(baseline['raw']), saved, [], lambda: False, progress))
        new_folder, record = read_result(project, result['result_id'], verify=True)
        assert record['package_version'] == args.candidate_version
        assert record['method_id'] == 'B_owned_mz_center_v1' and record['local_method_id'] == 'B_owned_v1'
        assert record['status'] == 'passed' and record['complete'] is True
        equal_metadata(scientific_runs(old_record['runs']), scientific_runs(record['runs']), path='runs')
        for key in ['method_id','local_method_id','dataset_id','parameters','inputs','events','features',
                    'merge','python_version','platform','dependencies','event_binding','qc_intervals_ns',
                    'inclusion_policy','adapter_version','adapter_sha256']:
            equal_metadata(old_record[key],record[key],path=key)
        measurements.append(dict(repetition=repetition+1, result_id=result['result_id'], resources=resources,
            core_api_seconds=record['elapsed_seconds'], matrix_sha256=sha256(new_folder/'native_matrix.h5ad'),
            ))
        completed.append(new_folder)
        print(json.dumps(measurements[-1]),flush=True)
    # Large comparison DataFrames must not inflate the validator RSS of the
    # next timed extraction. Each extraction still uses a fresh spawned worker.
    for measurement, folder in zip(measurements, completed):
        new, parity = compare_results(old_folder, folder)
        assert new.uns['package_version'] == args.candidate_version
        measurement['parity'] = parity
    project = open_project(project.project_dir)
    assert snapshot(project)['binding'] == saved['binding']
    assert read_result(project, old_id, verify=True)[1] == old_record
    for handoff in (False, True):
        kind = 'lma' if handoff else 'analysis'
        exports['new_' + kind] = check_archive(project, result['result_id'], root, prefix+'-new-'+kind+'.zip', handoff)
    for name, digest in protected.items():
        assert sha256(project.project_dir / name) == digest, name
    assert tree(Path(baseline['source_project'])) == baseline['source_tree']
    report = dict(status='passed', baseline_version=old_record['package_version'],
        package_version=record['package_version'], method_id=record['method_id'], local_method_id=record['local_method_id'],
        events=new.n_obs, features=new.n_vars, preserved_original_and_old_results=True,
        measurements=measurements, exports=exports,
        old_result_id=old_id, new_result_id=result['result_id'],
        old_matrix_sha256=sha256(old_folder / 'native_matrix.h5ad'),
        new_matrix_sha256=sha256(new_folder / 'native_matrix.h5ad'),
        resource_scope='Studio run_extraction: raw read/hash, spawn/import/JIT, full API, validation and atomic publication. '
            '20 ms sum of validator+descendant RSS; worker peak_wset is Windows high-water working set. '
            'Comparator and ZIP exports outside measurement. Separate version cache directories; first run compiles, later run reuses cache.')
    (root / args.report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['measurements','exports']}),flush=True)


if __name__ == '__main__':
    main()
