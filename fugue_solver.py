from music21 import stream, note
from z3 import *

VOICE_NAMES = {
    0: "Top Voice",
    1: "Middle Voice",
    2: "Bass Voice",
}
PERFECT_INTERVALS = {0, 7}
UPPER_VOICE_CONSONANCES = {0, 3, 4, 5, 7, 8, 9}
BASS_CONSONANCES = {0, 3, 4, 7, 8, 9}

def clone_stream(m21_stream):
    new_stream = stream.Stream()
    if not m21_stream:
        return new_stream

    for n in m21_stream.flatten().notesAndRests:
        if n.isRest:
            new_stream.append(note.Rest(quarterLength=n.quarterLength))
        else:
            new_stream.append(note.Note(n.pitch.midi, quarterLength=n.quarterLength))

    return new_stream

def concatenate_streams(m21_streams):
    new_stream = stream.Stream()
    for m21_stream in m21_streams:
        for n in clone_stream(m21_stream).flatten().notesAndRests:
            new_stream.append(n)
    return new_stream

def make_rest_stream(total_16ths):
    rest_stream = stream.Stream()
    rest_stream.append(note.Rest(quarterLength=total_16ths * 0.25))
    return rest_stream

def get_stream_length_16ths(m21_stream):
    if not m21_stream:
        return 0
    return sum(int(n.quarterLength / 0.25) for n in m21_stream.flatten().notesAndRests)

def _stream_to_pitch_grid(m21_stream, total_16ths):
    grid = [-1] * total_16ths
    if not m21_stream:
        return grid

    offset = 0
    for n in m21_stream.flatten().notesAndRests:
        dur_16ths = int(n.quarterLength / 0.25)
        pitch = -1 if n.isRest else n.pitch.midi
        if pitch != -1:
            for tick in range(offset, min(offset + dur_16ths, total_16ths)):
                grid[tick] = pitch
        offset += dur_16ths

    return grid

def _build_meter_info(meter_info=None):
    meter_info = meter_info or {}
    numerator = max(1, int(meter_info.get("numerator", 4)))
    denominator = max(1, int(meter_info.get("denominator", 4)))
    beat_16ths = max(1, int(round(16.0 / denominator)))
    measure_16ths = max(beat_16ths, numerator * beat_16ths)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "beat_16ths": beat_16ths,
        "measure_16ths": measure_16ths,
    }

