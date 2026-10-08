"""P1 work unit must not absorb standalone P2 surface acquisition."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from hardsurface import edit_review as workflow, subdivision, contract
from hardsurface.io import RuntimeFailure
from test_edit_review import request as workflow_request
from test_surface_export import request as export_request
from hardsurface.sparse_observation import MODE


def request():
    q=workflow_request({'file':'/tmp/neutral.blend','sha256':'a'*64,'bytes':10})
    d=q['params']['before_diagnosis']['params']
    d['cpu_threads']=2
    d['surface_export']=copy.deepcopy(export_request()['params']['surface_export'])
    d['surface_export']['binding_mode']=MODE
    return q


class WorkflowSurfaceBoundaryTests(unittest.TestCase):
    def test_standalone_P2_valid_but_P1_schema_rejects(self):
        q=request()
        subdivision.validate_request(q['params']['before_diagnosis'])
        with self.assertRaises(contract.ContractError):workflow.validate_request(q)

    def test_runtime_guard_rejects_even_if_embedded_schema_is_widened(self):
        q=request();schema=copy.deepcopy(workflow.REQUEST)
        schema['properties']['params']['properties']['before_diagnosis']=copy.deepcopy(subdivision.REQUEST)
        with patch.object(workflow,'REQUEST',schema):
            with self.assertRaises(RuntimeFailure) as caught:workflow.validate_request(q)
        self.assertEqual(caught.exception.code,'INVALID_REQUEST')

    def test_nonrendering_workflow_snapshot_has_no_surface_option(self):
        actual=workflow.schema()
        fields=actual['properties']['params']['properties']['before_diagnosis']['properties']['params']['properties']
        self.assertNotIn('surface_export',fields)
        self.assertIn('surface_export',subdivision.schema()['properties']['params']['properties'])
        path=Path(__file__).resolve().parents[1]/'schemas/hardsurface-edit-review.schema.json'
        self.assertEqual(actual,json.loads(path.read_text()))


if __name__=='__main__':unittest.main()
