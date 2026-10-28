r"""Regression tests for defects found in the follow-up review (2026-10-27), no game data:

- the harness records a missing requested path-data file as an error per file key (not only when every requested
  file is absent), in the file scope so a later level check named "path-data" cannot overwrite it;
- the harness refuses to overwrite its --reference baseline when the output name is a Windows casing alias of it
  (LAST vs last);
- NavKit's restore still removes the generated file after a second install onto a map that had no original HPD
  (the second install must keep recording that the original state was "no file");
- the stock-MP installer discovers its install markers case-insensitively, so a Windows directory written as
  MP_TEST is reverted like mp_test;
- FbrbTool rejects a four-column fbrb.manifest (an older unpack) whose size2 differs from the payload length,
  instead of silently losing that size2, while a four-column manifest whose size2 matches still packs.

  python tests\test_review_fixes2.py
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit'))
sys.path.insert(0, os.path.join(SUITE, 'moh-auto-navkit', 'tools'))


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, ok, what, detail=''):
        self.failed += not ok
        print(f'{"ok  " if ok else "FAIL"} {what}' + (f'  ({detail})' if detail and not ok else ''))


def harness_missing(c, work):
    sys.path.insert(0, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness'))
    import harness
    from testharness import checks, levelchecks, levelinfo
    fake = types.SimpleNamespace(name='LV', notes=[], sources=[], targets=[], orders=[],
                                 heightfield=None, water=None, assets={})
    saved = levelinfo.gather, levelinfo.pathdata_files, levelchecks.all_checks
    levelinfo.gather = lambda root, lv, cache, log: fake
    levelinfo.pathdata_files = lambda root, lv, which, navkit_source: []
    levelchecks.all_checks = lambda info, ph: {'path-data': checks.Result('pass', 'ok')}
    try:
        opts = {'files': ['navkit', 'dice'], 'navkit': 'build', 'tests': ['structure', 'level'],
                'pairs': 10, 'level_pairs': 10, 'seed': 1}
        L = harness.run_level('ROOT', 'lv', opts, os.path.join(work, 'cache'), log=lambda *a: None)
        c.check(L['files']['navkit']['tests']['missing']['status'] == 'error'
                and L['files']['dice']['tests']['missing']['status'] == 'error',
                'harness: a missing requested file is an error per key, not only when all are absent')
        c.check(L['level']['path-data']['status'] == 'pass',
                'harness: the level path-data check is not overwritten by the missing-file error')
    finally:
        levelinfo.gather, levelinfo.pathdata_files, levelchecks.all_checks = saved


def harness_baseline_case(c, work):
    sys.path.insert(0, os.path.join(SUITE, 'moh-automated-map-and-bot-test-harness'))
    import harness
    root = os.path.join(work, 'opfor')
    os.makedirs(os.path.join(root, 'game'))
    runs_dir = os.path.join(root, 'work', 'harness', 'runs')
    os.makedirs(runs_dir)
    last = os.path.join(runs_dir, 'last.json')
    open(last, 'w').write('{"format": "moh-harness-run", "version": 1, "name": "last"}')
    with contextlib.redirect_stdout(io.StringIO()):
        rc = harness.cmd_run(['LAST', '--reference', 'last', '--opfor', root, '--levels', 'sp_009',
                              '--files', 'dice', '--tests', 'structure'])
    c.check(rc == 2 and open(last).read().startswith('{"format"'),
            'harness: --reference last with output LAST (the same Windows file) is refused, baseline kept')


def navkit_restore_reinstall(c, work):
    import navkit
    saved_gr = navkit.game_running
    navkit.game_running = lambda: False

    class Pr:
        root = work

        def pathdata(self, lv):
            return os.path.join(work, 'game', 'Output', 'win32', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')

        def out_dir(self, lv):
            d = os.path.join(work, 'navkit', lv)
            os.makedirs(d, exist_ok=True)
            return d

    try:
        pr = Pr()
        ours = os.path.join(pr.out_dir('sp_01'), 'pathdata.hpd')
        open(ours, 'wb').write(b'build 1')
        navkit.install(pr, 'sp_01', src=ours, log=lambda *a: None)
        open(ours, 'wb').write(b'build 2')
        navkit.install(pr, 'sp_01', src=ours, log=lambda *a: None)
        dest = pr.pathdata('sp_01')
        c.check(open(dest, 'rb').read() == b'build 2' and os.path.isfile(dest + navkit.NEW_SUFFIX)
                and not os.path.isfile(dest + navkit.BACKUP_SUFFIX),
                'navkit: reinstalling onto a no-original map keeps the no-original record, not a fake backup')
        navkit.restore(pr, 'sp_01', log=lambda *a: None)
        c.check(not os.path.isfile(dest) and not os.path.isfile(dest + navkit.NEW_SUFFIX),
                'navkit: restore after a second no-original install removes the generated file')
    finally:
        navkit.game_running = saved_gr


def navkit_restore_deleted(c, work):
    import navkit
    saved_gr = navkit.game_running
    navkit.game_running = lambda: False

    class Pr:
        root = work

        def pathdata(self, lv):
            return os.path.join(work, 'game', 'Output', 'win32', 'ai', 'levels', lv, 'infantry', 'pathdata.hpd')

        def out_dir(self, lv):
            d = os.path.join(work, 'navkit', lv)
            os.makedirs(d, exist_ok=True)
            return d

    def setup(lv):
        pr = Pr()
        ours = os.path.join(pr.out_dir(lv), 'pathdata.hpd')
        open(ours, 'wb').write(b'build')
        dest = pr.pathdata(lv)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, 'wb').write(b'original')
        navkit.install(pr, lv, src=ours, log=lambda *a: None)   # backs up the original
        return pr, ours, dest

    try:
        pr, ours, dest = setup('sp_02')
        os.remove(dest)                                          # the installed file is deleted
        navkit.install(pr, 'sp_02', src=ours, log=lambda *a: None)   # reinstall
        c.check(not os.path.isfile(dest + navkit.NEW_SUFFIX) and os.path.isfile(dest + navkit.BACKUP_SUFFIX),
                'navkit: reinstalling after the installed file was deleted keeps the backup, not a no-original marker')
        navkit.restore(pr, 'sp_02', log=lambda *a: None)
        c.check(open(dest, 'rb').read() == b'original' and not os.path.isfile(dest + navkit.NEW_SUFFIX),
                'navkit: restore recovers the original and leaves no no-original marker')
        navkit.restore(pr, 'sp_02', log=lambda *a: None)
        c.check(open(dest, 'rb').read() == b'original', 'navkit: a second restore does not delete the recovered original')

        pr3, _ours3, dest3 = setup('sp_03')
        open(dest3 + navkit.NEW_SUFFIX, 'w').close()              # a stray marker left by the old installer
        navkit.restore(pr3, 'sp_03', log=lambda *a: None)
        c.check(open(dest3, 'rb').read() == b'original' and not os.path.isfile(dest3 + navkit.NEW_SUFFIX),
                'navkit: restore clears a stale no-original marker left beside the backup')
        navkit.restore(pr3, 'sp_03', log=lambda *a: None)
        c.check(open(dest3, 'rb').read() == b'original',
                'navkit: after the stale marker is cleared, a further restore leaves the original intact')
    finally:
        navkit.game_running = saved_gr


def stockmp_case(c, work):
    sys.path.insert(0, os.path.join(SUITE, 'moh-stockmp-ai'))
    import stockmp
    import pipeline
    game = os.path.join(work, 'game')
    paths = pipeline.Paths('mp_test', opfor=work, work=os.path.join(work, 'stockmp'), game=game)
    out = os.path.join(paths.root, 'out', 'game')
    rel = 'Output/win32/ai/levels/MP_TEST/infantry/pathdata.hpd'      # uppercase level directory
    os.makedirs(os.path.dirname(os.path.join(out, rel)), exist_ok=True)
    open(os.path.join(out, rel), 'wb').write(b'built')
    os.makedirs(os.path.dirname(os.path.join(game, rel)), exist_ok=True)
    dst = os.path.join(game, rel)
    open(dst, 'wb').write(b'original')
    with contextlib.redirect_stdout(io.StringIO()):
        stockmp.install(paths, ['install', 'mp_test', '--apply'])
        shutil.rmtree(out, ignore_errors=True)                         # build outputs gone
        stockmp.install(paths, ['install', 'mp_test', '--revert'])
    c.check(open(dst, 'rb').read() == b'original' and not os.path.isfile(dst + stockmp.APPLIED),
            'stock-MP: revert finds an uppercase level directory via the install record (case-insensitive)')


def fbrb_old_manifest(c, work):
    fbrb = os.path.join(SUITE, 'moh-fbrb-tool', 'FbrbTool.exe')
    if not os.path.isfile(fbrb):
        subprocess.run(['cmd', '/c', os.path.join(SUITE, 'moh-fbrb-tool', 'build.bat')], capture_output=True)
    if not os.path.isfile(fbrb):
        c.check(False, 'FbrbTool.exe is built')
        return
    d = os.path.join(work, 'oldmanifest')
    os.makedirs(os.path.join(d, 'Levels', 'Test'))
    open(os.path.join(d, 'Levels', 'Test', 'Test.dbx'), 'wb').write(b'0123456789')     # 10 bytes
    manifest = os.path.join(d, 'fbrb.manifest')
    open(manifest, 'w').write('# fbrb.manifest - entry order matters. name|type|flags(hex)|size2  zipped=0\n'
                              'Levels/Test/Test.dbx|<non-resource>|00010000|10\n')
    r = subprocess.run([fbrb, 'pack', d, os.path.join(work, 'ok.fbrb')], capture_output=True, text=True)
    c.check(r.returncode == 0, 'FbrbTool: a four-column manifest whose size2 matches the payload still packs',
            (r.stdout + r.stderr).strip()[-200:])
    open(manifest, 'w').write('# fbrb.manifest - entry order matters. name|type|flags(hex)|size2  zipped=0\n'
                              'Levels/Test/Test.dbx|<non-resource>|00010000|999\n')
    r = subprocess.run([fbrb, 'pack', d, os.path.join(work, 'bad.fbrb')], capture_output=True, text=True)
    c.check(r.returncode != 0 and 'older unpack' in (r.stdout + r.stderr),
            'FbrbTool: a four-column manifest with a differing size2 is rejected with a re-unpack hint',
            (r.stdout + r.stderr).strip()[-200:])


def main(argv):
    c = Checks()
    work = tempfile.mkdtemp(prefix='review_fixes2_')
    try:
        harness_missing(c, work)
        harness_baseline_case(c, work)
        navkit_restore_reinstall(c, work)
        navkit_restore_deleted(c, work)
        stockmp_case(c, work)
        fbrb_old_manifest(c, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not c.failed else f'{c.failed} check(s) failed')
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
