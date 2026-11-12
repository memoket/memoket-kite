"""Fictional wire-schema and failed-HTTP regression cases; zero paid calls."""
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import jsonschema
from benchmarks.multihop.schema_transport import inline_annotated_refs
from memoket_kite.providers import llm


class SchemaTransportTests(unittest.TestCase):
    def test_constraints_descriptions_and_pure_refs_survive(self):
        schema = {'type': 'object', '$defs': {'span': {'type': 'string', 'pattern': r'^w[0-2]:w[0-2]$'}},
                  'properties': {'name': {'$ref': '#/$defs/span', 'description': 'Use the literal name.'},
                                 'source': {'$ref': '#/$defs/span'}},
                  'required': ['name', 'source'], 'additionalProperties': False}
        old = copy.deepcopy(schema)
        fixed = inline_annotated_refs(schema)
        self.assertEqual(schema, old)
        self.assertEqual(fixed['properties']['name'], {'type': 'string', 'pattern': r'^w[0-2]:w[0-2]$', 'description': 'Use the literal name.'})
        self.assertEqual(fixed['properties']['source'], schema['properties']['source'])
        for value in [{'name': a, 'source': b} for a in ['w0:w1', 'w9:w9', 1] for b in ['w0:w2', 'unknown']]:
            self.assertEqual(jsonschema.Draft202012Validator(schema).is_valid(value),
                             jsonschema.Draft202012Validator(fixed).is_valid(value))

    def test_semantic_siblings_and_annotated_cycles_are_refused(self):
        for schema in [
            {'$defs': {'a': {'type': 'string'}}, '$ref': '#/$defs/a', 'maxLength': 1},
            {'$defs': {'a': {'$ref': '#/$defs/a', 'description': 'loop'}}, '$ref': '#/$defs/a', 'description': 'loop'},
        ]:
            with self.assertRaises(ValueError):
                inline_annotated_refs(schema)

    def test_http_error_preserves_type_status_and_redacted_body(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/'calls.jsonl'
            key = 'fictional-credential'
            body = json.dumps({'error': {'param': 'response_format', 'message': 'invalid ref '+key}}).encode()
            error = urllib.error.HTTPError('https://invalid.test', 400, 'Bad Request', {}, io.BytesIO(body))
            with patch.dict(os.environ, {'OPENAI_API_KEY': key, 'OPENAI_BASE_URL': 'https://invalid.test/v1', 'KITE_LLM_LOG': str(log)}), patch.object(llm.urllib.request, 'urlopen', side_effect=error):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    llm._http_llm('Fictional request', 1, model='gpt-4o-mini')
            self.assertIs(raised.exception, error)
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([row['event'] for row in rows], ['request', 'error'])
            self.assertEqual(rows[-1]['http_status'], 400)
            self.assertIn('response_format', rows[-1]['error_body'])
            self.assertNotIn(key, log.read_text())

    def test_error_body_is_bounded_and_read_failure_does_not_mask_http_error(self):
        secret = 'fictional-credential'
        body = b'x'*8188+secret.encode()+b'y'*100
        error = urllib.error.HTTPError('https://invalid.test', 400, 'Bad Request', {}, io.BytesIO(body))
        details = llm._http_error_details(error, secret)
        self.assertLessEqual(len(details['error_body']), 8192)
        self.assertTrue(details['error_body_truncated'])
        self.assertNotIn(secret[:4], details['error_body'])
        with patch.object(error, 'read', side_effect=OSError('unreadable')):
            self.assertEqual(llm._http_error_details(error, secret)['error_body_read_error'], 'OSError')


if __name__ == '__main__':
    unittest.main()
