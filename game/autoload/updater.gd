extends Node
## Checks GitHub releases for a newer build and installs it.
##
## Flow: query releases/latest -> compare build number with version.txt ->
## download the windows zip -> unpack to user://update/ -> write a batch
## file that waits for the game to close, copies the new files over the
## install folder and starts the game again.
##
## Files the player owns are never overwritten: data/settings.json and
## tracker/tracker_config.json.  Everything else in the release is refreshed.

signal status_changed(text: String)
signal update_ready()

enum State { IDLE, CHECKING, DOWNLOADING, READY, UP_TO_DATE, FAILED, DISABLED }

const ASSET_SUFFIX := "windows-x64.zip"
const KEEP_FILES := ["settings.json", "tracker_config.json"]

var state: State = State.IDLE
var latest_tag := ""
var status_text := ""
var _http: HTTPRequest
var _stage_dir := ""


func _ready() -> void:
	_http = HTTPRequest.new()
	_http.timeout = 120
	add_child(_http)
	if OS.has_feature("editor") or OS.get_name() != "Windows" or Paths.version() == "dev":
		_set_state(State.DISABLED, "Updates: disabled (development build)")
		return
	if Settings.get_value("update.check_on_startup", true):
		check_for_update()


func check_for_update() -> void:
	if state == State.CHECKING or state == State.DOWNLOADING:
		return
	_set_state(State.CHECKING, "Updates: checking...")
	var repo: String = Settings.get_value("update.repo", "")
	var url := "https://api.github.com/repos/%s/releases/latest" % repo
	_http.request_completed.connect(_on_release_info, CONNECT_ONE_SHOT)
	var error := _http.request(url, PackedStringArray(["User-Agent: TaikoEX", "Accept: application/vnd.github+json"]))
	if error != OK:
		_set_state(State.FAILED, "Updates: request failed (%d)" % error)


func _on_release_info(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if code != 200:
		_set_state(State.FAILED, "Updates: GitHub returned %d" % code)
		return
	var info = JSON.parse_string(body.get_string_from_utf8())
	if not (info is Dictionary):
		_set_state(State.FAILED, "Updates: bad response")
		return
	latest_tag = str(info.get("tag_name", ""))
	if build_number(latest_tag) <= build_number(Paths.version()):
		_set_state(State.UP_TO_DATE, "Updates: up to date (%s)" % Paths.version())
		return
	var asset_url := ""
	for asset in info.get("assets", []):
		if str(asset.get("name", "")).ends_with(ASSET_SUFFIX):
			asset_url = str(asset.get("browser_download_url", ""))
	if asset_url.is_empty():
		_set_state(State.FAILED, "Updates: %s has no windows build" % latest_tag)
		return
	if not Settings.get_value("update.auto_install", true):
		_set_state(State.FAILED, "Update %s available (auto install is off)" % latest_tag)
		return
	_download(asset_url)


func _download(url: String) -> void:
	_set_state(State.DOWNLOADING, "Updates: downloading %s..." % latest_tag)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("user://update"))
	_http.download_file = ProjectSettings.globalize_path("user://update/update.zip")
	_http.request_completed.connect(_on_downloaded, CONNECT_ONE_SHOT)
	var error := _http.request(url, PackedStringArray(["User-Agent: TaikoEX"]))
	if error != OK:
		_set_state(State.FAILED, "Updates: download failed (%d)" % error)


func _on_downloaded(_result: int, code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
	_http.download_file = ""
	if code != 200:
		_set_state(State.FAILED, "Updates: download returned %d" % code)
		return
	_stage_dir = ProjectSettings.globalize_path("user://update/stage")
	if not _unzip(ProjectSettings.globalize_path("user://update/update.zip"), _stage_dir):
		_set_state(State.FAILED, "Updates: could not unpack")
		return
	_set_state(State.READY, "Update %s ready - restart to install" % latest_tag)
	update_ready.emit()


func _unzip(zip_path: String, target_dir: String) -> bool:
	var reader := ZIPReader.new()
	if reader.open(zip_path) != OK:
		return false
	for file_name in reader.get_files():
		if file_name.ends_with("/"):
			continue
		var out_path := target_dir.path_join(file_name)
		DirAccess.make_dir_recursive_absolute(out_path.get_base_dir())
		var file := FileAccess.open(out_path, FileAccess.WRITE)
		if file == null:
			reader.close()
			return false
		file.store_buffer(reader.read_file(file_name))
	reader.close()
	return true


## Runs the staged update: the game exits and a batch file copies the files.
func apply_and_restart() -> void:
	if state != State.READY:
		return
	var install := Paths.install_dir()
	var exe := OS.get_executable_path().get_file()
	var script_path := ProjectSettings.globalize_path("user://update/apply_update.bat")
	var excludes := " ".join(KEEP_FILES)
	var script := "\r\n".join([
		"@echo off",
		"echo Installing TaikoEX %s ..." % latest_tag,
		":wait",
		"tasklist /FI \"PID eq %d\" 2>NUL | find \"%d\" >NUL" % [OS.get_process_id(), OS.get_process_id()],
		"if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto wait)",
		"robocopy \"%s\" \"%s\" /E /IS /IT /NFL /NDL /NJH /NJS /XF %s >NUL" % [_stage_dir, install, excludes],
		"start \"\" \"%s\"" % install.path_join(exe),
		"exit",
	])
	var file := FileAccess.open(script_path, FileAccess.WRITE)
	file.store_string(script)
	file.close()
	OS.create_process("cmd.exe", PackedStringArray(["/c", script_path]), false)
	get_tree().quit()


## Build numbers are the last integer in a tag such as "v0.1.0-build.42" or "build-42".
static func build_number(tag: String) -> int:
	var regex := RegEx.new()
	regex.compile("(\\d+)(?!.*\\d)")
	var found := regex.search(tag)
	return int(found.get_string(1)) if found else -1


func _set_state(new_state: State, text: String) -> void:
	state = new_state
	status_text = text
	status_changed.emit(text)
	print(text)
