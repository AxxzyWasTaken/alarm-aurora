import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'packages' / 'aurora-flatpak-gl' / 'aurora-flatpak-gl'
ID = 'org.freedesktop.Platform.GL.aurora'

# A flatpak stand-in that keeps the system installation in files: installed
# refs, remotes and the global override's environment lines. FAKE_FLATPAK_FAIL
# names one subcommand that fails.
FAKE_FLATPAK = r'''#!/bin/bash
s=$FAKE_FLATPAK_STATE
touch "$s/installed" "$s/remotes"
cmd=$1; shift
[ "${FAKE_FLATPAK_FAIL:-}" = "$cmd" ] && { echo "error: $cmd failed" >&2; exit 1; }
args=()
for a in "$@"; do case $a in -*) ;; *) args+=("$a") ;; esac; done
case $cmd in
  remotes) cat "$s/remotes" ;;
  remote-add) echo "${args[-2]}" >> "$s/remotes" ;;
  remote-modify) ;;
  remote-delete) grep -vxF "${args[0]}" "$s/remotes" > "$s/r.tmp"; mv "$s/r.tmp" "$s/remotes" ;;
  list) cat "$s/installed" ;;
  install) ref=${args[1]#runtime/}; grep -qxF "$ref" "$s/installed" || echo "$ref" >> "$s/installed" ;;
  uninstall) grep -vxF "${args[0]#runtime/}" "$s/installed" > "$s/i.tmp"; mv "$s/i.tmp" "$s/installed" ;;
  pin) ;;
  override)
    grep -qx '\[Environment\]' "$OVERRIDES" 2>/dev/null || echo '[Environment]' >> "$OVERRIDES"
    for a in "$@"; do case $a in --env=*) grep -qxF "${a#--env=}" "$OVERRIDES" || echo "${a#--env=}" >> "$OVERRIDES" ;; esac; done ;;
esac
'''


class FlatpakGlHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.state = self.tmp / 'state'
        self.state.mkdir()
        bindir = self.tmp / 'bin'
        bindir.mkdir()
        fake = bindir / 'flatpak'
        fake.write_text(FAKE_FLATPAK)
        fake.chmod(0o755)
        self.repo = self.tmp / 'repo'
        (self.repo / 'objects').mkdir(parents=True)
        heads = self.repo / 'refs/heads/runtime' / ID / 'aarch64'
        heads.mkdir(parents=True)
        for branch in ('25.08', '26.08'):
            (heads / branch).write_text('0' * 64 + '\n')
        self.overrides = self.tmp / 'global'
        self.env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}', FAKE_FLATPAK_STATE=str(self.state),
                        OVERRIDES=str(self.overrides), AURORA_FLATPAK_GL_REPO=str(self.repo),
                        AURORA_FLATPAK_GL_OVERRIDES=str(self.overrides))

    def run_helper(self, action):
        return subprocess.run(['bash', str(HELPER), action], env=self.env, capture_output=True, text=True)

    def installed(self):
        path = self.state / 'installed'
        return path.read_text().split() if path.exists() else []

    def test_enable_then_disable(self):
        self.assertEqual(self.run_helper('enable').returncode, 0)
        self.assertEqual(self.installed(), [f'{ID}/aarch64/25.08', f'{ID}/aarch64/26.08'])
        self.assertEqual(self.run_helper('disable').returncode, 0)
        self.assertEqual(self.installed(), [])
        self.assertFalse(self.overrides.exists())

    def test_a_failed_pin_is_reported(self):
        # An unpinned GL.aurora is removed by "flatpak uninstall --unused".
        self.env['FAKE_FLATPAK_FAIL'] = 'pin'
        self.assertNotEqual(self.run_helper('enable').returncode, 0)

    def test_a_failed_uninstall_is_reported(self):
        self.run_helper('enable')
        self.env['FAKE_FLATPAK_FAIL'] = 'uninstall'
        self.assertNotEqual(self.run_helper('disable').returncode, 0)
        self.assertEqual(len(self.installed()), 2)

    def test_a_missing_pin_is_not_an_error(self):
        # "flatpak pin --remove" fails for a ref that was never pinned.
        self.run_helper('enable')
        self.env['FAKE_FLATPAK_FAIL'] = 'pin'
        self.assertEqual(self.run_helper('disable').returncode, 0)


if __name__ == '__main__':
    unittest.main()
