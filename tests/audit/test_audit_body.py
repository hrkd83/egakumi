"""Blobの断片から本文と部分記録を正しく復元する。"""
import json
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from audit.audit_body import recover


class AuditBodyTests(unittest.TestCase):
    def test_stream_reconstruction_and_missing_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            request_id = str(UUID(int=1, version=4))
            capture_id = str(UUID(int=2, version=4))
            root = Path(temporary) / request_id / capture_id
            root.mkdir(parents=True)

            def write(sequence, kind, data, *, part=0, parts=1):
                source = root / f'{sequence:09d}.json'
                source.write_text(json.dumps({'version': 1, 'sequence': sequence,
                    'requestId': request_id, 'captureId': capture_id, 'operationId': 'responses-create',
                    'timeUtc': '2026-01-01T00:00:00.000+00:00', 'kind': kind,
                    'part': part, 'parts': parts, 'data': data}), encoding='utf-8')
                source.chmod(0o600)
                return source

            write(0, 'request', '{"input":"Synthetic"}')
            write(1, 'stream-segment', '{"text":', parts=2)
            part = write(2, 'stream-segment', '{"output.0":"Result"}}', part=1, parts=2)
            write(3, 'stream-end', '{"state":"complete"}')
            reconstructed = recover(root, lambda path: path)
            self.assertTrue(reconstructed['complete'])
            self.assertEqual(reconstructed['streamText'], {'output.0': 'Result'})
            part.unlink()
            incomplete = recover(root, lambda path: path)
            self.assertFalse(incomplete['complete'])
            self.assertTrue(incomplete['sequenceGap'])
            self.assertEqual(incomplete['incompleteGroups'], [1])

            (root / '000000003.json').unlink()
            write(1, 'response', '{"httpStatus":200,"bodyOmitted":"projection-size"}')
            omitted = recover(root, lambda path: path)
            self.assertTrue(omitted['complete'])
            self.assertEqual(omitted['bodyOmissions'], [
                {'sequence': 1, 'kind': 'response', 'reason': 'projection-size'}])
