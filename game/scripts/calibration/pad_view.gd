class_name PadView
extends Control
## Top-down (x/z) and side (x/y) views of the virtual pads and the controllers.

var pads: Array = []
var scale_px_per_m := 350.0
var _flash: Dictionary = {}


func _ready() -> void:
	custom_minimum_size = Vector2(560, 360)
	TrackerClient.hit_received.connect(func(hit): _flash[hit.get("pad", "")] = 1.0)


func _process(delta: float) -> void:
	for key in _flash:
		_flash[key] = max(0.0, _flash[key] - delta * 4.0)
	queue_redraw()


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), Color(0.1, 0.1, 0.12))
	var top_h := size.y * 0.62
	var top_center := Vector2(size.x / 2.0, top_h / 2.0)
	var side_center := Vector2(size.x / 2.0, top_h + (size.y - top_h) * 0.75)
	draw_line(Vector2(0, top_h), Vector2(size.x, top_h), Color(0.3, 0.3, 0.35), 1.0)
	draw_string(ThemeDB.fallback_font, Vector2(8, 18), "top view (x right, z away from camera)", HORIZONTAL_ALIGNMENT_LEFT, -1, 13, Color(0.7, 0.7, 0.75))
	draw_string(ThemeDB.fallback_font, Vector2(8, top_h + 18), "side view (height above pads)", HORIZONTAL_ALIGNMENT_LEFT, -1, 13, Color(0.7, 0.7, 0.75))
	# Pads, widest first: a ring is drawn by punching its middle back out to the
	# background, which would erase a smaller pad already drawn underneath it.
	var ordered := pads.duplicate()
	ordered.sort_custom(func(a, b): return float(a.get("radius", 0.1)) > float(b.get("radius", 0.1)))
	for pad in ordered:
		var c: Array = pad.get("center", [0, 0, 0])
		var radius := float(pad.get("radius", 0.1)) * scale_px_per_m
		var colour := GameSkin.color("don" if pad.get("kind", "don") == "don" else "ka")
		colour.a = 0.35 + 0.6 * float(_flash.get(pad.get("id", ""), 0.0))
		var top := top_center + Vector2(float(c[0]), -float(c[2])) * scale_px_per_m
		draw_circle(top, radius, colour)
		var inner := float(pad.get("inner_radius", 0.0)) * scale_px_per_m
		if inner > 0:
			draw_circle(top, inner, Color(0.1, 0.1, 0.12))
		draw_string(ThemeDB.fallback_font, top + Vector2(-radius, radius + 14), str(pad.get("id", "")), HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Color.WHITE)
		var side := side_center + Vector2(float(c[0]), -float(c[1])) * scale_px_per_m
		draw_line(side - Vector2(radius, 0), side + Vector2(radius, 0), colour, 4.0)
	# Controllers.
	for controller in TrackerClient.state.get("controllers", []):
		if not controller.get("visible", false):
			continue
		var w: Array = controller.get("world", [0, 0, 0])
		var colour := GameSkin.color("hit_left" if int(controller.get("id", 0)) == 0 else "hit_right")
		var top := top_center + Vector2(float(w[0]), -float(w[2])) * scale_px_per_m
		var height_px: float = clamp(6.0 + float(w[1]) * 40.0, 3.0, 16.0)
		draw_circle(top, height_px, colour)
		draw_circle(top, height_px, Color.WHITE, false, 1.5)
		var side := side_center + Vector2(float(w[0]), -float(w[1])) * scale_px_per_m
		draw_circle(side, 7, colour)
