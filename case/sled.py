#!/usr/bin/env python3
"""Pi sled for the Tube Board.

A TRAY, not a box. The frame's pocket is 28 mm deep and the mount, screen and
backing board already eat 15.5 of it, so there is about 12.5 mm left behind the
backing for the Pi - and a Pi with its GPIO header is already 9.9 mm tall. A
closed case cannot fit. So this is the thinnest thing that does the three jobs
that actually matter:

  1. holds the Pi off the backing board so nothing shorts on it,
  2. stops a tug on either cable pulling a connector off the board,
  3. lets the SD card come out without dismantling the frame.

Everything is measured from the Raspberry Pi mechanical drawing for the 3 A+:
65 x 56 board, 4x M2.5 holes 3.5 mm in from each edge, connectors on one 65 mm
edge (micro-USB at 10.6, HDMI at 32.0), microSD on the underside of the opposite
edge.

    python3 sled.py        # writes sled.stl, clamp.stl, and pi_dummy.stl
"""
import math
from manifold3d import Manifold, CrossSection

# ---------------------------------------------------------------- the Pi
PI_W, PI_H, PI_T = 65.0, 56.0, 1.4
HOLE_IN = 3.5
HOLES = [(HOLE_IN, HOLE_IN), (HOLE_IN, PI_H - HOLE_IN),
         (PI_W - HOLE_IN, HOLE_IN), (PI_W - HOLE_IN, PI_H - HOLE_IN)]
GPIO_H = 8.5                    # header above the PCB - the tallest thing on it
MICRO_USB_X, HDMI_X = 10.6, 32.0

# ---------------------------------------------------------------- the sled
MARGIN = 4.0                    # sled overhang around the board
BASE_T = 2.0                    # base plate - thin, because depth is the enemy
STANDOFF_H = 3.0                # clears the microSD and the underside parts
STANDOFF_D = 6.0
SCREW_D = 2.2                   # pilot for a self-tapping M2.5
RAIL_H = 5.0                    # side rails: rigidity and a bump guard
RAIL_T = 2.0
W = PI_W + 2 * MARGIN
H = PI_H + 2 * MARGIN
PI_X, PI_Y = MARGIN, MARGIN     # where the board sits on the sled

TOTAL = BASE_T + STANDOFF_H + PI_T + GPIO_H


def rbox(w, h, d, r=1.5):
    """Rounded-corner box, corners in plan."""
    if r <= 0:
        return Manifold.cube([w, h, d])
    return Manifold.extrude(
        CrossSection.square([w - 2 * r, h - 2 * r]).offset(r, 0, 0, 12).translate([r, r]),
        d)


def cyl(d, h, seg=48):
    return Manifold.cylinder(h, d / 2, d / 2, seg)


def build_sled():
    base = rbox(W, H, BASE_T, 3.0)

    # --- standoffs, with a pilot hole for an M2.5 self-tapper
    for hx, hy in HOLES:
        x, y = PI_X + hx, PI_Y + hy
        base += cyl(STANDOFF_D, BASE_T + STANDOFF_H).translate([x, y, 0])
        base -= cyl(SCREW_D, BASE_T + STANDOFF_H + 1).translate([x, y, -0.5])

    # --- side rails on the two edges with no connectors, for stiffness.
    #     The connector edge (y=0) and the SD edge (y=H) stay open.
    for x0 in (0.0, W - RAIL_T):
        base += rbox(RAIL_T, H, BASE_T + RAIL_H, 1.0).translate([x0, 0, 0])

    # --- SD card access: a slot through the base under the card's edge, so a
    #     fingernail can push it out with the sled still screwed down.
    sd_w, sd_d = 22.0, 14.0
    base -= rbox(sd_w, sd_d + 2, BASE_T + 2, 2.0).translate(
        [PI_X + PI_W / 2 - sd_w / 2, H - sd_d, -1])

    # --- ventilation under the board: it is a sealed frame, give the heat a path
    for i in range(4):
        for j in range(3):
            base -= rbox(9, 5, BASE_T + 2, 1.5).translate(
                [PI_X + 8 + i * 13, PI_Y + 13 + j * 11, -1])

    # --- fixing to the backing board: four countersunk holes, clear of the Pi
    for fx, fy in ((2 + RAIL_T, H / 2), (W - 2 - RAIL_T, H / 2),
                   (W / 2 - 14, 2.2), (W / 2 + 14, 2.2)):
        base -= cyl(3.4, BASE_T + 2).translate([fx, fy, -1])
        base -= Manifold.cylinder(1.6, 3.4, 1.7, 32).translate([fx, fy, BASE_T - 1.6])

    # --- posts for the cable clamp, outboard of the connector edge
    for px in (PI_X + 2, PI_X + PI_W - 2):
        base += cyl(5.0, BASE_T + RAIL_H).translate([px, 2.6, 0])
        base -= cyl(SCREW_D, BASE_T + RAIL_H + 1).translate([px, 2.6, -0.5])
    return base


