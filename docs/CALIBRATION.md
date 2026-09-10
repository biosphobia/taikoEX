# Calibration

Open **Camera & drum calibration** from the main menu.  The tabs are in the order you
should work through them.  Everything is saved to `tracker/tracker_config.json`
(press *Save tracker config* if you want to be sure).

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

Normally nothing needs changing here.  Switch *Preview shows* to **mask** to see
exactly what the tracker sees for each colour.

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

**Distance scale.**  Hold the controller at a measured distance from the lens (a tape
measure from the front of the lens to the centre of the sphere), type the distance and
press *Calibrate distance scale now*.  This sets the focal length.  The defaults
(545 px for the narrow lens, about 420 px for the wide lens at 640x480) are close enough
to start with.

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
* *Latency compensation* - camera and processing delay subtracted from hit times.  The
  results screen shows your average timing; if it says you are consistently late, raise
  this (or set the audio offset in Settings).

## 5. Pads

The top half draws the pads from above (x right, z towards you) and the controllers as
dots; the bottom half is a side view showing how high the spheres are above the pads.
Pads flash when hit.

* **Taiko layout at controller 0 / origin** - places the four pads in a row like a real
  taiko seen from above, centred on the controller or the calibrated origin: left rim,
  left face, right face, right rim.
* **Place: C0 / C1** - hold that controller where you want the pad and press.
* **Radius** - size of each disc.  Make the faces bigger and the rims smaller if you keep
  hitting the wrong one, or move the rims further out.

Pads are flat discs with a normal vector.  The default normal `(0, 1, 0)` means you hit
downwards.  Edit `normal` in `tracker_config.json` if you want angled pads (for example
`[0, 0.8, -0.6]` for a pad you strike forwards and down).

## Accuracy notes

* Depth (distance from the camera) is measured from the sphere's size.  A 45 mm sphere is
  only ~8 px across at 1.5 m with a 640x480 camera, so depth noise grows quickly with
  distance.  Keep the camera 1 - 1.5 m away and use the narrow lens setting.
* Left/right and up/down positions are far more accurate than depth, and downward strokes
  are what the pads detect, so timing stays accurate even when depth is noisy.  Put the
  camera in front of you rather than beside you so that "down" is not "towards the lens".
* 60 fps means a frame every 16 ms; the stroke's crossing time is interpolated between
  frames, so timing error is well under that.  Add *Latency compensation* for the
  camera's own delay (about one to two frames).
