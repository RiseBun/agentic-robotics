"""Public task export must not turn the private evaluator into an online API."""
import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from agentic_cap.server import export_episode

class PublicExportBoundaryTests(unittest.TestCase):
    def test_private_truth_is_excluded_while_public_event_and_binary_evidence_survive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'episode';folder.mkdir()
            (folder/'events.jsonl').write_text('{"kind":"public"}\n')
            (folder/'evaluation-private.jsonl').write_text('{"truth":"secret"}\n')
            (folder/'evaluation-private.bin').write_bytes(b'private-binary')
            (folder/'recorded.bin').write_bytes(b'public-binary')
            with zipfile.ZipFile(io.BytesIO(export_episode(root,'episode'))) as package:
                self.assertNotIn('episode/evaluation-private.jsonl',package.namelist())
                self.assertEqual(package.read('episode/events.jsonl'),b'{"kind":"public"}\n')
                with tarfile.open(fileobj=io.BytesIO(package.read('observations.tar.xz'))) as binary:
                    self.assertNotIn('episode/evaluation-private.bin',binary.getnames())
                    self.assertEqual(binary.extractfile('episode/recorded.bin').read(),b'public-binary')
                self.assertIn('offline',json.loads(package.read('manifest.json'))['scope'])
            self.assertTrue((folder/'evaluation-private.jsonl').exists())

if __name__=='__main__':unittest.main()