def _format_location(offset_16ths, meter_info=None):
    meter = _build_meter_info(meter_info)
    measure = (offset_16ths // meter["measure_16ths"]) + 1
    beat_offset = offset_16ths % meter["measure_16ths"]
    beat = 1 + (beat_offset / float(meter["beat_16ths"]))
    beat_text = str(int(beat)) if beat.is_integer() else f"{beat:.2f}".rstrip("0").rstrip(".")
    return f"Measure {measure}, beat {beat_text}"

def _is_strong_beat_consonance(interval, voice_a, voice_b):
    if 2 in (voice_a, voice_b):
        return interval in BASS_CONSONANCES
    return interval in UPPER_VOICE_CONSONANCES

def _same_direction(motion_a, motion_b):
    return motion_a != 0 and motion_b != 0 and ((motion_a > 0 and motion_b > 0) or (motion_a < 0 and motion_b < 0))

def _is_weak_dissonance_allowed(moving_grid, fixed_grid, offset_16ths):
    if offset_16ths <= 0 or offset_16ths >= len(moving_grid) - 1:
        return False

    prev_pitch = moving_grid[offset_16ths - 1]
    curr_pitch = moving_grid[offset_16ths]
    next_pitch = moving_grid[offset_16ths + 1]
    if -1 in (prev_pitch, curr_pitch, next_pitch):
        return False

    if fixed_grid[offset_16ths - 1] != fixed_grid[offset_16ths] or fixed_grid[offset_16ths] != fixed_grid[offset_16ths + 1]:
        return False

    motion_in = curr_pitch - prev_pitch
    motion_out = next_pitch - curr_pitch
    if motion_in == 0 or motion_out == 0:
        return False

    stepwise = abs(motion_in) <= 2 and abs(motion_out) <= 2
    if not stepwise:
        return False

    return _same_direction(motion_in, motion_out) or prev_pitch == next_pitch

def _stream_to_events(m21_stream):
    events = []
    if not m21_stream:
        return events

    offset = 0
    for n in m21_stream.flatten().notesAndRests:
        dur_16ths = int(n.quarterLength / 0.25)
        if dur_16ths <= 0:
            continue

        events.append({
            "start": offset,
            "end": offset + dur_16ths,
            "pitch": -1 if n.isRest else n.pitch.midi,
            "isRest": n.isRest,
        })
        offset += dur_16ths

    return events

def _active_event_at(events, offset_16ths):
    for event in events:
        if event["start"] <= offset_16ths < event["end"]:
            return event
    return None

def _note_refs_for_offset(events_by_voice, voice_ids, offset_16ths):
    refs = []
    for voice_id in voice_ids:
        event = _active_event_at(events_by_voice.get(voice_id, []), offset_16ths)
        if event and not event["isRest"]:
            refs.append({
                "voice_id": voice_id,
                "start_offset": event["start"],
                "end_offset": event["end"],
                "pitch": event["pitch"],
            })
    return refs

def evaluate_counterpoint_issues(voice_streams, meter_info=None):
    meter = _build_meter_info(meter_info)
    total_16ths = max((get_stream_length_16ths(m21_stream) for m21_stream in voice_streams.values()), default=0)
    if total_16ths == 0:
        return []

    grids = {
        voice_id: _stream_to_pitch_grid(m21_stream, total_16ths)
        for voice_id, m21_stream in voice_streams.items()
    }
    events_by_voice = {
        voice_id: _stream_to_events(m21_stream)
        for voice_id, m21_stream in voice_streams.items()
    }

    note_change_offsets = {0}
    onset_offsets = set()
    for voice_id, events in events_by_voice.items():
        for event in events:
            note_change_offsets.add(event["start"])
            if not event["isRest"]:
                onset_offsets.add(event["start"])

    strong_beat_offsets = set(range(0, total_16ths, meter["beat_16ths"]))
    vertical_offsets = sorted(offset for offset in (onset_offsets | strong_beat_offsets) if offset < total_16ths)
    transition_offsets = sorted(offset for offset in note_change_offsets if 0 < offset < total_16ths)

    issues = []
    last_offset_by_key = {}

    def add_issue(issue_id, voice_ids, offset_16ths, summary):
        if last_offset_by_key.get(issue_id, -99) >= offset_16ths - 1:
            return

        issue = {
            "id": issue_id,
            "offset": offset_16ths,
            "location": _format_location(offset_16ths, meter),
            "summary": summary,
            "voices": list(voice_ids),
            "note_refs": _note_refs_for_offset(events_by_voice, voice_ids, offset_16ths),
        }
        issues.append(issue)
        last_offset_by_key[issue_id] = offset_16ths

    for offset in vertical_offsets:
        sounding = {voice_id: grid[offset] for voice_id, grid in grids.items() if grid[offset] != -1}

        if 0 in sounding and 1 in sounding and sounding[0] < sounding[1]:
            add_issue("voice_cross_top_mid", [0, 1], offset, "Voice crossing between the top and middle voices.")
        if 1 in sounding and 2 in sounding and sounding[1] < sounding[2]:
            add_issue("voice_cross_mid_bass", [1, 2], offset, "Voice crossing between the middle and bass voices.")
        if 0 in sounding and 1 in sounding and sounding[0] - sounding[1] > 19:
            add_issue("wide_top_mid", [0, 1], offset, "Top and middle voices are spaced too widely.")
        if 1 in sounding and 2 in sounding and sounding[1] - sounding[2] > 24:
            add_issue("wide_mid_bass", [1, 2], offset, "Middle and bass voices are spaced too widely.")

        active_voice_ids = sorted(sounding.keys())
        for i in range(len(active_voice_ids)):
            for j in range(i + 1, len(active_voice_ids)):
                voice_a = active_voice_ids[i]
                voice_b = active_voice_ids[j]
                interval = abs(sounding[voice_a] - sounding[voice_b]) % 12

                if not _is_strong_beat_consonance(interval, voice_a, voice_b):
                    if not (
                        _is_weak_dissonance_allowed(grids[voice_a], grids[voice_b], offset)
                        or _is_weak_dissonance_allowed(grids[voice_b], grids[voice_a], offset)
                    ):
                        beat_type = "Strong-beat" if offset in strong_beat_offsets else "Unprepared weak-beat"
                        add_issue(
                            f"dissonance_{voice_a}_{voice_b}",
                            [voice_a, voice_b],
                            offset,
                            f"{beat_type} dissonance between {VOICE_NAMES[voice_a].lower()} and {VOICE_NAMES[voice_b].lower()}.",
                        )

    for offset in transition_offsets:
        prev_offset = offset - 1
        for voice_a in voice_streams:
            for voice_b in voice_streams:
                if voice_a >= voice_b:
                    continue

                prev_a = grids[voice_a][prev_offset]
                prev_b = grids[voice_b][prev_offset]
                curr_a = grids[voice_a][offset]
                curr_b = grids[voice_b][offset]
                if -1 in (prev_a, prev_b, curr_a, curr_b):
                    continue

                prev_interval = abs(prev_a - prev_b) % 12
                curr_interval = abs(curr_a - curr_b) % 12
                motion_a = curr_a - prev_a
                motion_b = curr_b - prev_b

                if (prev_interval in PERFECT_INTERVALS and curr_interval in PERFECT_INTERVALS and 
                    motion_a != 0 and motion_b != 0 and _same_direction(motion_a, motion_b)):
                    add_issue(
                        f"parallel_perfect_{voice_a}_{voice_b}",
                        [voice_a, voice_b],
                        offset,
                        f"Parallel fifth or octave between {VOICE_NAMES[voice_a].lower()} and {VOICE_NAMES[voice_b].lower()}.",
                    )

                if {voice_a, voice_b} == {0, 2} and curr_interval in PERFECT_INTERVALS and motion_a != 0 and motion_b != 0 and _same_direction(motion_a, motion_b):
                    top_motion = motion_a if voice_a == 0 else motion_b
                    if abs(top_motion) > 2 and prev_interval not in PERFECT_INTERVALS:
                        add_issue(
                            "direct_outer_perfect",
                            [0, 2],
                            offset,
                            "Direct fifth or octave in the outer voices.",
                        )

    return issues


def analyze_voice_streams(voice_streams, check_weak_dissonances=True, meter_info=None, return_events=False):
    issues = {voice_id: [] for voice_id in voice_streams}
    seen = {voice_id: set() for voice_id in voice_streams}
    meter = _build_meter_info(meter_info)

    total_16ths = max((get_stream_length_16ths(m21_stream) for m21_stream in voice_streams.values()), default=0)
    if total_16ths == 0:
        return (issues, []) if return_events else issues

    grids = {
        voice_id: _stream_to_pitch_grid(m21_stream, total_16ths)
        for voice_id, m21_stream in voice_streams.items()
    }
    issue_events = []
    event_seen = set()

    def add_issue(voice_ids, message, offset):
        if message not in event_seen:
            issue_events.append({
                "offset": offset,
                "location": _format_location(offset, meter),
                "message": message,
            })
            event_seen.add(message)
        for voice_id in voice_ids:
            if message not in seen[voice_id]:
                issues[voice_id].append(message)
                seen[voice_id].add(message)

    for offset in range(total_16ths):
        sounding = {voice_id: grid[offset] for voice_id, grid in grids.items() if grid[offset] != -1}

        if 0 in sounding and 1 in sounding and sounding[0] <= sounding[1]:
            add_issue([0, 1], f"Voice crossing between the top and middle voices at {_format_location(offset, meter)}.", offset)
        if 1 in sounding and 2 in sounding and sounding[1] <= sounding[2]:
            add_issue([1, 2], f"Voice crossing between the middle and bass voices at {_format_location(offset, meter)}.", offset)
        if 0 in sounding and 1 in sounding and sounding[0] - sounding[1] > 19:
            add_issue([0, 1], f"Top and middle voices are spaced too widely at {_format_location(offset, meter)}.", offset)
        if 1 in sounding and 2 in sounding and sounding[1] - sounding[2] > 24:
            add_issue([1, 2], f"Middle and bass voices are spaced too widely at {_format_location(offset, meter)}.", offset)

        active_voice_ids = sorted(sounding.keys())
        for i in range(len(active_voice_ids)):
            for j in range(i + 1, len(active_voice_ids)):
                voice_a = active_voice_ids[i]
                voice_b = active_voice_ids[j]
                interval = abs(sounding[voice_a] - sounding[voice_b]) % 12

                if offset % meter["beat_16ths"] == 0:
                    if not _is_strong_beat_consonance(interval, voice_a, voice_b):
                        add_issue(
                            [voice_a, voice_b],
                            f"Strong-beat dissonance between {VOICE_NAMES[voice_a].lower()} and {VOICE_NAMES[voice_b].lower()} at {_format_location(offset, meter)}.",
                            offset,
                        )
                elif check_weak_dissonances and not _is_strong_beat_consonance(interval, voice_a, voice_b):
                    if not (
                        _is_weak_dissonance_allowed(grids[voice_a], grids[voice_b], offset)
                        or _is_weak_dissonance_allowed(grids[voice_b], grids[voice_a], offset)
                    ):
                        add_issue(
                            [voice_a, voice_b],
                            f"Unprepared dissonance between {VOICE_NAMES[voice_a].lower()} and {VOICE_NAMES[voice_b].lower()} at {_format_location(offset, meter)}.",
                            offset,
                        )

    for offset in range(1, total_16ths):
        for voice_a in voice_streams:
            for voice_b in voice_streams:
                if voice_a >= voice_b:
                    continue

                prev_a = grids[voice_a][offset - 1]
                prev_b = grids[voice_b][offset - 1]
                curr_a = grids[voice_a][offset]
                curr_b = grids[voice_b][offset]
                if -1 in (prev_a, prev_b, curr_a, curr_b):
                    continue

                prev_interval = abs(prev_a - prev_b) % 12
                curr_interval = abs(curr_a - curr_b) % 12
                motion_a = curr_a - prev_a
                motion_b = curr_b - prev_b

                if prev_interval in PERFECT_INTERVALS and curr_interval in PERFECT_INTERVALS and _same_direction(motion_a, motion_b):
                    add_issue(
                        [voice_a, voice_b],
                        f"Parallel fifth or octave between {VOICE_NAMES[voice_a].lower()} and {VOICE_NAMES[voice_b].lower()} near {_format_location(offset, meter)}.",
                        offset,
                    )

                if {voice_a, voice_b} == {0, 2} and curr_interval in PERFECT_INTERVALS and _same_direction(motion_a, motion_b):
                    top_motion = motion_a if voice_a == 0 else motion_b
                    if abs(top_motion) > 2 and prev_interval not in PERFECT_INTERVALS:
                        add_issue(
                            [0, 2],
                            f"Direct fifth or octave in the outer voices near {_format_location(offset, meter)}.",
                            offset,
                        )

    if return_events:
        return issues, issue_events

    return issues

class FugueSolver(object):
    def __init__(self, existing_streams, target_notes=None, prev_gen_pitch=None, prev_ext_pitches=None, strict_invertible=False, voice_id=0, target_chord=None, allowed_durations=None, locked_prefix=None):
        self.s = Solver()
        self.s.set("timeout", 10000)
        self.existing_streams = existing_streams
        self.prev_gen_pitch = prev_gen_pitch
        self.prev_ext_pitches = prev_ext_pitches 
        self.strict_invertible = strict_invertible
        self.voice_id = voice_id 
        self.target_chord = target_chord 
        self.allowed_durations = sorted(set(allowed_durations or [1, 2, 4, 8]))
        self.locked_prefix = []
        if locked_prefix:
            for n in locked_prefix.flatten().notesAndRests:
                self.locked_prefix.append((
                    -1 if n.isRest else n.pitch.midi,
                    int(n.quarterLength / 0.25),
                ))

        if len(self.existing_streams) > 0:
            reference_stream = self.existing_streams[0]
            self.total_subject_ql = sum(n.quarterLength for n in reference_stream.flatten().notesAndRests)
            self.total_16ths = int(self.total_subject_ql / 0.25)
            self.reference_note_count = max(1, len(list(reference_stream.flatten().notesAndRests)))
        else:
            self.total_16ths = 16 
            self.reference_note_count = 4

        default_note_budget = min(self.total_16ths, max(6, self.reference_note_count * 3))
        self.max_notes = target_notes if target_notes is not None else default_note_budget

        if self.max_notes <= 0:
            self.max_notes = 16

        self.new_pitches = [Int(f'new_p_{i}') for i in range(self.max_notes)]
        self.new_durations = [Int(f'new_d_{i}') for i in range(self.max_notes)]
        self.new_offsets = [Int(f'new_off_{i}') for i in range(self.max_notes)]

    def setup_rules(self):
        self.apply_rhythm_rules()
        self.apply_locked_prefix_rules()
        self.apply_melodic_rules()
        self.apply_counterpoint_rules()
        self.apply_parallel_rules()
        self.apply_diatonic_scale_rule()
        self.apply_harmony_rules() 
        self.apply_voice_crossing_rules()
        if self.strict_invertible:
            self.apply_invertible_rules()

    def apply_rhythm_rules(self):
        self.s.add(self.new_offsets[0] == 0)
        
        for i in range(self.max_notes):
            d = self.new_durations[i]
            off = self.new_offsets[i]
            p = self.new_pitches[i]
            
            duration_choices = [d == 0] + [d == value for value in self.allowed_durations]
            self.s.add(Or(*duration_choices))
            self.s.add((d == 0) == (p == -1)) 
            
            if i < self.max_notes - 1:
                self.s.add(Implies(d == 0, self.new_durations[i+1] == 0))
            
            if i > 0:
                self.s.add(self.new_offsets[i] == self.new_offsets[i-1] + self.new_durations[i-1])

            self.s.add(Implies(d >= 4, off % 2 == 0))
            self.s.add(Implies(d >= 8, off % 4 == 0))

            if i == 0:
                self.s.add(Implies(d == 1, self.new_durations[i+1] == 1))
            elif i == self.max_notes - 1:
                self.s.add(Implies(d == 1, self.new_durations[i-1] == 1))
            else:
                self.s.add(Implies(d == 1, Or(self.new_durations[i-1] == 1, self.new_durations[i+1] == 1)))

        self.s.add(Sum(self.new_durations) == self.total_16ths)

    def apply_locked_prefix_rules(self):
        if not self.locked_prefix:
            return

        for i, (pitch, duration) in enumerate(self.locked_prefix):
            if i >= self.max_notes:
                break
            self.s.add(self.new_durations[i] == duration)
            self.s.add(self.new_pitches[i] == pitch)

    def apply_melodic_rules(self):
        for i in range(self.max_notes):
            p = self.new_pitches[i]
            is_active = And(self.new_durations[i] > 0, p != -1)
            
            if self.voice_id == 0:
                self.s.add(Implies(is_active, And(p >= 60, p <= 84)))
            elif self.voice_id == 1:
                self.s.add(Implies(is_active, And(p >= 48, p <= 72)))
            else:
                self.s.add(Implies(is_active, And(p >= 36, p <= 60)))

        if self.prev_gen_pitch is not None and self.prev_gen_pitch != -1 and self.max_notes > 0:
            first_pitch = self.new_pitches[0]
            first_active = And(self.new_durations[0] > 0, first_pitch != -1)
            first_interval = first_pitch - self.prev_gen_pitch
            first_abs_interval = If(first_interval >= 0, first_interval, -first_interval)
            if self.voice_id == 2:
                self.s.add(Implies(first_active, And(first_abs_interval >= 1, first_abs_interval <= 7)))
            else:
                self.s.add(Implies(first_active, And(first_abs_interval >= 1, first_abs_interval <= 5)))

        for i in range(self.max_notes - 1):
            p1 = self.new_pitches[i]
            p2 = self.new_pitches[i+1]
            both_sound = And(self.new_durations[i] > 0, p1 != -1, self.new_durations[i+1] > 0, p2 != -1)
            
            interval = p2 - p1
            abs_interval = If(interval >= 0, interval, -interval)
            
            self.s.add(Implies(both_sound, Or(abs_interval <= 8, abs_interval == 12)))
            self.s.add(Implies(both_sound, And(abs_interval != 10, abs_interval != 11)))
            
            is_fast_transition = Or(self.new_durations[i] == 1, self.new_durations[i+1] == 1)
            self.s.add(Implies(And(both_sound, is_fast_transition), abs_interval <= 4))

            if i < self.max_notes - 2:
                p3 = self.new_pitches[i+2]
                all_three = And(both_sound, self.new_durations[i+2] > 0, p3 != -1)
                int1 = p2 - p1
                int2 = p3 - p2
                
                self.s.add(Implies(And(all_three, int1 > 4), And(int2 <= 0, int2 >= -4)))
                self.s.add(Implies(And(all_three, int1 < -4), And(int2 >= 0, int2 <= 4)))

    def apply_counterpoint_rules(self):
        for ext_stream in self.existing_streams:
            fixed_data = [] 
            curr_off = 0
            for n in ext_stream.flatten().notesAndRests:
                d = int(n.quarterLength / 0.25)
                p = -1 if n.isRest else n.pitch.midi
                fixed_data.append((curr_off, d, p))
                curr_off += d
                
            for i in range(self.max_notes):
                n_off = self.new_offsets[i]
                n_dur = self.new_durations[i]
                n_p = self.new_pitches[i]
                is_active = And(n_dur > 0, n_p != -1)
                is_strong_beat = (n_off % 4 == 0)
                
                for f_off, f_dur, f_p in fixed_data:
                    if f_p == -1: continue
                    overlap = And(n_off < f_off + f_dur, n_off + n_dur > f_off)
                    interval = If(n_p >= f_p, n_p - f_p, f_p - n_p) % 12
                    
                    is_consonant = Or(interval == 0, interval == 3, interval == 4, 
                                      interval == 5, interval == 7, interval == 8, interval == 9)
                    self.s.add(Implies(And(is_active, overlap, is_strong_beat), is_consonant))

    def apply_parallel_rules(self):
        for ext_stream in self.existing_streams:
            fixed_data = [] 
            curr_off = 0
            for n in ext_stream.flatten().notesAndRests:
                d = int(n.quarterLength / 0.25)
                p = -1 if n.isRest else n.pitch.midi
                fixed_data.append((curr_off, d, p))
                curr_off += d
                
            for i in range(self.max_notes - 1):
                p1 = self.new_pitches[i]
                p2 = self.new_pitches[i+1]
                off1 = self.new_offsets[i]
                off2 = self.new_offsets[i+1]
                both_sound = And(self.new_durations[i] > 0, p1 != -1, self.new_durations[i+1] > 0, p2 != -1)
                
                for j in range(len(fixed_data) - 1):
                    f_off1, f_dur1, f_p1 = fixed_data[j]
                    f_off2, f_dur2, f_p2 = fixed_data[j+1]
                    if f_p1 == -1 or f_p2 == -1: continue
                    
                    simultaneous_motion = And(off1 == f_off1, off2 == f_off2)
                    int1 = If(p1 >= f_p1, p1 - f_p1, f_p1 - p1) % 12
                    int2 = If(p2 >= f_p2, p2 - f_p2, f_p2 - p2) % 12
                    
                    is_parallel_5th = And(int1 == 7, int2 == 7)
                    is_parallel_8ve = And(int1 == 0, int2 == 0)
                    is_parallel_4th = And(int1 == 5, int2 == 5)
                    
                    if self.strict_invertible:
                        self.s.add(Implies(And(both_sound, simultaneous_motion), Not(Or(is_parallel_5th, is_parallel_8ve, is_parallel_4th))))
                    else:
                        self.s.add(Implies(And(both_sound, simultaneous_motion), Not(Or(is_parallel_5th, is_parallel_8ve))))

    def apply_diatonic_scale_rule(self):
        for i in range(self.max_notes):
            p = self.new_pitches[i]
            pc = p % 12
            is_active = And(self.new_durations[i] > 0, p != -1)
            is_white_key = Or(pc == 0, pc == 2, pc == 4, pc == 5, pc == 7, pc == 9, pc == 11)
            self.s.add(Implies(is_active, is_white_key))

    def apply_harmony_rules(self):
        if not self.target_chord: return
        
        if self.target_chord == 'I': chord_pcs = [0, 4, 7]
        elif self.target_chord == 'IV': chord_pcs = [0, 5, 9]
        elif self.target_chord == 'V': chord_pcs = [2, 7, 11]
        else: return
        
        for i in range(self.max_notes):
            p = self.new_pitches[i]
            off = self.new_offsets[i]
            is_active = And(self.new_durations[i] > 0, p != -1)
            pc = p % 12
            
            is_downbeat = (off == 0)
            self.s.add(Implies(And(is_active, is_downbeat), Or(
                pc == chord_pcs[0], 
                pc == chord_pcs[1], 
                pc == chord_pcs[2]
            )))

    def apply_voice_crossing_rules(self):
        """Mathematically bans the generated voice from crossing existing voices."""
        for ext_stream in self.existing_streams:
            fixed_data = [] 
            curr_off = 0
            for n in ext_stream.flatten().notesAndRests:
                d = int(n.quarterLength / 0.25)
                p = -1 if n.isRest else n.pitch.midi
                fixed_data.append((curr_off, d, p))
                curr_off += d
                
            for i in range(self.max_notes):
                n_off = self.new_offsets[i]
                n_dur = self.new_durations[i]
                n_p = self.new_pitches[i]
                is_active = And(n_dur > 0, n_p != -1)
                
                for f_off, f_dur, f_p in fixed_data:
                    if f_p == -1: continue
                    # Check if the notes are playing at the exact same time
                    overlap = And(n_off < f_off + f_dur, n_off + n_dur > f_off)
                    
                    # If generating the Top Voice, it MUST be higher than the existing note
                    if self.voice_id == 0: 
                        self.s.add(Implies(And(is_active, overlap), n_p > f_p))
                        
                    # If generating the Bass Voice, it MUST be lower than the existing note
                    elif self.voice_id == 2: 
                        self.s.add(Implies(And(is_active, overlap), n_p < f_p))

    def apply_invertible_rules(self):
        if not self.strict_invertible: return
        pass

    def check_for_mistakes(self, stream_to_check):
        mistakes = []
        notes = list(stream_to_check.flatten().notesAndRests)
        limit = min(len(notes), self.max_notes)
        for i in range(self.max_notes):
            if i < limit:
                n = notes[i]
                p = -1 if n.isRest else n.pitch.midi
                d = int(n.quarterLength / 0.25)
            else:
                p, d = -1, 0
                
            self.s.add(self.new_pitches[i] == p)
            self.s.add(self.new_durations[i] == d)
            
        self.s.add(self.new_offsets[0] == 0)
        for i in range(1, self.max_notes):
            self.s.add(self.new_offsets[i] == self.new_offsets[i-1] + self.new_durations[i-1])
        
        self.s.push()
        self.apply_melodic_rules()
        if self.s.check() == unsat: mistakes.append("Melodic error: Awkward leap or unresolved large leap.")
        self.s.pop()

        self.s.push()
        self.apply_parallel_rules()
        if self.s.check() == unsat: mistakes.append("Harmonic error: Parallel 5ths or Octaves detected.")
        self.s.pop()
        
        self.s.push()
        self.apply_counterpoint_rules()
        if self.s.check() == unsat: mistakes.append("Counterpoint error: Unresolved dissonance on a strong beat.")
        self.s.pop()
        
        return mistakes

    def generate_next_solution(self):
        if self.s.check() == sat:
            model = self.s.model()
            new_stream = stream.Stream()
            
            blocking_clause = []
            
            for i in range(self.max_notes):
                d_val = model.evaluate(self.new_durations[i]).as_long()
                blocking_clause.append(self.new_durations[i] != d_val)
                
                if d_val > 0:
                    p_val = model.evaluate(self.new_pitches[i]).as_long()
                    blocking_clause.append(self.new_pitches[i] != p_val)
                    
                    if p_val == -1:
                        new_stream.append(note.Rest(quarterLength=d_val * 0.25))
                    else:
                        new_stream.append(note.Note(p_val, quarterLength=d_val * 0.25))
            
            self.s.add(Or(blocking_clause))
            return new_stream
        return None

def transpose_stream(m21_stream, semitones):
    if not m21_stream:
        return stream.Stream()

    new_stream = stream.Stream()
    for n in m21_stream.flatten().notesAndRests:
        if n.isRest:
            new_stream.append(note.Rest(quarterLength=n.quarterLength))
        else:
            new_stream.append(note.Note(n.pitch.midi + semitones, quarterLength=n.quarterLength))
    return new_stream
