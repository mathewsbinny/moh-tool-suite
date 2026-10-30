"""NavKit: the Medal of Honor (2010) multiplayer soldier that path data is built for - one place for the sizes every
NavKit tool uses (sampling capsule, walkable slope and step, headroom, water, node heights).

Source: common_mp's Objects/Soldiers/Common/DefaultSoldierPhysics (Physics.CharacterPhysicsData and its states),
the physics every MP kit uses (US / RU Assault, Demolition, Recon):

    PhysicalRadius 0.35        MaxAscendAngle 45          WaterDepthLimit 1.4 (SwimmingStateData BodyUnderWater 1.4)
    Stand: Height 1.8, StepHeight 0.3, eyes 1.6          Crouch: Height 1.15, StepHeight 0.3, eyes 1.0
    JumpStateData JumpHeight 0.65                         OnGroundStateData AllowedDistanceFromGround 0.5
    LadderAcceptAngle 30, LadderAcceptAnglePitch -30

(BFBC2, which NavKit was first written for, used Kynapse's infantry settings instead: radius 0.4, StepMax 0.28,
EntityHeight 1.9, GroundSlopeMax 40 - MOH's MP client has no Kynapse; NavKit's output feeds OPFOR's own bots.)

    python mohsoldier.py <OPFOR root>     compare these values with the game copy's DefaultSoldierPhysics
"""
import glob
import math
import os
import re
import sys

RADIUS = 0.35              # PhysicalRadius
HEIGHT = 1.8               # standing pose Height
CROUCH_HEIGHT = 1.15       # crouching pose Height
STEP = 0.3                 # StepHeight (both poses)
SLOPE = 45.0               # MaxAscendAngle (deg): steeper ground is not climbed
JUMP = 0.65                # JumpHeight: what a jump clears
WATER_DEPTH = 1.4          # WaterDepthLimit: deeper water is swum, not walked

# Derived (NavKit's conventions; every tool takes them from here so files stay consistent)
Y_OFFSET = HEIGHT / 2      # path-data outline and node heights: ground + half the soldier's height
AREA_Y_MAX = round(0.8 * HEIGHT, 3)   # an area's ground may rise this much inside one area (Kynapse format convention)
HIGH_STEP = JUMP           # the high-capsule run: obstacles a jump clears (BFBC2: 0.6)
HEADROOM = round(HEIGHT + 0.1, 3)     # ground with a surface lower than this above it (and over HEADROOM_MIN) is too low


def link_step(pitch):
    """Largest height step between straight neighbouring cells pitch apart that a route edge joins: the slope limit
    over one cell, and never less than a step the soldier takes (0.8 m: 0.80; 0.2 m: 0.30)."""
    return round(max(STEP, pitch * math.tan(math.radians(SLOPE))), 3)


def capsule_axis(bottom):
    """(lowest, highest) sphere-centre heights above the surface of the sampling capsule whose bottom is lifted
    bottom (STEP or HIGH_STEP) off the floor: the soldier's capsule. (BFBC2's in-game sampler fitted 0.95 / 1.15
    for its 0.28 / 0.6 runs; MOH has no in-game sampler to fit against, so the physics' nominal capsule is used.)"""
    return bottom + RADIUS, HEIGHT - RADIUS


FIELDS = {'PhysicalRadius': RADIUS, 'MaxAscendAngle': SLOPE, 'WaterDepthLimit': WATER_DEPTH, 'JumpHeight': JUMP}


def read_physics(root):
    """{name: value} from the game copy's DefaultSoldierPhysics (unpacked by navkit.py prepare into
    work\\navkit\\common), plus 'StandHeight', 'CrouchHeight', 'StepHeight'; None when not unpacked."""
    paths = sorted(glob.glob(os.path.join(root, 'work', 'navkit', 'common', 'common_mp*', 'Objects', 'Soldiers',
                                          'Common', 'DefaultSoldierPhysics.xml')), key=lambda p: '.patch' not in p)
    if not paths:
        return None
    t = open(paths[0], encoding='utf-8').read()
    out = {}
    for k in FIELDS:
        m = re.search(r'name="%s">([-\d.]+)<' % k, t)
        if m:
            out[k] = float(m.group(1))
    for m in re.finditer(r'type="Physics\.CharacterPoseData">(.*?)</instance>', t, re.S):
        body = m.group(1)
        pose = re.search(r'name="PoseType">(\w+)<', body).group(1)
        out[pose + 'Height'] = float(re.search(r'name="Height">([-\d.]+)<', body).group(1))
        out['StepHeight'] = float(re.search(r'name="StepHeight">([-\d.]+)<', body).group(1))
    return out


def differences(root):
    """[(name, game value, NavKit value)] where the game copy's soldier physics differ from the constants above;
    None when the physics are not unpacked."""
    phys = read_physics(root)
    if phys is None:
        return None
    want = dict(FIELDS, StandHeight=HEIGHT, CrouchHeight=CROUCH_HEIGHT, StepHeight=STEP)
    return [(k, phys[k], v) for k, v in want.items() if k in phys and abs(phys[k] - v) > 1e-6]


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 1
    d = differences(argv[1])
    if d is None:
        print('DefaultSoldierPhysics not unpacked (run navkit.py prepare on a level first)')
        return 1
    for k, game, ours in d:
        print(f'{k}: game {game:g}, NavKit {ours:g}')
    print('the same' if not d else f'{len(d)} difference(s)')
    return 1 if d else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
