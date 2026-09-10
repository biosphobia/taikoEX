extends Node
## Visual style loaded from data/skins/<name>/.
##
## skin.json holds colours and sizes.  Any of the optional PNG files listed
## in TEXTURE_FILES that exists in the skin folder replaces the built-in
## procedural drawing.  Copy the "default" folder to make your own skin and
## point Settings "skin" at it.

const TEXTURE_FILES := {
	"don": "don.png", "ka": "ka.png", "don_big": "don_big.png", "ka_big": "ka_big.png",
	"roll_head": "roll_head.png", "roll_body": "roll_body.png", "balloon": "balloon.png",
	"lane": "lane.png", "background": "background.png", "drum": "drum.png",
}

const DEFAULT_STYLE := {
	"colors": {
		"background": "#1c1c22", "lane": "#2f2f36", "lane_border": "#111114",
		"judge_circle": "#4a4a55", "judge_circle_outline": "#d0d0d8",
		"don": "#f04e2c", "ka": "#2cb9e8", "note_outline": "#ffffff", "note_inner": "#ffe4c8",
		"roll": "#f2c94c", "balloon": "#f78c1e", "barline": "#8a8a96",
		"text": "#f4f4f8", "text_dim": "#a0a0aa", "good": "#ffd54a", "ok": "#e6e6ec", "bad": "#4f8cff",
		"gauge": "#f5b53a", "gauge_clear": "#ff4a6d", "gogo": "#ff9a3c",
		"drum_face": "#efe4cf", "drum_rim": "#7d4a2b", "hit_left": "#f04e2c", "hit_right": "#2cb9e8",
	},
	"sizes": {
		"note_radius": 34, "big_note_radius": 48, "lane_height": 110, "judge_x": 260,
		"measure_width": 700, "lane_y": 200,
	},
}

var style: Dictionary = DEFAULT_STYLE.duplicate(true)
var textures: Dictionary = {}
var sounds: Dictionary = {}


func _ready() -> void:
	load_skin(Settings.get_value("skin", "default"))
	load_sounds()


func load_skin(name: String) -> void:
	var folder := Paths.skins_dir().path_join(name)
	style = DEFAULT_STYLE.duplicate(true)
	var custom = Paths.read_json(folder.path_join("skin.json"), {})
	if custom is Dictionary:
		for section in custom:
			if style.has(section) and custom[section] is Dictionary:
				style[section].merge(custom[section], true)
	textures.clear()
	for key in TEXTURE_FILES:
		var path: String = folder.path_join(TEXTURE_FILES[key])
		if FileAccess.file_exists(path):
			var image := Image.load_from_file(path)
			if image:
				textures[key] = ImageTexture.create_from_image(image)


func load_sounds() -> void:
	sounds.clear()
	for name in ["don", "ka", "balloon_pop"]:
		var wav := Paths.sounds_dir().path_join(name + ".wav")
		var ogg := Paths.sounds_dir().path_join(name + ".ogg")
		if FileAccess.file_exists(wav):
			sounds[name] = AudioStreamWAV.load_from_file(wav)
		elif FileAccess.file_exists(ogg):
			sounds[name] = AudioStreamOggVorbis.load_from_file(ogg)


func color(name: String) -> Color:
	return Color.html(style["colors"].get(name, "#ff00ff"))


func size(name: String) -> float:
	return float(style["sizes"].get(name, 0))


func texture(name: String) -> Texture2D:
	return textures.get(name)
