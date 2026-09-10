# How the tracking works

This is the part of TaikoEX that turns two glowing spheres and a cheap camera
into drum hits.  It is worth understanding because almost every setting in the
calibration screen is a knob on one of these steps.

```
 PS3 Eye ──► find the spheres ──► where are they in 3D ──► which pad, and when
             (vision.py)           (geometry.py,            (pads.py)
                                    tracking_filter.py)
                        ▲                    ▲                    ▲
                        │                    │                    │
             background learning     the controller's       the controller's
             masks out the room      accelerometer          accelerometer
                                     predicts the motion    times the hit
```

## Telling *don* from *ka*

A taiko has two sounds: **don**, the deep note from hitting the middle of the
skin, and **ka**, the dry click from hitting the rim.  On a real drum your hand
chooses between them by *where on the drum it lands*, and TaikoEX works exactly
the same way.

The default layout is one drum: a **face** disc about 44 cm across for don, and
a **rim** ring around it out to about 90 cm for ka.  When a stroke is detected,
the tracker measures how far the sphere is from the middle of the drum, in the
plane of the drum:

* inside the face radius → **don**
* between the inner and outer rim radius → **ka**
* outside everything → no hit at all

Left and right come from a different question: *which hand*.  Each controller
is bound to a colour, so the tracker always knows whether a hit came from your
left hand or your right one, and that decides `left_don` from `right_don`.
Nothing about the position has to be accurate for that - it is simply which
sphere moved.

That split matters, because the two questions have very different accuracy
requirements and the design gives each one the measurement it can rely on:

| question | answered by | how accurate that is |
| --- | --- | --- |
| left or right hand? | which colour moved | exact |
| don or ka? | distance from the drum centre, sideways | a few millimetres to a few centimetres |
| when? | the accelerometer's deceleration peak | a few milliseconds |
| how hard? | the size of that peak | fine for a loud/soft distinction |

The don/ka boundary is a 1 cm gap between two big targets, so it survives
several centimetres of error.  Compare that with the alternative four-pad
layout (`style: "four_pads"`), which puts left rim, left face, right face and
right rim in a row: it needs the hand placed correctly along one line to within
a few centimetres, which is more than a camera two metres away can promise.
Use the four-pad layout only with a close camera in front of you.

If you want a different rule entirely - a rim that is only the far half of the
drum, angled pads, three drums - the pads are plain entries in
`tracker_config.json` with a centre, a normal, an outer radius and an inner
radius.  Anything you can describe with discs and rings, the tracker will play.

## Finding the spheres

Each controller's LED is set to its own colour (magenta and cyan by default,
because they are far apart in hue and unlike skin, wood or daylight).  For each
colour the tracker thresholds the frame in HSV, then:

* **recovers the blown-out middle.**  A bright sphere is white in the centre and
  only shows its colour near the edge, so plain thresholding finds a ring.  The
  saturated white pixels next to a colour blob are merged back in.
* **measures the radius from the blob's area**, not from a circle drawn around
  it.  Area is a count of thousands of pixels, so its error averages out to a
  fraction of a pixel.  A circle around the blob would grow every time the hand
  moved during the exposure.
* **corrects for motion blur.**  A sphere smeared during the exposure is a
  capsule, whose area is `(π + 4(e−1))r²` for an axis ratio `e`; inverting that
  gives the radius the sphere would have had standing still.  Without this a
  fast stroke reads as much closer than it is, which is the difference between
  a hit and a miss.
* **notices when the sphere is not all there.**  Comparing the blob against the
  capsule it should be says how much has been bitten out of it by a hand or the
  drum edge.

## Learning the room

Real rooms contain things that look like a glowing sphere: a lamp, a television,
a magenta poster, sunlight on a wall.  Rather than making you draw polygons
around each one, press **Learn background**.  The tracker turns the sphere LEDs
off over Bluetooth, watches for a moment, and whatever still matches a
controller's colour must be part of the room - so it is masked out.  Then the
LEDs come back on.

Do this **first**, before the other calibration steps.  Until the room is
masked, the tracker has no reason to prefer a small sphere over a big bright
rectangle, and everything that follows depends on it measuring the sphere.

## From pixels to metres

The sphere is 45 mm across, so how big it looks says how far away it is, and
where it sits in the frame says in which direction.  Both need the lens's focal
length, and there is a catch: a glowing sphere always measures about a pixel
*wider* than it is, because the blended pixels at its edge pass the colour
threshold too.  One distance measurement cannot separate that constant from the
focal length, so the calibration takes **two** distances (about 0.7 m and 1.5 m)
and solves for both.  Skipping the second one leaves every position scaled by
about ten per cent.

