class_name PreviewView
extends Control
## Shows the tracker's camera preview and lets you draw on it.
##
## Modes: "none", "sample" (click a sphere to learn its colour),
## "crop" (drag a rectangle), "mask" (click polygon corners, right click to finish).

signal sample_requested(frame_x: int, frame_y: int)
signal crop_drawn(rect: Rect2i)
signal mask_drawn(points: Array)

var mode := "none"
var frame_size := Vector2(640, 480)
var config: Dictionary = {}

var _texture := ImageTexture.new()
var _has_frame := false
var _drag_start := Vector2.ZERO
var _drag_end := Vector2.ZERO
var _dragging := false
var _polygon: Array = []


func _ready() -> void:
	custom_minimum_size = Vector2(640, 480)
	TrackerClient.preview_frame.connect(_on_frame)
	mouse_filter = Control.MOUSE_FILTER_STOP


func _on_frame(image: Image) -> void:
	if _has_frame and _texture.get_size() == Vector2(image.get_size()):
		_texture.update(image)
	else:
		_texture.set_image(image)
	_has_frame = true
	queue_redraw()


## Preview pixels -> full camera frame pixels (the preview may be downscaled).
func to_frame(point: Vector2) -> Vector2:
	return Vector2(point.x / size.x * frame_size.x, point.y / size.y * frame_size.y)


func to_view(point: Vector2) -> Vector2:
	return Vector2(point.x / frame_size.x * size.x, point.y / frame_size.y * size.y)


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var pos: Vector2 = event.position
		if event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
			match mode:
				"sample":
					var p := to_frame(pos)
					sample_requested.emit(int(p.x), int(p.y))
				"crop":
					_dragging = true
					_drag_start = pos
					_drag_end = pos
				"mask":
					_polygon.append(to_frame(pos))
		elif event.button_index == MOUSE_BUTTON_LEFT and not event.pressed and _dragging:
			_dragging = false
			var a := to_frame(_drag_start)
			var b := to_frame(_drag_end)
			var rect := Rect2i(Vector2i(a.min(b)), Vector2i((a - b).abs()))
			if rect.size.x > 4 and rect.size.y > 4:
				crop_drawn.emit(rect)
		elif event.button_index == MOUSE_BUTTON_RIGHT and event.pressed and mode == "mask":
			if _polygon.size() >= 3:
				var points: Array = []
				for p in _polygon:
					points.append([int(p.x), int(p.y)])
				mask_drawn.emit(points)
			_polygon.clear()
		queue_redraw()
	elif event is InputEventMouseMotion and _dragging:
		_drag_end = event.position
		queue_redraw()


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), Color(0.05, 0.05, 0.06))
	if _has_frame:
		draw_texture_rect(_texture, Rect2(Vector2.ZERO, size), false)
	else:
		draw_string(ThemeDB.fallback_font, Vector2(20, size.y / 2), "Waiting for camera preview from the tracker...",
				HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color.WHITE)
	# Existing crop + masks from the config.
	var processing: Dictionary = config.get("processing", {})
	var crop: Dictionary = processing.get("crop", {})
	if float(crop.get("w", 0)) > 0 and float(crop.get("h", 0)) > 0:
		var a := to_view(Vector2(crop["x"], crop["y"]))
		var b := to_view(Vector2(crop["x"] + crop["w"], crop["y"] + crop["h"]))
		draw_rect(Rect2(a, b - a), Color(1, 1, 1, 0.9), false, 2.0)
	for polygon in processing.get("mask_polygons", []):
		var pts := PackedVector2Array()
		for p in polygon:
			pts.append(to_view(Vector2(p[0], p[1])))
		if pts.size() >= 3:
			draw_colored_polygon(pts, Color(1, 0, 0, 0.25))
	# In-progress drawing.
	if _dragging:
		draw_rect(Rect2(_drag_start, _drag_end - _drag_start), Color(1, 1, 0), false, 2.0)
	if _polygon.size() > 0:
		var pts := PackedVector2Array()
		for p in _polygon:
			pts.append(to_view(p))
		for i in range(pts.size()):
			draw_circle(pts[i], 4, Color(1, 0.3, 0.3))
			if i > 0:
				draw_line(pts[i - 1], pts[i], Color(1, 0.3, 0.3), 2.0)
	if mode == "sample":
		var c := size / 2.0
		draw_rect(Rect2(c - Vector2(12, 12), Vector2(24, 24)), Color(0, 1, 0), false, 1.0)
