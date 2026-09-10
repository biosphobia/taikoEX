from taiko_tracker.tja import parse_tja_notes

CHART = """TITLE:test
BPM:120
OFFSET:-1.0
COURSE:Oni
#START
1020,
3,
#BPMCHANGE 240
1111,
#END
"""


def test_note_times():
    notes = parse_tja_notes(CHART)
    times = [round(n.time, 3) for n in notes]
    kinds = [n.kind for n in notes]
    assert kinds == ["don", "ka", "don", "don", "don", "don", "don"]
    # measure 1 at 120 bpm = 2 s, 4 cells -> 0.5 s each
    assert times[:2] == [1.0, 2.0]
    assert times[2] == 3.0 and notes[2].big
    # measure 3 at 240 bpm = 1 s, four cells of 0.25 s
    assert times[3:] == [5.0, 5.25, 5.5, 5.75]
