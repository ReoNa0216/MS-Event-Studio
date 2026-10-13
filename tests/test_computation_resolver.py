import base64
import hashlib
import os
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from scripts.resolve_computation import pinned_wheel, release_wheel


class ComputationResolverTest(unittest.TestCase):
    def release_fixture(self, manifest_changes=None):
        source = b'__version__ = "0.2.1"\n'
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as archive:
            archive.writestr('flame_feature_core/__init__.py', source)
            archive.writestr('flame_feature_core-0.2.1.dist-info/METADATA',
                             'Name: flame-feature-core\nVersion: 0.2.1\n')
        wheel = payload.getvalue()
        name = 'flame_feature_core-0.2.1-py3-none-any.whl'
        digest = hashlib.sha256(wheel).hexdigest()
        manifest = dict(version='0.2.1', commit='a'*40,
            artifacts={name:dict(bytes=len(wheel),sha256=digest)},
            runtime_sha256={'src/flame_feature_core/__init__.py':hashlib.sha256(source).hexdigest()})
        manifest.update(manifest_changes or {})
        data = json.dumps(manifest).encode()
        sums = f'{digest}  {name}\n{hashlib.sha256(data).hexdigest()}  release-manifest.json\n'.encode()
        assets = {name:wheel, 'release-manifest.json':data, 'SHA256SUMS.txt':sums}
        lock = dict(version='0.2.1', commit='a'*40, release_tag='v0.2.1', repository='owner/core',
            wheel=dict(filename=name,sha256=digest),
            release_manifest_sha256=hashlib.sha256(data).hexdigest(), checksums_sha256=hashlib.sha256(sums).hexdigest())
        def download(command, check):
            target = Path(command[command.index('--dir')+1])
            for filename, content in assets.items():
                (target/filename).write_bytes(content)
        return lock, assets, download

    def test_official_release_verifies_manifest_metadata_and_runtime(self):
        lock, assets, download = self.release_fixture()
        with tempfile.TemporaryDirectory() as directory, patch('scripts.resolve_computation.subprocess.run', side_effect=download):
            wheel = release_wheel('flame-feature-core', lock, Path(directory))
            self.assertEqual(wheel.read_bytes(), assets[wheel.name])

    def test_release_rejects_wrong_commit_or_runtime_provenance(self):
        for changes, message in [({'commit':'b'*40}, 'version/commit'),
                ({'runtime_sha256':{'src/flame_feature_core/__init__.py':'0'*64}}, 'runtime source')]:
            lock, _, download = self.release_fixture(changes)
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory, patch(
                    'scripts.resolve_computation.subprocess.run', side_effect=download):
                with self.assertRaisesRegex(RuntimeError, message):
                    release_wheel('flame-feature-core', lock, Path(directory))

    def test_release_rejects_changed_attachment(self):
        lock, assets, download = self.release_fixture()
        assets['release-manifest.json'] += b' '
        with tempfile.TemporaryDirectory() as directory, patch('scripts.resolve_computation.subprocess.run', side_effect=download):
            with self.assertRaisesRegex(RuntimeError, 'pinned SHA256'):
                release_wheel('flame-feature-core', lock, Path(directory))

    def test_pinned_wheel_checks_bytes_before_writing(self):
        payload = b'fixed computation wheel'
        lock = {'wheel': {'secret': 'FLAME_TEST_WHEEL', 'filename': 'example.whl',
                          'sha256': hashlib.sha256(payload).hexdigest()}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'FLAME_TEST_WHEEL': ''}):
                self.assertIsNone(pinned_wheel(lock, root))
            with patch.dict(os.environ, {'FLAME_TEST_WHEEL': base64.b64encode(b'changed').decode()}):
                with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                    pinned_wheel(lock, root)
                self.assertEqual(list(root.iterdir()), [])
            with patch.dict(os.environ, {'FLAME_TEST_WHEEL': base64.b64encode(payload).decode()}):
                self.assertEqual(pinned_wheel(lock, root).read_bytes(), payload)
