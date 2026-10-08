# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact-byte reference read regression, using synthetic local JSON only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hardsurface import reference
from hardsurface.io import descriptor,RuntimeFailure


class ExactReferenceByteBindingTests(unittest.TestCase):
    def test_aba_bytes_cannot_replace_pinned_read(self):
        root=Path(tempfile.mkdtemp(prefix='reference-byte-host-fixture-'))
        path=root/'reference.json';original=b'{"value": 1}';path.write_bytes(original)
        rec=descriptor(path);input_ref={'file':rec['file'],'expected_sha256':rec['sha256'],'bytes':rec['bytes']}
        reader=reference.read_json
        def swapped(file,**kwargs):
            path.write_bytes(b'{"value": 2}')
            try:return reader(file,**kwargs)
            finally:path.write_bytes(original)
        with patch.object(reference,'read_json',side_effect=swapped),self.assertRaises(RuntimeFailure):
            reference.verified_json(input_ref)
        self.assertEqual(descriptor(path),rec)


if __name__=='__main__':unittest.main()
