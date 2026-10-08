# SPDX-License-Identifier: GPL-3.0-or-later
"""New source cages fail before queue or process creation. HOST tests only."""
from pathlib import Path
import json,tempfile,unittest
from unittest import mock

from test_sparse_source_stage import source_request
from test_sparse_fixed_frame_pipeline import fixed_parameters
from hardsurface import host
from hardsurface.io import RuntimeFailure


class SourceCageAdmissionTests(unittest.TestCase):
    def test_failed_complete_quality_never_enters_queue_or_spawns(self):
        for background in (False,True):
            root=Path(tempfile.mkdtemp(prefix='source-gate-admission-'))
            request=source_request();step=fixed_parameters()
            step['sparse_cage']['wall_support_fraction']=.03
            request['params']['design']['state']['features'][0]['program']['steps']=[step]
            path=root/'request.json';path.write_text(json.dumps(request))
            with self.subTest(background=background),mock.patch.object(host,'store_for') as queue,mock.patch.object(host.subprocess,'Popen') as spawn,mock.patch.object(host,'worker') as native:
                with self.assertRaises(RuntimeFailure) as caught:
                    host.submit(path,root/'jobs','never-executed',background=background)
                self.assertEqual(caught.exception.code,'SPARSE_SOURCE_PREFLIGHT_FAILED')
                queue.assert_not_called();spawn.assert_not_called();native.assert_not_called()

    def test_successful_new_source_still_requires_real_execution(self):
        root=Path(tempfile.mkdtemp(prefix='source-gate-pass-admission-'))
        request=source_request();step=fixed_parameters();step['sparse_cage']['wall_support_fraction']=.25
        request['params']['design']['state']['features'][0]['program']['steps']=[step]
        path=root/'request.json';path.write_text(json.dumps(request))
        # Stop at the queue boundary to prove a predictive pass is not returned
        # as native success and does not bypass normal execution ownership.
        with mock.patch.object(host,'store_for',side_effect=RuntimeError('queue boundary')) as queue:
            with self.assertRaisesRegex(RuntimeError,'queue boundary'):
                host.submit(path,root/'jobs','never-executed')
            queue.assert_called_once()


if __name__=='__main__':unittest.main()
