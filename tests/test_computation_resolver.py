import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.resolve_computation import pinned_wheel


class ComputationResolverTest(unittest.TestCase):
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