Then the three-point world calibration - origin, right, forward - turns camera
coordinates into *your* coordinates.  After it, "up" is up regardless of where
the camera is: on a shelf, off to one side, on the floor pointing at the
ceiling.

## Why distance is the hard part

A camera measures direction beautifully and distance badly, and the gap is
enormous.  At 1.5 m a 45 mm sphere is about 8 pixels across on a 640×480 PS3
Eye.  Half a pixel of error in where its centre sits is about **one millimetre**
sideways.  Half a pixel of error in its *radius* is about **seven centimetres**
in depth.

So the position filter does not treat the three axes alike.  It is a
constant-velocity Kalman filter whose measurement noise is an ellipsoid
stretched along the line from the lens to the sphere, with both radii computed
from the pinhole model:

```
sigma sideways = distance × pixel noise / focal length
sigma along the ray = |d(distance)/d(radius)| × radius noise
```

Depth gets averaged hard, the sideways axes are left almost untouched, and
because the ellipsoid is expressed in world coordinates through the calibration,
a camera up on a shelf automatically puts its blurry axis where it really
points.  Two guards sit on top: a measurement far outside the prediction is
skipped rather than followed (a hand crossing in front makes a sphere look half
the size and so twice as far away), and a radius that changes faster than the
hand could possibly move marks the distance as unusable for that frame while
the direction is still used.

Turn the rays on in the 3D view (**G**) to watch this happen: the reading sits
right on the sphere sideways and slides back and forth along the line of sight.

## Deciding when a hit happened

Nothing is actually struck when you drum in the air, so a "hit" is the moment
your hand *stops* - the bottom of the stroke, which is also the moment you would
have heard a drum.  There are two ways to find it, and the tracker prefers the
first:

**From the accelerometer** (`hits.use_accelerometer`, on by default).  The IMU
is inside your hand.  It feels the stroke stop whether or not the camera can
make it out, which matters most when the camera looks down the same line your
hand travels - a camera on the floor, or up on a shelf - because that is exactly
the direction a camera measures worst.  The hit is the *peak* of the
deceleration, located to a fraction of a sample by fitting a parabola through
the three readings around it, and it only counts if the same sensor saw the hand
drive at the pad first.  The camera still decides which pad, which is a sideways
question it answers well.

**From the motion** (`hits.mode: "stroke"`, used when there is no IMU).  The hit
is where the speed towards the pad falls through zero, interpolated between
frames.  It needs no absolute accuracy in the depth, only the turn-around.

There is also `hits.mode: "plane"`, which fires when the sphere crosses the pad
surface going down.  It is the sharpest of the three and the least forgiving,
because it depends entirely on the depth estimate.  Use it with a close camera
pointing straight at you.

After a hit, that pad has to be re-armed by lifting away from where you struck -
measured *relative* to the height read at the hit, not to the pad surface, so a
camera whose idea of height is off by several centimetres still re-arms
correctly.

## What to expect

`python tools/evaluate_tracking.py` scores the whole pipeline against the truth
in five simulated rooms, each with its own lamps, screens, sunlight, sensor
noise and an arm that sweeps across the spheres.  Representative numbers on a
640×480 camera at 60 fps:

| room | camera | position error | strokes | wrong pad | timing spread |
| --- | --- | --- | --- | --- | --- |
| clean | 1.4 m in front | 22 mm | 24/24 | 0 | ±12 ms |
| living room | 1.6 m, lamp + poster + a face | 68 mm | 23/24 | 0 | ±6 ms |
| sunny | 1.7 m, window glare, waving arm | 41 mm | 24/24 | 0 | ±12 ms |
| floor | 1.2 m, on the floor looking up | 299 mm | 18/24 | 0 | ±4 ms |
| far shelf | 3.3 m, high and 35° to the side | 936 mm | 19/24 | 1 | ±9 ms |

Three things are worth drawing out.

Timing holds up even where the position does not.  On the floor the camera is
staring straight along the direction the hands move, so its idea of where they
are is thirty centimetres out - and the strokes are still timed to within four
milliseconds, because that number comes from the accelerometer.

Don and ka are almost never confused, one case in 120 across all five rooms,
because that decision is a sideways measurement across a target twenty
centimetres wide.

Position error grows sharply with distance.  At 3.3 m the spheres are under four
pixels across and no amount of filtering fixes that.  **Put the camera between
one and two metres away**, and use the narrow lens setting (the red dot on the
lens ring) - it is the single biggest thing you can do for accuracy.

Run it yourself with `python tools/evaluate_tracking.py`, and watch it with
`python tools/record_setups.py --godot <path to godot>`, which records the 3D
view in each room.
