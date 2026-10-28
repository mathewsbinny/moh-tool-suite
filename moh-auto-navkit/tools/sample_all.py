"""NavKit: sample several levels in one unattended run (each run starts the game, samples, closes it).

  sample_all.py [--pitch 0.2] [--levels sp_009,sp_01,...] [--smoke sp_009] [--budget <ms per frame>] [--log <file>]

For each level, navkit.py's sample (normal and high-capsule runs). With --pitch 0.2 only the level's detailed
sectors are sampled (detailed.py); a level without 0.8 m samples gets its 0.8 m runs first (the detailed choice and
the build need them). --smoke: that level first; if it fails, nothing else runs (a broken sampler stops the night's
run at once). Other failures are logged and the run goes on. A summary at the end.
"""
import functools
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import navkit  # noqa: E402

LEVELS = ['sp_01', 'sp_02', 'sp_03', 'sp_04', 'sp_003_b', 'sp_004_b', 'sp_005', 'sp_005_b', 'sp_006', 'sp_007', 'sp_008',
          'sp_008_b', 'sp_009', 'ons_mp_002', 'ons_mp_004']


def main(argv):
    opts = dict(zip(argv[1:], argv[2:]))
    pitch = float(opts.get('--pitch', 0.2))
    levels = opts['--levels'].split(',') if '--levels' in opts else list(LEVELS)
    smoke = opts.get('--smoke')
    budget = int(opts['--budget']) if '--budget' in opts else None
    logf = open(opts.get('--log', os.path.join(navkit.Project(None).work, 'navsample', 'sample_all.log')), 'a', encoding='utf-8')

    def log(*a):
        line = time.strftime('%H:%M:%S ') + ' '.join(str(x) for x in a)
        print(line, flush=True)
        logf.write(line + '\n')
        logf.flush()
    pr = navkit.Project(None)
    order = ([smoke] if smoke else []) + [lv for lv in levels if lv != smoke]
    done, failed = [], []
    t0 = time.time()
    log(f'== sample_all: {len(order)} levels at pitch {pitch}: {", ".join(order)}')
    for n, lv in enumerate(order, 1):
        t1 = time.time()
        try:
            if abs(pitch - 0.8) > 1e-6 and not pr.samples08(lv):
                log(f'[{n}/{len(order)}] {lv}: no 0.8 m samples - those first')
                if not navkit.sample(pr, lv, log=log, pitch=0.8):
                    raise navkit.NavKitError('0.8 m sampling cancelled')
            log(f'[{n}/{len(order)}] {lv}: {len(navkit.detailed_sectors(pr, lv)) if abs(pitch - 0.8) > 1e-6 else "all"} sectors')
            if not navkit.sample(pr, lv, log=log, pitch=pitch, budget_ms=budget):
                raise navkit.NavKitError('cancelled')
            done.append(lv)
            log(f'[{n}/{len(order)}] {lv}: done in {(time.time() - t1) / 60:.1f} min (total {(time.time() - t0) / 60:.0f} min)')
        except Exception as e:     # noqa: BLE001 - log and go on
            failed.append((lv, str(e)))
            log(f'[{n}/{len(order)}] {lv}: FAILED - {e}')
            logf.write(traceback.format_exc() + '\n')
            if lv == smoke:
                log('smoke test failed - stopping')
                break
    log(f'== sample_all finished in {(time.time() - t0) / 60:.0f} min: {len(done)} done, {len(failed)} failed'
        + ''.join(f'\n   {lv}: {e}' for lv, e in failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
