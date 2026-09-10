class_name GameSession
extends RefCounted
## What song / course the player picked.  Song select fills this in and
## gameplay reads it; results keep it for "play again".

static var chart: Chart = null
static var course_name: String = "Oni"


static func course() -> Chart.Course:
	return chart.course_named(course_name) if chart != null else null
