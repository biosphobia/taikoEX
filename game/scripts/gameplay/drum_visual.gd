class_name DrumVisual
extends Control
## The little taiko in the corner that lights up when you hit it.

var _flash := {"left_don": 0.0, "right_don": 0.0, "left_ka": 0.0, "right_ka": 0.0}


func _ready() -> void:
	custom_minimum_size = Vector2(200, 200)


func hit(kind: String, side: String) -> void:
	_flash[side + "_" + kind] = 1.0


func _process(delta: float) -> void:
	for key in _flash:
		_flash[key] = max(0.0, _flash[key] - delta * 5.0)
	queue_redraw()


func _draw() -> void:
	var texture := GameSkin.texture("drum")
	var center := size / 2.0
	var radius: float = min(size.x, size.y) / 2.0 - 6
	if texture:
		draw_texture_rect(texture, Rect2(Vector2.ZERO, size), false)
	else:
		draw_circle(center, radius, GameSkin.color("drum_rim"))
		draw_circle(center, radius * 0.72, GameSkin.color("drum_face"))
		draw_line(center - Vector2(0, radius), center + Vector2(0, radius), GameSkin.color("drum_rim"), 3.0)
	for key in _flash:
		if _flash[key] <= 0.0:
			continue
		var parts: PackedStringArray = key.split("_")
		var colour := GameSkin.color("hit_left" if parts[0] == "left" else "hit_right")
		colour.a = _flash[key] * 0.8
		var start_angle := PI / 2 if parts[0] == "left" else -PI / 2
		if parts[1] == "don":
			_draw_half_disc(center, radius * 0.72, start_angle, colour)
		else:
			draw_arc(center, radius * 0.86, start_angle, start_angle + PI, 32, colour, radius * 0.26)


func _draw_half_disc(center: Vector2, radius: float, start_angle: float, colour: Color) -> void:
	var points := PackedVector2Array([center])
	for i in range(33):
		var angle := start_angle + PI * i / 32.0
		points.append(center + Vector2(cos(angle), sin(angle)) * radius)
	draw_colored_polygon(points, colour)
