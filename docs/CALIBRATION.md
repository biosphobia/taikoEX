# Calibration

Open **Camera & drum calibration** from the main menu.  The tabs are in the order you
should work through them.  Everything is saved to `tracker/tracker_config.json`
(press *Save tracker config* if you want to be sure).

The short version, if you have done it before:

1. **Camera** - turn auto exposure off, drop exposure and gain until the room is
   nearly black and only the spheres are bright.
2. **Detection** - press **Learn background**.  Do this before anything else.
3. **Colours** - sample each sphere's colour.
4. **Space** - two distances for the scale, then origin / right / forward for the axes.
5. **Pads** - place the drum, or do it in the 3D view where you can see it.

The left half of the screen shows the camera with the detections drawn on top.  The
status lines under it show each controller's 3D position in metres and its radius in
pixels - watch them while you tune.

## 1. Camera

* **Backend / index / resolution / fps** - pick the device.  Press *Apply camera*.
  640x480 at 60 fps is the sweet spot; 320x240 at up to 187 fps works with the
  `pseye` backend if you prefer speed over accuracy.
* **Flip / rotate** - if you mounted the camera upside down.
* **Exposure and gain** (live) - turn *Auto exposure* off, then lower exposure and gain
  until the room is nearly black and the spheres are still clearly visible.  This is the
  single most important step: bright spheres on a dark background track well at any
  distance.  If the spheres wash out completely to white, lower *LED brightness* in the
  Colours tab or lower exposure further.
* **White balance** - turn auto off so colours do not drift.

The **crop** tool (*Draw crop*, drag on the preview) limits detection to the playing
area.  The **mask** tool (*Draw mask*, click corners, right click to finish) hides
anything that is the same colour as a sphere - a lamp, a red poster, a TV.

## 2. Detection

**Learn background** is the first thing to press, before the colour and space
steps.  The tracker turns the sphere LEDs off for half a second, looks at what
in the room still matches a controller's colour - a lamp, a screen, a poster,
sunlight on a wall - and masks those areas away.  Until that is done, nothing
stops it preferring a big bright rectangle to a small sphere, and every
measurement after it would be calibrated against the wrong thing.  Press it
again whenever you move the camera, change the lighting, or change the sphere
colours.  **Clear background** undoes it.

Switch *Preview shows* to **mask** to see exactly what the tracker sees for each
colour.

* *Blur* smooths sensor noise.
* *Open / Close iterations* remove speckles and bridge gaps.  Both are symmetric so the
  blob keeps its true size (its size is the distance measurement!).
* *Fill holes* and *bright core* recover the over-exposed white centre of a sphere.
* *Min / max radius, circularity, fill ratio* decide which blobs may be a sphere.
  Motion blur stretches a fast sphere; the radius is taken from the narrow side of the
  blob so blurred strokes still measure correctly.
* *Track reach* - blobs close to the last known position win against a bigger blob
  elsewhere.
* *Radius offset* - fine adjustment if the measured radius is consistently off.

## 3. Colours

Pick the controller to edit (0 = left hand, 1 = right hand).

* **Sphere colour** - the LED colour sent to the controller.  Magenta and cyan are the
  best pair: far apart in hue and unlike skin, wood and most rooms.
* **Sample colour** - press the button under the preview (or *Sample colour from preview
  centre*), then click on the sphere.  The HSV range is set from those pixels with a
  margin.  Repeat for the other controller.
* The six **H / S / V** sliders let you widen or narrow the range by hand.  Hue can wrap
  around (min 170, max 10 is a valid red range).
* **Position smoothing** - only affects the displayed / pad-placement position; hit
  timing always uses the raw position so smoothing never adds latency.
* **Bind to Bluetooth address** - if you have more than two controllers around.

## 4. Space

**Distance scale.**  This takes **two** measurements, not one.  Hold the
controller at about 0.7 m from the lens (a tape measure from the front of the
lens to the middle of the sphere), type that distance and press *Add distance
sample*.  Then do it again at about 1.5 m.

