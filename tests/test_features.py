from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.features import (FeatureError, snapshot, read_centroids, qc_intervals,
    run_extraction, read_result, overview, export_result, selected_events, sha256)
from ms_event_studio.errors import CancelledError
from ms_event_studio.project import CreateProjectRequest, create_project
from ms_event_studio.window_service import ProjectWindowService
from test_web_review_api_contract import open_session, create_adjustable_project
from test_web_range_export_api_contract import wait_job


def feature_project(root):
    signal = np.full(1001, 10.0)
    for n in range(100, 900, 40):
        signal[n-1:n+2] = [400., 2000., 500.]
    source = write_ms_file(root / 'source.txt', [spectrum_lines(
        n, 100 + n, f'{n/600:.12f}', mz_values=[500.123456789123, 760.5851, 782.5616, 900.],
        intensities=[555.123456789123, float(signal[n]), 900., 1000.]) for n in range(len(signal))])
    project = create_project(CreateProjectRequest(source, root / 'project', 'Feature 测试', '0', '1.6'))
    with ProjectWindowService.open(project.project_dir) as service:
        if not service.all_events():
            for n in range(100, 900, 40):
                service.review_store.add_event(click_time_sec=n/10, scans=service.scans,
                    analysis_start_ns=service.analysis_start_ns, analysis_end_ns=service.analysis_end_ns,
                    actor='test', session_id='test', reason='explicit manual fixture')
        for row in service.all_events():
            service.review_store.set_status(row['event_id'], 'accepted', expected_revision=row['revision'],
                actor='test', session_id='test', reason='fixture')
    return source, project


