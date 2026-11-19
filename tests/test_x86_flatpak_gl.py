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

    def test_x86_extension_takes_the_vdso_thunk_from_the_packaged_fex(self):
        recipe = X86 / 'PKGBUILD'
        self.assertEqual(assignment(recipe, '_fex_commit'), assignment(FEX / 'PKGBUILD', '_commit'))
        self.assertEqual(assignment(recipe, '_fex_sha256'), assignment(FEX / 'PKGBUILD', '_sha256'))
        self.assertIn('cmake --build guest-thunks --target VDSO-guest', recipe.read_text())


class ManifestTests(unittest.TestCase):
    def test_x86_drivers_link_no_llvm_and_live_under_the_x86_prefix(self):
        text = (X86 / 'org.freedesktop.Platform.GL.aurora.yml.in').read_text()
        self.assertNotIn('aarch64-linux-gnu', text)
        mesa = text[text.index('  - name: mesa\n'):]
        for option in ('-Dllvm=disabled', '-Dgallium-drivers=asahi,zink', '-Dvulkan-drivers=asahi',
                       '-Dmesa-clc=system', '-Dprecomp-compiler=system'):
            self.assertIn('      - ' + option + '\n', mesa)
        self.assertNotIn('libLLVM', mesa)
        self.assertIn('  - /share/build-tools\n', text)
        self.assertIn('prefix: /usr/lib/x86_64-linux-gnu/GL/aurora\n', text)

    def test_mesa_consumers_include_the_x86_extension(self):
        for field in ('epoch', 'pkgver', 'pkgrel', '_commit', '_sha256'):
            self.assertEqual(assignment(X86 / 'PKGBUILD', field), assignment(GL / 'PKGBUILD', field))


class TreeValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trees = Path(self.tmp.name)
        elf = b'\x7fELF\x02\x01' + bytes(12) + struct.pack('<H', 62)
        for branch in validator.BRANCHES:
            root = self.trees / branch / 'files'
            self.write(root / '.aurora-mesa-commit', COMMIT)
            for name in ('lib/libvulkan_asahi.so', 'lib/libEGL_mesa.so.0', 'lib/libGLX_mesa.so.0',
                         'lib/dri/asahi_dri.so', 'lib/dri/zink_dri.so', 'GuestThunks/libVDSO-guest.so'):
                self.write(root / name, elf + f' Mesa (git-{COMMIT[:10]}), Borealis'.encode())
            for path, lib in (('vulkan/icd.d/asahi_icd.x86_64.json', 'libvulkan_asahi.so'),
                              ('glvnd/egl_vendor.d/50_mesa.json', 'libEGL_mesa.so.0')):
                self.write(root / path, json.dumps({'ICD': {'library_path': validator.PREFIX + 'lib/' + lib}}))
            self.write(root / 'share/drirc.d/00-hk-defaults.conf', 'hk_report_honeykrisp_driver_id')
            self.write(self.trees / branch / 'metadata',
                       f'[Runtime]\nname={validator.ID}\n'
                       f'[ExtensionOf]\nref=runtime/org.freedesktop.Platform/x86_64/{branch}\npriority=10\n')

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode() if isinstance(value, str) else value)

    def test_complete_trees(self):
        validator.validate(self.trees, COMMIT)

    def test_aarch64_library_is_rejected(self):
        self.write(self.trees / '26.08/files/lib/dri/zink_dri.so', b'\x7fELF\x02\x01' + bytes(12) + struct.pack('<H', 183))
        with self.assertRaisesRegex(ValueError, 'not an x86-64'):
            validator.validate(self.trees, COMMIT)

    def test_llvm_and_a_packaged_fex_bundle_are_rejected(self):
        path = self.trees / '25.08/files/lib/libGLX_mesa.so.0'
        path.write_bytes(path.read_bytes() + b'libLLVM.so.22.1')
        with self.assertRaisesRegex(ValueError, 'links LLVM'):
            validator.validate(self.trees, COMMIT)
        path.write_bytes(path.read_bytes().replace(b'libLLVM.so.22.1', b''))
        self.write(self.trees / '25.08/files/fex/FEX', b'')
        with self.assertRaisesRegex(ValueError, 'FEX bundle'):
            validator.validate(self.trees, COMMIT)

    def test_aarch64_runtime_is_rejected(self):
        path = self.trees / '25.08/metadata'
        path.write_text(path.read_text().replace('/x86_64/', '/aarch64/'))
        with self.assertRaisesRegex(ValueError, 'identity or priority'):
            validator.validate(self.trees, COMMIT)


if __name__ == '__main__':
    unittest.main()
