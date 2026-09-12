"""One analysis ZIP: current reviewed events plus an optional current matrix."""
import json
import os
from pathlib import Path
import tempfile
import re
import uuid
import zipfile

from .export import export_human_csv
from .features import FeatureError, read_result, sha256, snapshot
from .paths import resolve_project_path


def archive_filename(value):
    if not isinstance(value, str) or not value.strip():
        raise FeatureError('请填写 ZIP 文件名。')
    name = value.strip()
    if not name.lower().endswith('.zip'):
        name += '.zip'
    stem = name[:-4]
    if (not stem or stem.endswith(('.', ' ')) or len(name) > 180
            or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name)
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', stem, re.I)):
        raise FeatureError('文件名无效，请使用普通名称，不要填写路径或特殊字符。')
    return name


def export_analysis(project, parent, *, binding, result_id, include_pending, handoff=False, filename=None):
    parent = Path(parent).resolve(strict=True)
    if parent == project.project_dir or project.project_dir in parent.parents:
        raise FeatureError('请选择项目文件夹以外的位置。')
    saved = snapshot(project)
    if binding != saved['binding']:
        raise FeatureError('审阅事件已变化，请重新打开导出窗口。')
    feature_folder = record = None
    if result_id is not None:
        feature_folder, record = read_result(project, result_id, verify=True)
        if record['event_binding'] != binding:
            raise FeatureError('矩阵与当前事件不一致，请重新提取，或仅导出事件表。')
    name = f'{"lma-events" if handoff else "analysis"}-{uuid.uuid4().hex[:12]}.zip'
    name = archive_filename(filename) if filename is not None else name
    destination = parent / name
    if destination.exists():
        raise FeatureError("同名 ZIP 已存在，请修改文件名。")
    with tempfile.TemporaryDirectory(prefix='.ms-analysis-', dir=parent) as temp:
        temp = Path(temp)
        csv = export_human_csv(saved['reviews'], temp/'events.csv',
            analysis_start_ns=saved['interval']['start_ns'], analysis_end_ns=saved['interval']['end_ns'],
            include_pending=include_pending)
        metadata = dict(schema='ms-analysis-export-v1', project_id=saved['project_id'],
                        event_binding=binding, source_sha256=saved['source']['sha256'],
                        include_pending=include_pending, csv_rows=csv.row_count, csv_sha256=csv.sha256,
                        feature_result_id=result_id,
                        feature_events=record['events'] if record else None,
                        note='Matrix excludes explicit QC; pending events, if exported in CSV, are not matrix rows.')
        archive_path = temp / name
        if handoff:
            from flame_ms_core.export import export_machine_contract
            from flame_ms_core.exchange import read_event_package
            event_root = feature_folder / 'source_events' if record else temp / 'events'
            if not record:
                export_machine_contract(saved['reviews'], saved['automatic'], event_root,
                    source_fingerprint=saved['source'], detector_version=saved['protocol']['detector_version'],
                    parameter_hash=saved['protocol']['parameter_hash'], generation_id=saved['protocol']['generation_id'],
                    analysis_start_ns=saved['interval']['start_ns'], analysis_end_ns=saved['interval']['end_ns'],
                    scientific_settings=saved['settings'])
            package = read_event_package(event_root)
            metadata = dict(schema='ms-lma-handoff-v1', feature_result_id=result_id,
                            event_manifest_sha256=package.manifest_sha256,
                            feature_record_sha256=sha256(feature_folder / 'execution_record.json') if record else None)
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            if handoff:
                for item in ('manifest.json', 'events.parquet', 'checksums.sha256'):
                    archive.write(event_root / item, 'events/' + item)
            else:
                archive.write(csv.path, 'events.csv')
            archive.writestr('handoff_record.json' if handoff else 'analysis_record.json', json.dumps(metadata, ensure_ascii=False, indent=2))
            if record:
                for item in [*record['artifacts'], 'execution_record.json']:
                    archive.write(resolve_project_path(feature_folder, item), 'features/'+item)
        if result_id is not None:
            read_result(project, result_id, verify=True)
        digest = sha256(archive_path)
        if snapshot(project)['binding'] != binding:
            raise FeatureError('导出期间事件已变化，未生成结果，请重新打开导出窗口。')
        try:
            os.link(archive_path, destination)
        except FileExistsError as exc:
            raise FeatureError("同名 ZIP 已存在，请修改文件名。") from exc
    return dict(kind='audit_package' if handoff else 'review_results', display_name=name,
                row_count=len(saved['rows']) if handoff else csv.row_count, sha256=digest,
                message=('已导出 LMA 事件包，' if handoff else '已导出分析结果，') +
                        ('包含 Feature 矩阵。' if record else '不含 Feature 矩阵。'))