class FeatureTests(unittest.TestCase):
    def test_qc_intervals_are_explicit_closed_nanoseconds(self):
        self.assertEqual(qc_intervals([['0', '2.5']]), [[0, 150_000_000_000]])
        for value in [[[2, 3]], [['3', '2']], [['NaN','2']], [['-1','2']], {}]:
            with self.subTest(value=value), self.assertRaises(FeatureError): qc_intervals(value)

    def test_raw_reader_preserves_empty_physical_spectra_and_precision(self):
        from ms_event_studio.parser import parse_ms_scan_summary
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = ['spectrum:', 'index: 1', 'id: scanId=11', 'defaultArrayLength: 0',
                     'cvParam: base peak m/z, 0', 'cvParam: base peak intensity, 0', 'cvParam: total ion current, 0',
                     'cvParam: scan start time, 0.1, minute', 'cvParam: m/z array, m/z',
                     'binary: [0]', 'cvParam: intensity array', 'binary: [0]']
            source = write_ms_file(root / 'raw.txt', [
                spectrum_lines(0,10,'0', intensities=[1,2345.12345678912,2,3]), empty,
                spectrum_lines(2,12,'0.2', intensities=[5,1234.98765432198,3,4])])
            parsed = parse_ms_scan_summary(source)
            events = pd.DataFrame({'event_id':['one'], 'apex_scan_ordinal':[1]})
            raw = read_centroids(source, parsed.scans, events, {'sha256':sha256(source),'size_bytes':source.stat().st_size})
            np.testing.assert_array_equal(raw.scan_ordinal, [0,1,2])
            np.testing.assert_array_equal(raw.offsets, [0,4,4,8])
            self.assertEqual(raw.intensity.dtype, np.dtype('float64'))
            self.assertEqual(raw.intensity[1], 2345.12345678912)
            with self.assertRaises(FeatureError):
                read_centroids(source, parsed.scans, events, {'sha256':'0'*64,'size_bytes':source.stat().st_size})
            events.apex_scan_ordinal = 0
            with self.assertRaises(FeatureError):
                read_centroids(source, parsed.scans, events, {'sha256':sha256(source),'size_bytes':source.stat().st_size})

    def test_reviewed_peak_and_full_event_versions_survive_adapter(self):
        from flame_ms_core.exchange import EventPackage
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _, project = create_adjustable_project(root)
            saved = snapshot(project)
            frame = pd.DataFrame(saved['rows'])
            package = EventPackage({}, frame, '', {r['event_id']:'version-'+r['event_id'] for r in saved['rows']})
            with ProjectWindowService.open(project.project_dir) as service:
                selected, policy = selected_events(package, service.scans, [])
                self.assertEqual(selected.apex_scan_ordinal.tolist(), [310])
                self.assertTrue(selected.event_version.iloc[0].startswith('version-'))
                with self.assertRaises(FeatureError): selected_events(package, service.scans, [[0,120_000_000_000]])
                frame.loc[frame.status.eq('accepted'),'current_scan_id'] = '99999'
                with self.assertRaises(FeatureError): selected_events(package, service.scans, [])

    def test_real_worker_save_reopen_export_and_stale_review(self):
        import anndata
        from flame_feature_core import extract_dataset
        from flame_ms_core.exchange import read_event_package
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = feature_project(root)
            saved = snapshot(project)
            source_hash = sha256(source)
            manifest_hash = sha256(project.project_dir / 'ms_event_project.json')
            with ProjectWindowService.open(project.project_dir) as service:
                audit = service.review_store.audit_events()
            result = run_extraction(project, source, saved, [], lambda: False, lambda *_: None)
            folder, record = read_result(project, result['result_id'], verify=True)
            self.assertEqual(result['events'],20)
            self.assertGreater(result['features'],0)
            self.assertEqual(source_hash, sha256(source))
            self.assertEqual(manifest_hash, sha256(project.project_dir / 'ms_event_project.json'))
            self.assertEqual(saved['binding'], snapshot(project)['binding'])
            self.assertTrue(overview(project)['results'][0]['current'])
            self.assertEqual(record['package_version'],'0.1.0')
            self.assertEqual(read_event_package(folder / 'source_events').event_versions,
                {r.event_id:r.event_version for r in pd.read_parquet(folder / 'event_inclusion.parquet').itertuples()})
            # Same raw/roster through a direct package call must reproduce X.
            package = read_event_package(folder / 'source_events')
            with ProjectWindowService.open(project.project_dir) as service:
                self.assertEqual(audit, service.review_store.audit_events())
                events, _ = selected_events(package, service.scans, [])
                raw = read_centroids(source, service.scans, events, saved['source'])
            direct = root / 'direct'
            extract_dataset([raw], dataset_id=saved['project_id'], output=direct)
            np.testing.assert_array_equal(anndata.read_h5ad(folder/'native_matrix.h5ad').X,
                                          anndata.read_h5ad(direct/'native_matrix.h5ad').X)
            exported = export_result(project, result['result_id'], root)
            self.assertTrue((root/exported['display_name']).is_file())
            with self.assertRaises(FileExistsError): export_result(project,result['result_id'],root)
            with ProjectWindowService.open(project.project_dir) as service:
                row = service.all_events()[0]
                service.review_store.set_status(row['event_id'],'pending',expected_revision=row['revision'],actor='test',session_id='test',reason='changed')
            self.assertFalse(overview(project)['results'][0]['current'])
            (folder/'feature_axis.parquet').write_bytes(b'broken')
            with self.assertRaises(FeatureError): read_result(project,result['result_id'],verify=True)

    def test_cancellation_and_wrong_source_never_publish(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = feature_project(root)
            saved = snapshot(project)
            with self.assertRaises(CancelledError): run_extraction(project,source,saved,[],lambda:True,lambda *_:None)
            self.assertFalse((project.project_dir/'features').exists())
            source.write_bytes(b'wrong source')
            with self.assertRaises(FeatureError): run_extraction(project,source,saved,[],lambda:False,lambda *_:None)
            self.assertFalse((project.project_dir/'features').exists())

    def test_web_job_saves_and_blocks_mutations_until_complete(self):
        import threading
        from ms_event_studio.web_app import WebBoundaryError
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root=Path(tmp)
            source,project=feature_project(root)
            session=open_session(root,project)
            entered,release=threading.Event(),threading.Event()
            def blocked(*args):
                entered.set(); release.wait(5); return {'events':20}
            try:
                payload={'source_token':session.register_path('source_file',source)['selection_token'],
                         'binding':session.feature_overview()['binding'], 'qc_intervals':[]}
                with patch('ms_event_studio.features.run_extraction',side_effect=blocked):
                    started=session.start_feature_extraction(payload)
                    self.assertTrue(entered.wait(5))
                    with self.assertRaises(WebBoundaryError): session.undo_review({})
                    with self.assertRaises(WebBoundaryError): session.start_feature_extraction(payload)
                    with self.assertRaises(WebBoundaryError) as raised:
                        session.start_range_apply({'preview_token':'unused','confirmed':True})
                    self.assertEqual(raised.exception.code, 'project_busy')
                    release.set()
                    self.assertEqual(wait_job(session,started)['state'],'succeeded')
                self.assertEqual(session.feature_overview()['accepted'],20)
                payload['binding']='stale'
                with self.assertRaises(WebBoundaryError): session.start_feature_extraction(payload)
            finally: release.set(); session.close()

    def test_queued_cancellation_unlocks_feature_project(self):
        from concurrent.futures import Future
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = feature_project(root)
            session = open_session(root, project)
            try:
                payload = {'source_token': session.register_path('source_file', source)['selection_token'],
                           'binding': session.feature_overview()['binding'], 'qc_intervals': []}
                with patch.object(session._executor, 'submit', return_value=Future()):
                    started = session.start_feature_extraction(payload)
                    cancelled = session.cancel_job(started['job']['job_id'])
                self.assertEqual(cancelled['job']['state'], 'cancelled')
                self.assertEqual(session.feature_overview()['accepted'], 20)
            finally:
                session.close()

    def test_broken_history_does_not_block_new_extraction(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, project = feature_project(Path(tmp))
            broken = project.project_dir/'features'/('a'*32)
            broken.mkdir(parents=True)
            for content in ('{broken', 'null', '[]'):
                with self.subTest(content=content):
                    (broken/'execution_record.json').write_text(content)
                    result = overview(project)
                    self.assertEqual(result['accepted'], 20)
                    self.assertEqual(result['unavailable'], 1)
                    self.assertEqual(result['results'], [])

    def test_exact_inclusion_with_mixed_review_states_and_partial_qc(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, project = feature_project(Path(tmp))
            rows = pd.DataFrame(snapshot(project)['rows'])
            rows.loc[:2, 'status'] = ['pending', 'rejected', 'unreviewed']
            rows['label'] = 'Unknown'
            versions = {identity: 'version-'+identity for identity in rows.event_id}
            package = SimpleNamespace(events=rows, event_versions=versions)
            at = int(rows.current_apex_time_ns.iloc[3])
            with ProjectWindowService.open(project.project_dir) as service:
                events, policy = selected_events(package, service.scans, [[at, at]])
            self.assertEqual(list(events.event_id), list(rows.event_id.iloc[4:]))
            self.assertEqual(list(policy.inclusion_reason.iloc[:4]),
                ['not_accepted', 'not_accepted', 'not_accepted', 'explicit_qc_interval'])
            self.assertEqual(set(events.event_version), {versions[x] for x in rows.event_id.iloc[4:]})

    def test_running_cancellation_and_review_change_refuse_publication(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            source, project = feature_project(Path(tmp))
            saved = snapshot(project)
            cancel = []
            with self.assertRaises(CancelledError):
                run_extraction(project, source, saved, [], lambda: bool(cancel), lambda *_: cancel.append(True))
            self.assertFalse((project.project_dir/'features').exists())
            changed = []
            def change_review(*_):
                if changed: return
                with ProjectWindowService.open(project.project_dir) as service:
                    row = service.all_events()[0]
                    service.review_store.set_status(row['event_id'], 'pending', expected_revision=row['revision'],
                        actor='test', session_id='test', reason='concurrent edit')
                changed.append(True)
            with self.assertRaises(FeatureError):
                run_extraction(project, source, saved, [], lambda: False, change_review)
            self.assertTrue(changed)
            self.assertFalse((project.project_dir/'features').exists())


if __name__ == '__main__':
    unittest.main()
