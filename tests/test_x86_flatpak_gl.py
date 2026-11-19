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

    def test_fex_emu_ships_the_vdso_thunk_and_isolates_flatpak_socket(self):
        recipe = (FEX / 'PKGBUILD').read_text()
        self.assertIn("'openssl'", recipe)
        self.assertIn('cmake --build guest-thunks --target VDSO-guest', recipe)
        self.assertIn(f'"{BUNDLE}/GuestThunks/libVDSO-guest.so"', recipe.replace('$pkgdir', ''))
        self.assertIn('0004-FEXServerClient-isolate-flatpak-sandbox-socket.patch', recipe)
        self.assertIn('grep -qaF .flatpak- build/Bin/FEX', recipe)
        self.assertIn('grep -qaF .flatpak- build/Bin/FEXServer', recipe)
        self.assertIn("grep -qaF 'Server{}.lock' build/Bin/FEXServer", recipe)
        self.assertIn("grep -qE 'Library soname: \\[linux-vdso\\.so\\.1\\]'", recipe)
        patch = (FEX / '0004-FEXServerClient-isolate-flatpak-sandbox-socket.patch').read_text()
        self.assertIn('/.flatpak-info', patch)
        self.assertIn('[Instance]', patch)
        self.assertIn('instance-id=', patch)
        self.assertIn('.flatpak-{}', patch)
        self.assertIn('{}Server{}.lock', patch)
        self.assertIn('{}{}.FEXServer.Socket', patch)
        readme = (FEX / 'README.aurora').read_text()
        for token in ('glibc', 'gcc-libs', 'libfmt', 'libxxhash', '.flatpak-', 'Server.flatpak-'):
            self.assertIn(token, readme)
        updates = (ROOT / 'docs/updates.md').read_text()
        for token in ('fex-emu', 'glibc', 'gcc-libs', 'libfmt', 'libxxhash'):
            self.assertIn(token, updates)



if __name__ == '__main__':
    unittest.main()
