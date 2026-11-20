"""The ALARM Edge overlay feed and Edge images (aurora-distributions docs/edge-train.md)."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import edgelib  # noqa: E402
import updatelib  # noqa: E402


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


site = module('edge_channel_site', 'scripts/channel-site.py')
proof = module('edge_image_proof', 'scripts/edge-image-proof.py')

LINUX = {'repository': 'aurora-silicon/linux', 'branch': 'aurora-edge', 'revision': '1' * 40, 'tree': '2' * 40}
MESA = {'repository': 'aurora-silicon/mesa', 'branch': 'aurora-edge', 'revision': '3' * 40, 'tree': '4' * 40}
PACMAN = """[options]
Architecture = aarch64

[core]
Include = /etc/pacman.d/mirrorlist
"""


class OverlayTests(unittest.TestCase):
    def test_edge_tags_are_their_own_namespace(self):
        self.assertEqual(edgelib.snapshot('edge-20261121.1'), 'edge-20261121.1')
        for tag in ('testing-20261121.1', 'edge-latest', 'edge-20261121.1/x'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                edgelib.snapshot(tag)

    def test_the_overlay_carries_only_kernel_and_mesa_packages(self):
        rows = [('linux-aurora', 'linux-aurora', '7.3.0.aurora1-43'),
                ('linux-aurora', 'linux-aurora-headers', '7.3.0.aurora1-43'),
                ('mesa-aurora', 'vulkan-aurora', '26.3.0-35'), ('mesa-aurora', 'mesa-aurora', '26.3.0-35'),
                ('aurora-flatpak-gl', 'aurora-flatpak-gl', '26.3.0-35'),
                ('mesa-aurora-fex-guest', 'mesa-aurora-fex-guest', '26.3.0-35')]
        self.assertEqual(edgelib.select(rows), sorted(name for _, name, _ in rows))
        with self.assertRaisesRegex(ValueError, 'not a kernel or Mesa'):
            edgelib.select(rows + [('aura', 'aura', '1.0.0b14-1')])
        with self.assertRaisesRegex(ValueError, 'one version'):
            edgelib.select(rows + [('mesa-aurora', 'opencl-rusticl-aurora', '26.3.0-34')])
        with self.assertRaisesRegex(ValueError, 'one version'):
            edgelib.select(rows[2:])

    def test_staging_may_change_only_the_pair_recipes(self):
        edgelib.check_staging_files(['sources.json', 'packages/linux-aurora/PKGBUILD', 'packages/linux-aurora/config',
                                     'packages/mesa-aurora/PKGBUILD', 'packages/aurora-flatpak-gl/PKGBUILD'])
        for path in ('packages/aura/PKGBUILD', '.github/workflows/packages.yml', 'scripts/build-package'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'outside the kernel and Mesa'):
                edgelib.check_staging_files(['sources.json', path])

    def test_packaged_sources_are_one_pair(self):
        sources = {'linux-aurora': {'commit': LINUX['revision']}, 'mesa-aurora': {'commit': MESA['revision']},
                   'mesa-aurora-fex-guest': {'commit': MESA['revision']}}
        trees = {'linux': LINUX['tree'], 'mesa': MESA['tree']}
        self.assertEqual(edgelib.packaged_sources(sources, trees), {'linux': LINUX, 'mesa': MESA})
        sources['mesa-aurora-fex-guest']['commit'] = '5' * 40
        with self.assertRaisesRegex(ValueError, 'one Mesa'):
            edgelib.packaged_sources(sources, trees)


class GateTests(unittest.TestCase):
    def record(self, tag='edge-20261121.1'):
        return {'schema_version': 1, 'release': 'alpha.4', 'pair': {'linux': LINUX, 'mesa': MESA},
                'distributions': {'arch': {'repository': 'aurora-silicon/alarm-aurora', 'snapshot': tag}}}

    def sources(self, mesa=None):
        return {'schema': 1, 'channel': 'edge', 'packaged_sources': {'linux': LINUX, 'mesa': mesa or MESA}}

    def test_edge_is_a_channel_with_its_own_database(self):
        self.assertIn('edge', site.CHANNELS)
        self.assertEqual(site.database('edge'), 'aurora-edge')
        self.assertEqual(site.database('testing'), 'aurora')
        self.assertTrue(site.publisher('edge').endswith('/publish-edge.yml'))
        updatelib.record_channel(updatelib.empty_channels('aurora-silicon/alarm-aurora'), 'edge', {'tag': 'edge-20261121.1'})

    def test_only_the_adopted_train_snapshot_moves_the_overlay(self):
        edgelib.edge_gate(self.record(), 'edge-20261121.1', self.sources())
        for record, tag, sources, message in (
                (self.record('edge-20261120.1'), 'edge-20261121.1', self.sources(), 'adopted Edge train'),
                (self.record(), 'edge-20261121.1', self.sources(mesa=dict(MESA, revision='5' * 40)), 'pair'),
                (None, 'edge-20261121.1', self.sources(), 'adopted Edge train')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                edgelib.edge_gate(record, tag, sources)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        modules = self.root / 'usr/lib/modules/7.3.0-aurora1-43-aurora'
        modules.mkdir(parents=True)
        (modules / 'vmlinuz').write_bytes(b'kernel image')
        (self.root / 'usr/lib/libvulkan_asahi.so').write_bytes(b'mesa dso')
        (self.root / 'etc').mkdir()
        self.sources = {'linux': LINUX, 'mesa': MESA}

    def write_conf(self, text):
        (self.root / 'etc/pacman.conf').write_text(text)

    def test_build_and_sealed_sections(self):
        text = edgelib.add_sections(PACMAN, 'testing-20261121.1', 'edge-20261121.1')
        self.assertLess(text.index('[aurora-edge]'), text.index('[aurora]'))
        self.assertLess(text.index('[aurora]'), text.index('[core]'))
        self.assertIn('Server = https://github.com/aurora-silicon/alarm-aurora/releases/download/edge-20261121.1\n', text)
        sealed = edgelib.seal(text, 'testing-20261121.1', 'edge-20261121.1')
        self.assertIn('[aurora-edge]\nSigLevel = Required DatabaseRequired\nServer = ' + edgelib.FEED + '\n', sealed)
        self.assertIn('[aurora]\nSigLevel = Required DatabaseRequired\n'
                      'Server = https://aurorasilicon.org/alarm-aurora/testing/aarch64\n', sealed)
        self.assertNotIn('alarm-aurora/releases/download', sealed)
        plain = edgelib.add_sections(PACMAN, 'testing-20261121.1', None)
        self.assertNotIn('[aurora-edge]', plain)

    def test_proof_records_the_overlay_build(self):
        self.write_conf(edgelib.seal(edgelib.add_sections(PACMAN, 'testing-20261121.1', 'edge-20261121.1'),
                                     'testing-20261121.1', 'edge-20261121.1'))
        result = proof.proof(self.root, 'arch-kde', 'edge-20261121.1', self.sources)
        self.assertEqual((result['schema'], result['distribution'], result['channel']), (2, 'arch', 'edge'))
        self.assertEqual(result['feed'], edgelib.FEED)
        self.assertEqual(result['files']['kernelImageSHA256'], 'sha256:' + hashlib.sha256(b'kernel image').hexdigest())
        self.assertEqual(result['files']['mesaDsoSHA256'], 'sha256:' + hashlib.sha256(b'mesa dso').hexdigest())

    def test_an_image_without_the_overlay_first_has_no_proof(self):
        self.write_conf(edgelib.seal(edgelib.add_sections(PACMAN, 'testing-20261121.1', None), 'testing-20261121.1', None))
        with self.assertRaisesRegex(ValueError, 'Edge feed'):
            proof.proof(self.root, 'arch-kde', 'edge-20261121.1', self.sources)


class WorkflowTests(unittest.TestCase):
    def test_overlay_is_signed_only_on_main_in_the_signing_environment(self):
        text = (ROOT / '.github/workflows/publish-edge.yml').read_text()
        collect, rest = text.split('\n  sign:\n', 1)
        sign = rest.split('\n  release:\n', 1)[0]
        self.assertIn('environment: testing-signing', sign)
        self.assertIn('--network none', sign)
        self.assertIn('--repository-name aurora-edge', sign)
        self.assertNotIn('secrets.', collect)
        self.assertIn("github.ref == 'refs/heads/main'", collect)

    def test_deploys_and_images_know_edge(self):
        deploy = (ROOT / '.github/workflows/deploy-snapshot.yml').read_text()
        self.assertIn('options: [testing, stable, edge]', deploy)
        self.assertIn('site/installer/edge-train.json', deploy)
        images = (ROOT / '.github/workflows/images.yml').read_text()
        self.assertIn('options: [testing, stable, edge]', images)
        self.assertIn('out/*-installer/edge-image-proof.json', images)


if __name__ == '__main__':
    unittest.main()
