extends Node
## Checks GitHub releases for a newer build and installs it.
##
## Flow: query releases/latest -> compare build number with version.txt ->
## download the windows zip -> unpack to user://update/ -> write a batch
## file that waits for the game to close, copies the new files over the
## install folder and starts the game again.  If the game is closed while an
## update is staged, the same batch runs without restarting the game, so an
## update never needs more than "close the game".
##
## Files the player owns are never overwritten: data/settings.json and
## tracker/tracker_config.json.  Everything else in the release is refreshed.
## Everything the batch does is written to user://update/apply_update.log.

signal status_changed(text: String)
signal update_ready()

enum State { IDLE, CHECKING, DOWNLOADING, READY, UP_TO_DATE, FAILED, DISABLED }

const ASSET_SUFFIX := "windows-x64.zip"
const KEEP_FILES := ["settings.json", "tracker_config.json"]
const TRACKER_EXE := "taiko_tracker.exe"

var state: State = State.IDLE
var latest_tag := ""
var status_text := ""
var _http: HTTPRequest
var _stage_dir := ""
var _asset_size := 0
var _applied := false


func _ready() -> void:
	_http = HTTPRequest.new()
	_http.timeout = 0            # a 100 MB download on a slow line takes what it takes
	add_child(_http)
	if OS.has_feature("editor") or OS.get_name() != "Windows":
		_set_state(State.DISABLED, "Updates: disabled (development build)")
		return
	if Settings.get_value("update.check_on_startup", true):
		check_for_update()


func _process(_delta: float) -> void:
	if state == State.DOWNLOADING and Engine.get_process_frames() % 20 == 0:
		var done := _http.get_downloaded_bytes()
		var text := "Updates: downloading %s... %d MB" % [latest_tag, done / 1048576]
		if _asset_size > 0:
			text += " of %d MB" % (_asset_size / 1048576)
		if text != status_text:
			status_text = text
			status_changed.emit(text)


func _notification(what: int) -> void:
	# Closing the game with an update staged installs it on the way out.
	if what == NOTIFICATION_WM_CLOSE_REQUEST and state == State.READY and not _applied:
		_launch_apply_script(false)


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


func _on_release_info(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS:
		_set_state(State.FAILED, "Updates: could not reach GitHub (result %d)" % result)
		return
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
			_asset_size = int(asset.get("size", 0))
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


func _on_downloaded(result: int, code: int, _headers: PackedStringArray, _body: PackedByteArray) -> void:
	_http.download_file = ""
	if result != HTTPRequest.RESULT_SUCCESS:
		_set_state(State.FAILED, "Updates: download broke off (result %d) - it is retried next start" % result)
		return
	if code != 200:
		_set_state(State.FAILED, "Updates: download returned %d" % code)
		return
	_stage_dir = ProjectSettings.globalize_path("user://update/stage")
	if not _unzip(ProjectSettings.globalize_path("user://update/update.zip"), _stage_dir):
		_set_state(State.FAILED, "Updates: could not unpack")
		return
	_set_state(State.READY, "Update %s ready - it installs when you close the game (or press Restart to install)" % latest_tag)
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
	_launch_apply_script(true)
	get_tree().quit()


## Write and start the batch file that installs the staged files once this
## process has exited.  The tracker is stopped first: a tracker left running
## keeps its exe and DLLs locked, and robocopy would otherwise wait on them.
func _launch_apply_script(restart: bool) -> void:
	_applied = true
	var install := Paths.install_dir()
	var exe := OS.get_executable_path().get_file()
	var script_path := ProjectSettings.globalize_path("user://update/apply_update.bat")
	var log_path := ProjectSettings.globalize_path("user://update/apply_update.log")
	var excludes := " ".join(KEEP_FILES)
	var pid := OS.get_process_id()
	var lines := [
		"@echo off",
		"echo Installing TaikoEX %s > \"%s\"" % [latest_tag, log_path],
		"echo Installing TaikoEX %s - this window closes by itself." % latest_tag,
		":wait",
		"tasklist /FI \"PID eq %d\" 2>NUL | find \"%d\" >NUL" % [pid, pid],
		"if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto wait)",
		"taskkill /F /IM %s >NUL 2>&1" % TRACKER_EXE,
		"timeout /t 1 /nobreak >NUL",
		"robocopy \"%s\" \"%s\" /E /IS /IT /R:5 /W:2 /NFL /NDL /NJH /XF %s >> \"%s\"" % [_stage_dir, install, excludes, log_path],
		"if errorlevel 8 (echo COPY FAILED - see the log & pause)",
	]
	if restart:
		lines.append("start \"\" \"%s\"" % install.path_join(exe))
	lines.append("exit")
	var file := FileAccess.open(script_path, FileAccess.WRITE)
	file.store_string("\r\n".join(lines))
	file.close()
	OS.create_process("cmd.exe", PackedStringArray(["/c", script_path]), false)


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