Two distances are needed because a glowing sphere always measures about a pixel
wider than it really is - the blended pixels around its edge pass the colour
threshold too - and one measurement cannot tell that constant apart from the
focal length.  With two, the tracker solves for both and reports them.  Skip the
second sample and every position you get afterwards is scaled by roughly ten per
cent, which is enough to put the drum in the wrong place.

The defaults (545 px for the narrow lens, about 420 px for the wide lens at
640x480) are close enough to start with if you have no tape measure.

**Playing space.**  The camera can be anywhere - on a shelf to the side, on the floor
looking up, tilted.  Three captures define your own axes:

1. Hold the controller where the *centre of the drum* should be (roughly belly height,
   an arm's length in front of you).  Press *Capture ORIGIN*.
2. Move it about 40 cm to your *right*, same height.  *Capture RIGHT*.
3. Back to the centre, then about 40 cm *towards the camera* (forward), same height.
   *Capture FORWARD*.

After the third capture "world: calibrated" appears and the positions shown are now
"x = your right, y = up, z = away from the camera".  Re-do this whenever the camera
moves.

**Hit detection.**

* *Min stroke speed* - how fast the sphere must cross a pad.  Raise it if resting your
  hands near the drum triggers hits.
* *Re-arm height* - how far the sphere must rise above a pad before it can hit again.
* *Cooldown* - minimum time between two hits of the same hand.
* *Latency compensation* - camera and processing delay subtracted from hit times.
  There are two of these: one for hits timed from the camera and one for hits
  timed from the controller's accelerometer, which barely lag at all.  The
  results screen shows your average timing; if it says you are consistently
  late, raise them (or set the audio offset in Settings).
* *Use accelerometer* - on by default when a controller is connected over
  Bluetooth.  The IMU feels the stroke stop, so the timing of a hit no longer
  depends on the camera seeing the depth of it.  This is what makes a camera on
  the floor or up on a shelf usable at all.  See
  [TRACKING.md](TRACKING.md#deciding-when-a-hit-happened).

## 5. Pads

The top half draws the pads from above (x right, z towards you) and the controllers as
dots; the bottom half is a side view showing how high the spheres are above the pads.
Pads flash when hit.

* **Taiko layout at controller 0 / origin** - rebuilds the drum, centred on the
  controller or the calibrated origin.  The default is one drum: a face for
  *don* with a rim around it for *ka*, and the hand that strikes decides left
  from right.  The `four_pads` style instead puts left rim, left face, right
  face and right rim in a row; it is easier to aim at deliberately but needs a
  closer camera.  See
  [TRACKING.md](TRACKING.md#telling-don-from-ka) for why the single drum is the
  default.
* **Place: C0 / C1** - hold that controller where you want the pad and press.
* **Radius** - size of each disc or ring.  Make the face bigger if kas are
  landing as dons.

The **3D view** on the main menu does the same job with the drum drawn in front
of you: nudge it around with the keyboard, watch your controllers move in real
time, and see where the camera is sitting relative to you.

Pads are flat discs with a normal vector.  The default normal `(0, 1, 0)` means you hit
downwards.  Edit `normal` in `tracker_config.json` if you want angled pads (for example
`[0, 0.8, -0.6]` for a pad you strike forwards and down).

## Accuracy notes

The numbers below are measured, not guessed:
`python tools/evaluate_tracking.py` runs the whole pipeline against known truth
in five simulated rooms.  [TRACKING.md](TRACKING.md#what-to-expect) has the
table.

* Depth (distance from the camera) is measured from the sphere's size.  A 45 mm sphere is
  only ~8 px across at 1.5 m with a 640x480 camera, so depth noise grows quickly with
  distance.  Keep the camera 1 - 1.5 m away and use the narrow lens setting.
* Left/right and up/down positions are far more accurate than depth, and downward strokes
  are what the pads detect, so timing stays accurate even when depth is noisy.  Put the
  camera in front of you rather than beside you so that "down" is not "towards the lens".
* 60 fps means a frame every 16 ms; the stroke's crossing time is interpolated between
  frames, so timing error is well under that.  Add *Latency compensation* for the
  camera's own delay (about one to two frames).