def build_clamp():
    """A bar that screws down over both cables just outside the connectors, so a
    pull on the lead is taken by the sled and not by the socket on the board."""
    length = PI_W - 4 + 5
    bar = rbox(length, 7.0, 4.0, 1.5)
    # relief arches over each cable: micro-USB then HDMI
    for cx, cw in ((MICRO_USB_X, 9.0), (HDMI_X, 16.0)):
        x = cx + PI_X - (PI_X + 2) + 2.5
        bar -= rbox(cw, 9.0, 2.6, 1.0).translate([x - cw / 2, -1, -0.5])
    for sx in (2.5, length - 2.5):
        bar -= cyl(2.8, 5).translate([sx, 4.3, -0.5])
    return bar


def build_pi_dummy():
    """Not printed - it is in the viewer so the fit reads at a glance."""
    pi = rbox(PI_W, PI_H, PI_T, 3.0)
    pi += rbox(51.0, 5.1, GPIO_H, 0.6).translate([PI_W - 3.5 - 51 + 0.5, PI_H - 5.6, PI_T])
    pi += rbox(17.3, 13.0, 7.5, 0.5).translate([PI_W - 17.3, PI_H / 2 - 6.5, PI_T])   # USB-A
    pi += rbox(7.6, 5.6, 2.6, 0.4).translate([MICRO_USB_X - 3.8, -1.5, PI_T])          # micro-USB
    pi += rbox(15.0, 5.8, 5.7, 0.4).translate([HDMI_X - 7.5, -1.5, PI_T])              # HDMI
    return pi.translate([PI_X, PI_Y, BASE_T + STANDOFF_H])


if __name__ == "__main__":
    for name, solid in (("sled", build_sled()),
                        ("clamp", build_clamp().translate([PI_X + 2 - 2.5, 0, 40])),
                        ("pi_dummy", build_pi_dummy())):
        m = solid.to_mesh()
        v, t = m.vert_properties[:, :3], m.tri_verts
        with open(f"{name}.stl", "w") as f:
            f.write(f"solid {name}\n")
            for a, b, c in t:
                p, q, r = v[a], v[b], v[c]
                u1 = [q[i] - p[i] for i in range(3)]
                u2 = [r[i] - p[i] for i in range(3)]
                n = [u1[1]*u2[2]-u1[2]*u2[1], u1[2]*u2[0]-u1[0]*u2[2], u1[0]*u2[1]-u1[1]*u2[0]]
                L = math.sqrt(sum(x*x for x in n)) or 1.0
                f.write(f"facet normal {n[0]/L:.6f} {n[1]/L:.6f} {n[2]/L:.6f}\n outer loop\n")
                for w in (p, q, r):
                    f.write(f"  vertex {w[0]:.4f} {w[1]:.4f} {w[2]:.4f}\n")
                f.write(" endloop\nendfacet\n")
            f.write(f"endsolid {name}\n")
        print(f"{name}.stl  {len(t)} triangles")
    print(f"\nsled footprint : {W:.0f} x {H:.0f} mm")
    print(f"stack height   : base {BASE_T} + standoff {STANDOFF_H} + pcb {PI_T} + header {GPIO_H} = {TOTAL:.1f} mm")
