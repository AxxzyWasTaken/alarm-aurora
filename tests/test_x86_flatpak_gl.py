"""x86_64 Flatpak apps: one FEX bundle, the x86_64 GL.aurora extension and its override."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GL = ROOT / 'packages/aurora-flatpak-gl'
X86 = ROOT / 'packages/aurora-flatpak-gl-x86_64'
FEX = ROOT / 'packages/fex-emu'
BUNDLE = '/usr/lib/x86_64-linux-gnu/GL/aurora/fex'
spec = importlib.util.spec_from_file_location('x86_tree', X86 / 'validate-tree.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
COMMIT = 'c208afc352e9d4c10ee71c883a369a9079dd7d84'


def assignment(path, key):
    return re.search(rf'^{key}=(.*)$', path.read_text(), re.M)[1]


class FexBundleTests(unittest.TestCase):
    def test_fex_emu_owns_the_only_x86_registrations_and_points_them_at_the_bundle(self):
        for name, machine in (('FEX-x86.conf', r'\x03\x00'), ('FEX-x86_64.conf', r'\x3e\x00')):
            with self.subTest(name=name):
                lines = [l for l in (FEX / name).read_text().splitlines() if not l.startswith('#')]
                self.assertEqual(len(lines), 1)
                fields = lines[0].split(':')
                self.assertEqual(fields[1], name.removesuffix('.conf'))
                self.assertTrue(fields[4].endswith(machine))
                self.assertEqual(fields[6:], [BUNDLE + '/FEX', 'POCF'])
        recipe = (FEX / 'PKGBUILD').read_text()
        self.assertIn(f'bash fex-bundle build "$pkgdir{BUNDLE}"', recipe)
        for name in ('FEX-x86.conf', 'FEX-x86_64.conf'):
            self.assertIn(f'install -Dm644 {name} "$pkgdir/usr/lib/binfmt.d/{name}"', recipe)
        # Nothing else in the tree registers an x86 interpreter.
        for path in (ROOT / 'packages').rglob('*'):
            if path.is_file() and path.parent != FEX and path.suffix in ('.conf', '.hook', ''):
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertNotIn(r'\x02\x00\x3e\x00', path.read_text(errors='replace'))

    def test_bundle_script_targets_the_extension_path(self):
        script = (FEX / 'fex-bundle').read_text()
        self.assertIn(f'final={BUNDLE}\n', script)
        self.assertIn('--set-interpreter "$final/ld-linux-aarch64.so.1" --force-rpath --set-rpath "$final"', script)


if __name__ == '__main__':
    unittest.main()
