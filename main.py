import copy
from flask import Flask, request, jsonify
from music21 import stream, note
from fugue_solver import *
from fugue_state import FugueBlueprint

app = Flask(__name__)
blueprint = FugueBlueprint()

ROLE_SOURCE_VOICE = {
    'subject': 1,
    'answer': 2,
    'cs1': 0,
    'cs2': 0,
    'free_melody': 0,
    'episode_line': 0,
}
def make_empty_voice_streams():
    return {0: stream.Stream(), 1: stream.Stream(), 2: stream.Stream()}

global_state = {
    "solver": None,
    "measure_number": 2,
    "ans_stream": None,
    "generated_streams": [],
    "last_voice_streams": {},
    "committed_history_streams": make_empty_voice_streams(),
    "meter_info": {"numerator": 4, "denominator": 4},
}
VOICE_RANGES = {
    0: (60, 84),
    1: (48, 72),
    2: (36, 60),
}
VOICE_CENTERS = {
    0: 72,
    1: 60,
    2: 48,
}

# --- HELPERS ---
def get_last_pitch(m21_stream):
    if not m21_stream: return -1
    notes = list(m21_stream.flatten().notesAndRests)
    for n in reversed(notes):
        if not n.isRest: return n.pitch.midi
    return -1

def score_solution(voice_streams, blueprint):
    score = 0
    depth = blueprint.episode_count + blueprint.middle_entry_count
    is_developing_stage = (depth >= 2)

    v0_notes = list(voice_streams[0].flatten().notesAndRests) if voice_streams.get(0) else []
    v1_notes = list(voice_streams[1].flatten().notesAndRests) if voice_streams.get(1) else []
    v2_notes = list(voice_streams[2].flatten().notesAndRests) if voice_streams.get(2) else []

    # --- 1. DEVELOPING METER HEURISTIC ---
    for v_id, m21_stream in voice_streams.items():
        if not m21_stream: continue
        for n in m21_stream.flatten().notesAndRests:
            if not n.isRest:
                if n.quarterLength <= 0.25:
                    if is_developing_stage: score += 15
                    else: score -= 15

    # --- 2. HARMONIC PROGRESSION (Cadences) ---
    if blueprint.current_harmony == 'I':
        if len(v0_notes) > 1:
            for i in range(len(v0_notes) - 1):
                if not v0_notes[i].isRest and not v0_notes[i+1].isRest:
                    if v0_notes[i].pitch.midi % 12 == 11 and v0_notes[i+1].pitch.midi % 12 == 0: 
                        score += 50

        if len(v2_notes) > 1:
            for i in range(len(v2_notes) - 1):
                if not v2_notes[i].isRest and not v2_notes[i+1].isRest:
                    if v2_notes[i].pitch.midi % 12 == 7 and v2_notes[i+1].pitch.midi % 12 == 0:  
                        score += 50

    # --- 3. CONTRARY MOTION (Outer Voices) ---
    if len(v0_notes) > 1 and len(v2_notes) > 1:
        limit = min(len(v0_notes), len(v2_notes))
        for i in range(limit - 1):
            if not v0_notes[i].isRest and not v0_notes[i+1].isRest and not v2_notes[i].isRest and not v2_notes[i+1].isRest:
                top_int = v0_notes[i+1].pitch.midi - v0_notes[i].pitch.midi
                bass_int = v2_notes[i+1].pitch.midi - v2_notes[i].pitch.midi
                if (top_int > 0 and bass_int < 0) or (top_int < 0 and bass_int > 0):
                    score += 20

    # --- 4. MELODIC GAP-FILLING ---
    gen_voice_id = 0 if blueprint.state in ['EXPO_3', 'EPISODE'] else 2
    gen_notes = v0_notes if gen_voice_id == 0 else v2_notes
    if len(gen_notes) > 2:
        for i in range(len(gen_notes) - 2):
            if not gen_notes[i].isRest and not gen_notes[i+1].isRest and not gen_notes[i+2].isRest:
                int1 = gen_notes[i+1].pitch.midi - gen_notes[i].pitch.midi
                int2 = gen_notes[i+2].pitch.midi - gen_notes[i+1].pitch.midi
                
                if abs(int1) >= 5:
                    if (int1 > 0 and -2 <= int2 < 0) or (int1 < 0 and 0 < int2 <= 2):
                        score += 30

    return score

def reset_runtime():
    global blueprint
    blueprint = FugueBlueprint()
    global_state['solver'] = None
    global_state['generated_streams'] = []
    global_state['last_voice_streams'] = {}
    global_state['committed_history_streams'] = make_empty_voice_streams()
    global_state['meter_info'] = {"numerator": 4, "denominator": 4}
    global_state.pop('instructions', None)
    global_state.pop('target_gen_role', None)
    global_state.pop('active_voice_id', None)
    global_state.pop('anchor_pitch', None)

def summarize_issues(issue_map):
    summaries = []
    for voice_id in sorted(issue_map):
        if issue_map[voice_id]:
            summaries.append(f"{VOICE_NAMES[voice_id]}: {'; '.join(issue_map[voice_id][:3])}")
    return summaries

def get_meter_info():
    meter_info = global_state.get('meter_info') or {}
    numerator = max(1, int(meter_info.get('numerator', 4)))
    denominator = max(1, int(meter_info.get('denominator', 4)))
    return {"numerator": numerator, "denominator": denominator}

def strip_issue_location(message):
    for marker in [" at m.", " near m."]:
        index = message.find(marker)
        if index != -1:
            return message[:index].rstrip(".")
    return message.rstrip(".")

def format_issue_report(issue_events):
    if not issue_events:
        return ["No mistakes found!"]

    grouped = {}
    for event in sorted(issue_events, key=lambda item: (item.get("offset", 0), item.get("message", ""))):
        location = event.get("location", "Unknown location")
        detail = strip_issue_location(event.get("message", "Issue found"))
        grouped.setdefault(location, [])
        if detail not in grouped[location]:
            grouped[location].append(detail)

    lines = [f"{sum(len(details) for details in grouped.values())} issue(s) found:"]
    for location, details in grouped.items():
        lines.append(f"{location}: {'; '.join(details)}")
    return lines

def get_note_budget(state_name):
    subject_stream = blueprint.motives.get('subject')
    if not subject_stream:
        return 4

    allowed_durations = get_allowed_durations(state_name)
    shortest_allowed = min(allowed_durations) if allowed_durations else 1
    total_16ths = max(1, get_stream_length_16ths(subject_stream))
    return max(1, total_16ths // shortest_allowed)

def get_subject_durations():
    subject_stream = blueprint.motives.get('subject')
    if not subject_stream:
        return [1, 2, 4, 8]

    durations = sorted({
        int(n.quarterLength / 0.25)
        for n in subject_stream.flatten().notesAndRests
        if n.quarterLength > 0
    })
    return durations or [2, 4]

def get_allowed_durations(state_name):
    all_durations = [1, 2, 4, 8]
    subject_durations = get_subject_durations()
    shortest_subject = min(subject_durations) if subject_durations else 2

    if state_name == 'EPISODE':
        shortest_allowed = max(1, shortest_subject // 2)
    else:
        shortest_allowed = shortest_subject

    return [value for value in all_durations if value >= shortest_allowed]

def get_pitch_list(m21_stream):
    return [n.pitch.midi for n in m21_stream.flatten().notesAndRests if not n.isRest]

def get_first_pitch(m21_stream):
    if not m21_stream:
        return -1

    for n in m21_stream.flatten().notesAndRests:
        if not n.isRest:
            return n.pitch.midi
    return -1

def get_average_pitch(m21_stream):
    pitches = get_pitch_list(m21_stream)
    if not pitches:
        return None
    return sum(pitches) / float(len(pitches))

def copy_voice_streams(voice_streams):
    return {
        voice_id: clone_stream(m21_stream)
        for voice_id, m21_stream in voice_streams.items()
    }

def voice_streams_signature(voice_streams):
    return tuple(
        (
            voice_id,
            stream_signature(voice_streams.get(voice_id, stream.Stream())),
        )
        for voice_id in range(3)
    )

def build_candidate_entry(generated_stream, voice_streams, score, episode_plan=None):
    return {
        "generated_stream": clone_stream(generated_stream),
        "voice_streams": copy_voice_streams(voice_streams),
        "score": float(score),
        "episode_plan": copy.deepcopy(episode_plan),
    }

def candidate_signature(candidate):
    return voice_streams_signature(candidate.get("voice_streams", {}))

def copy_candidate_entries(candidates):
    return [
        build_candidate_entry(
            candidate["generated_stream"],
            candidate["voice_streams"],
            candidate.get("score", 0.0),
            candidate.get("episode_plan"),
        )
        for candidate in candidates
    ]

def extend_voice_streams(base_voice_streams, new_voice_streams):
    combined = {}
    for voice_id in range(3):
        combined[voice_id] = concatenate_streams([
            base_voice_streams.get(voice_id, stream.Stream()),
            new_voice_streams.get(voice_id, stream.Stream()),
        ])
    return combined

def subtract_issue_maps(base_issue_map, combined_issue_map):
    issue_delta = {0: [], 1: [], 2: []}
    for voice_id in issue_delta:
        seen = set(base_issue_map.get(voice_id, []))
        issue_delta[voice_id] = [
            issue for issue in combined_issue_map.get(voice_id, [])
            if issue not in seen
        ]
    return issue_delta

def evaluate_candidate_voice_streams(voice_streams, check_weak_dissonances=True):
    history_streams = copy_voice_streams(global_state.get('committed_history_streams', make_empty_voice_streams()))
    base_issues = analyze_voice_streams(
        history_streams,
        check_weak_dissonances=check_weak_dissonances,
        meter_info=get_meter_info(),
    )
    combined_streams = extend_voice_streams(history_streams, voice_streams)
    combined_issues = analyze_voice_streams(
        combined_streams,
        check_weak_dissonances=check_weak_dissonances,
        meter_info=get_meter_info(),
    )
    return subtract_issue_maps(base_issues, combined_issues), combined_streams

def score_boundary_continuity(history_streams, voice_streams, state_name):
    score = 0.0
    subject_voice = blueprint.last_subject_voice if state_name == 'MIDDLE_ENTRY' else None

    for voice_id in range(3):
        prev_pitch = get_last_pitch(history_streams.get(voice_id))
        curr_pitch = get_first_pitch(voice_streams.get(voice_id))
        if -1 in (prev_pitch, curr_pitch):
            continue

        abs_interval = abs(curr_pitch - prev_pitch)
        interval_limit = 7 if voice_id == 2 else 5

        if abs_interval == 0:
            score += 8 if state_name == 'MIDDLE_ENTRY' else 4
        elif abs_interval <= 2:
            score += 0
        elif abs_interval <= 4:
            score += 2
        elif abs_interval <= interval_limit:
            score += 8
        else:
            score += 18 + (abs_interval - interval_limit) * 6

        if state_name == 'MIDDLE_ENTRY' and voice_id != subject_voice and abs_interval > 2:
            score += 10 + (abs_interval - 2) * 4

        if state_name == 'EPISODE' and voice_id == 2 and abs_interval == 0:
            score += 8

    return score

def get_previous_voice_pitch(voice_id):
    history_streams = global_state.get('committed_history_streams', {})
    pitch = get_last_pitch(history_streams.get(voice_id))
    if pitch != -1:
        return pitch

    last_voice_streams = global_state.get('last_voice_streams', {})
    pitch = get_last_pitch(last_voice_streams.get(voice_id))
    if pitch != -1:
        return pitch

    if voice_id == 0:
        return get_last_pitch(blueprint.motives.get('subject'))

    return -1

def fit_stream_to_voice_range(m21_stream, voice_id, reference_pitch=None):
    pitches = get_pitch_list(m21_stream)
    if not pitches:
        return clone_stream(m21_stream)

    low, high = VOICE_RANGES[voice_id]
    center = VOICE_CENTERS[voice_id]
    candidates = []

    for octave_shift in range(-2, 3):
        shifted_stream = transpose_stream(m21_stream, octave_shift * 12)
        shifted_pitches = get_pitch_list(shifted_stream)
        if not shifted_pitches:
            continue

        if min(shifted_pitches) < low or max(shifted_pitches) > high:
            continue

        score = sum(abs(pitch - center) for pitch in shifted_pitches)
        if reference_pitch not in (-1, None):
            boundary_span = abs(shifted_pitches[0] - reference_pitch)
            interval_limit = 7 if voice_id == 2 else 5
            score += boundary_span * 2
            if boundary_span == 0:
                score += 6
            elif boundary_span > interval_limit:
                score += (boundary_span - interval_limit) * 10
        candidates.append((score, shifted_stream))

    if candidates:
        return min(candidates, key=lambda item: item[0])[1]

    return clone_stream(m21_stream)

def stream_signature(m21_stream):
    return tuple(
        (
            -1 if n.isRest else n.pitch.midi,
            int(n.quarterLength / 0.25),
        )
        for n in m21_stream.flatten().notesAndRests
    )

def score_generated_solution(generated_stream, voice_streams, active_voice_id, prev_pitch, state_name, issue_map=None):
    pitches = get_pitch_list(generated_stream)
    if not pitches:
        return float('inf')

    score = 0.0
    center = VOICE_CENTERS[active_voice_id]
    history_streams = global_state.get('committed_history_streams', make_empty_voice_streams())

    score += sum(abs(pitch - center) for pitch in pitches) / 8.0
    score += score_boundary_continuity(history_streams, voice_streams, state_name)

    first_pitch = pitches[0]
    if prev_pitch not in (-1, None):
        first_motion = first_pitch - prev_pitch
        abs_first_motion = abs(first_motion)
        if abs_first_motion == 0:
            score += 24
        else:
            score += max(0, abs_first_motion - 2) * 4

        if first_motion > 4:
            score += (first_motion - 4) * 3
        elif first_motion < -5:
            score += (abs(first_motion) - 5) * 1.5

    for i in range(len(pitches) - 1):
        interval = pitches[i + 1] - pitches[i]
        abs_interval = abs(interval)

        if abs_interval == 0:
            repeat_penalty = 3
            if state_name == 'EPISODE':
                repeat_penalty += 4
            if active_voice_id == 2:
                repeat_penalty += 4
            if i == len(pitches) - 2:
                repeat_penalty += 4
            if i + 2 < len(pitches) and pitches[i + 2] == pitches[i]:
                repeat_penalty += 3
            score += repeat_penalty
        elif abs_interval <= 2:
            score += 0
        elif abs_interval <= 4:
            score += 1
        elif abs_interval == 5:
            score += 3
        elif abs_interval <= 7:
            score += 6
        elif abs_interval <= 9:
            score += 12
        else:
            score += 25

        if abs_interval >= 5:
            if i + 2 < len(pitches):
                recovery = pitches[i + 2] - pitches[i + 1]
                recovers_by_step = abs(recovery) <= 2 and ((interval > 0 and recovery < 0) or (interval < 0 and recovery > 0))
                if not recovers_by_step:
                    score += 10
            else:
                score += 5

    if state_name in ['EXPO_2', 'EXPO_3']:
        score += sum(1 for i in range(len(pitches) - 1) if abs(pitches[i + 1] - pitches[i]) >= 5) * 3

    voice_issues = issue_map if issue_map is not None else analyze_voice_streams(
        voice_streams,
        meter_info=get_meter_info(),
    )
    score += sum(len(issues) for issues in voice_issues.values()) * 40

    return score

def convert_json_to_stream(json_payload):
    result = stream.Stream()
    subject_data = json_payload.get('subject', [])
    
    i = 0
    while i < len(subject_data):
        n = subject_data[i]
        pitch = n.get('pitch')
        q_len = n.get('ticks') / 480.0 
        offset_ql = n.get('offset_ticks', 0) / 480.0 
        
        is_tied = n.get('tie', False)
        
        while is_tied and i + 1 < len(subject_data) and subject_data[i+1].get('pitch') == pitch:
            i += 1
            next_n = subject_data[i]
            q_len += (next_n.get('ticks') / 480.0)
            is_tied = next_n.get('tie', False)
            
        if pitch == -1:
            new_note = note.Rest(quarterLength=q_len)
        else:
            new_note = note.Note(pitch, quarterLength=q_len)

        result.insert(offset_ql, new_note) 
        i += 1
        
    return result

def create_tonal_answer(m21_stream):
    new_stream = stream.Stream()
    adjustment_active = True
    for n in m21_stream.flatten().notesAndRests:
        if n.isRest:
            new_stream.append(note.Rest(quarterLength=n.quarterLength))
        else:
            p = n.pitch.midi
            new_p = p - 5 
            if adjustment_active and p % 12 == 7:
                new_p = p - 7 
                adjustment_active = False 
            new_stream.append(note.Note(new_p, quarterLength=n.quarterLength))
    return new_stream

def diatonic_sequence(m21_stream, steps_down):
    if not m21_stream: return stream.Stream()
    c_major = [0, 2, 4, 5, 7, 9, 11] 
    new_stream = stream.Stream()
    for n in m21_stream.flatten().notesAndRests:
        if n.isRest:
            new_stream.append(note.Rest(quarterLength=n.quarterLength))
        else:
            p = n.pitch.midi
            pc = p % 12
            octave = (p // 12) * 12
            if pc in c_major:
                idx = c_major.index(pc)
            else:
                idx = min(range(len(c_major)), key=lambda i: abs(c_major[i] - pc))
                
            new_idx = idx - steps_down
            octave_shift = 0
            while new_idx < 0:
                new_idx += 7
                octave_shift -= 12
            while new_idx >= 7:
                new_idx -= 7
                octave_shift += 12
                
            new_p = c_major[new_idx] + octave + octave_shift
            new_stream.append(note.Note(new_p, quarterLength=n.quarterLength))
    return new_stream

def get_episode_plan_templates():
    return [
        {"kind": "sequence", "direction": "down", "step_size": 1, "step_count": 3, "label": "descending_step_1"},
        {"kind": "sequence", "direction": "down", "step_size": 2, "step_count": 3, "label": "descending_step_2"},
        {"kind": "sequence", "direction": "up", "step_size": 1, "step_count": 3, "label": "ascending_step_1"},
        {"kind": "sequence", "direction": "up", "step_size": 2, "step_count": 3, "label": "ascending_step_2"},
        {"kind": "false_entry", "direction": "down", "step_size": 1, "step_count": 3, "label": "false_entry_down"},
        {"kind": "false_entry", "direction": "up", "step_size": 1, "step_count": 3, "label": "false_entry_up"},
    ]

def get_episode_step_shift(episode_plan, step_index):
    if not episode_plan or step_index <= 0:
        return 0

    direction = episode_plan.get("direction", "down")
    step_size = max(1, int(episode_plan.get("step_size", 1)))
    shift = step_index * step_size
    if direction == "up":
        shift *= -1
    return shift

def apply_episode_step(m21_stream, episode_plan, voice_id, step_index):
    transformed = clone_stream(m21_stream)
    shift = get_episode_step_shift(episode_plan, step_index)
    if shift:
        transformed = diatonic_sequence(transformed, shift)
        transformed = fit_stream_to_voice_range(transformed, voice_id)
    return transformed

def extract_subject_head_prefix():
    subject_stream = blueprint.motives.get('subject')
    if not subject_stream:
        return None

    subject_items = list(subject_stream.flatten().notesAndRests)
    if not subject_items:
        return None

    subject_length = get_stream_length_16ths(subject_stream)
    max_prefix_16ths = max(2, min(6, max(2, subject_length // 2)))
    prefix = stream.Stream()
    total_16ths = 0
    event_limit = 3 if min(get_subject_durations()) == 1 else 2

    for index, item in enumerate(subject_items):
        dur_16ths = int(item.quarterLength / 0.25)
        if total_16ths > 0 and total_16ths + dur_16ths > max_prefix_16ths:
            break
        if index == len(subject_items) - 1 and total_16ths > 0:
            break

        if item.isRest:
            prefix.append(note.Rest(quarterLength=item.quarterLength))
        else:
            prefix.append(note.Note(item.pitch.midi, quarterLength=item.quarterLength))
        total_16ths += dur_16ths

        if len(list(prefix.flatten().notesAndRests)) >= event_limit or total_16ths >= max_prefix_16ths:
            break

    if get_stream_length_16ths(prefix) == 0:
        first_item = subject_items[0]
        if first_item.isRest:
            prefix.append(note.Rest(quarterLength=first_item.quarterLength))
        else:
            prefix.append(note.Note(first_item.pitch.midi, quarterLength=first_item.quarterLength))

    return prefix

def build_episode_locked_prefix(episode_plan, voice_id):
    if not episode_plan or episode_plan.get("kind") != "false_entry":
        return None

    prefix = extract_subject_head_prefix()
    if not prefix:
        return None

    source_voice_id = blueprint.motive_sources.get('subject', ROLE_SOURCE_VOICE['subject'])
    resolved_prefix = transpose_stream(prefix, (source_voice_id - voice_id) * 12)
    return fit_stream_to_voice_range(
        resolved_prefix,
        voice_id,
        reference_pitch=get_previous_voice_pitch(voice_id),
    )

def get_episode_context_pitches(instructions, target_voice_id):
    pitches = []
    for voice_id, role in instructions.items():
        if role == 'rest':
            continue
        pitch = get_previous_voice_pitch(voice_id)
        if pitch != -1:
            pitches.append(pitch)

    if not pitches:
        pitches = [VOICE_CENTERS[target_voice_id]]

    target_pitch = get_previous_voice_pitch(target_voice_id)
    if target_pitch == -1:
        target_pitch = VOICE_CENTERS[target_voice_id]

    return pitches, target_pitch

def score_episode_plan_context(episode_plan, instructions, target_voice_id):
    context_pitches, target_pitch = get_episode_context_pitches(instructions, target_voice_id)
    average_pitch = sum(context_pitches) / float(len(context_pitches))
    low, high = VOICE_RANGES[target_voice_id]
    room_below = max(0, target_pitch - low)
    room_above = max(0, high - target_pitch)

    direction = episode_plan.get("direction", "down")
    step_size = max(1, int(episode_plan.get("step_size", 1)))
    kind = episode_plan.get("kind", "sequence")

    score = 0.0
    if kind == "false_entry":
        score += 5 if blueprint.episode_count <= 1 else 1
    else:
        score -= 1

    if direction == "down":
        if average_pitch >= 68:
            score -= 10
        elif average_pitch >= 62:
            score -= 5
        elif average_pitch <= 54:
            score += 6

        if room_below < 6:
            score += 10 + (6 - room_below) * 2
        if room_above > 12 and step_size == 2:
            score -= 4
    else:
        if average_pitch <= 50:
            score -= 10
        elif average_pitch <= 58:
            score -= 5
        elif average_pitch >= 66:
            score += 6

        if room_above < 6:
            score += 10 + (6 - room_above) * 2
        if room_below > 12 and step_size == 2:
            score -= 4

    if 56 <= average_pitch <= 66 and step_size == 1:
        score -= 2
    if average_pitch >= 70 and direction == "down" and step_size == 2:
        score -= 5
    if average_pitch <= 48 and direction == "up" and step_size == 2:
        score -= 5
    if blueprint.episode_count >= 2 and kind == "false_entry":
        score -= 2

    return score

def rank_episode_plans(instructions, target_voice_id):
    ranked = []
    for plan in get_episode_plan_templates():
        ranked.append((score_episode_plan_context(plan, instructions, target_voice_id), plan))
    ranked.sort(key=lambda item: item[0])
    return [copy.deepcopy(plan) for _, plan in ranked]

def build_episode_fallback(instructions, target_gen_role, state_name, episode_plan=None):
    target_voice_id = None
    for voice_id, role in instructions.items():
        if role == target_gen_role:
            target_voice_id = voice_id
            break

    if target_voice_id is None:
        return None, None, ["Could not identify the generated episode voice."]

    support_candidates = []
    for voice_id, role in instructions.items():
        if role in ['rest', target_gen_role]:
            continue
        support_candidates.append((abs(target_voice_id - voice_id), voice_id, role))

    if not support_candidates:
        return None, None, ["No supporting motive was available for an episode fallback."]

    support_candidates.sort(key=lambda item: item[0])
    prev_pitch = get_previous_voice_pitch(target_voice_id)
    best_candidate = None

    for _, support_voice_id, support_role in support_candidates:
        support_stream = resolve_role_stream(support_role, support_voice_id, state_name, 0, episode_plan=episode_plan)
        if not get_pitch_list(support_stream):
            continue

        if episode_plan and episode_plan.get("direction") == "up":
            shift_options = [2, 1, 3, 4, -1]
        elif episode_plan and episode_plan.get("direction") == "down":
            shift_options = [-2, -1, -3, -4, 1]
        elif target_voice_id < support_voice_id:
            shift_options = [-1, -2, -3, -4, -5]
        else:
            shift_options = [1, 2, 3, 4, 5]

        if episode_plan and episode_plan.get("kind") == "false_entry":
            shift_options = [0] + [shift for shift in shift_options if shift != 0]

        for shift in shift_options:
            seed_source = support_stream if shift == 0 else diatonic_sequence(support_stream, shift)
            seed_stream = fit_stream_to_voice_range(
                seed_source,
                target_voice_id,
                reference_pitch=prev_pitch,
            )
            voice_streams = assemble_voice_streams(
                instructions,
                target_gen_role,
                seed_stream,
                state_name,
                episode_plan=episode_plan,
            )
            issues, _ = evaluate_candidate_voice_streams(voice_streams, check_weak_dissonances=True)
            score = score_generated_solution(
                seed_stream,
                voice_streams,
                target_voice_id,
                prev_pitch,
                state_name,
                issue_map=issues,
            )

            if any(issues.values()):
                score += sum(len(issue_list) for issue_list in issues.values()) * 100

            candidate = (score, seed_stream, voice_streams, issues)
            if best_candidate is None or candidate[0] < best_candidate[0]:
                best_candidate = candidate

    if best_candidate is None:
        return None, None, ["Episode fallback could not build any candidate."]

    _, seed_stream, voice_streams, issues = best_candidate
    if any(issues.values()):
        return None, None, summarize_issues(issues)

    return seed_stream, voice_streams, []

def build_middle_entry_fallback(instructions, target_gen_role, state_name):
    target_voice_id = None
    for voice_id, role in instructions.items():
        if role == target_gen_role:
            target_voice_id = voice_id
            break

    if target_voice_id is None:
        return None, None, ["Could not identify the generated middle-entry voice."]

    prev_pitch = get_previous_voice_pitch(target_voice_id)
    best_candidate = None

    for source_voice_id, role in instructions.items():
        if role in ['rest', target_gen_role]:
            continue

        source_stream = resolve_role_stream(role, source_voice_id, state_name, 0)
        if not get_pitch_list(source_stream):
            continue

        if target_voice_id > source_voice_id:
            shift_options = [2, 3, 4, 5]
        elif target_voice_id < source_voice_id:
            shift_options = [-2, -3, -4, -5]
        else:
            shift_options = [0, 2, -2]

        for shift in shift_options:
            seed_stream = fit_stream_to_voice_range(
                diatonic_sequence(source_stream, shift),
                target_voice_id,
                reference_pitch=prev_pitch,
            )
            voice_streams = assemble_voice_streams(instructions, target_gen_role, seed_stream, state_name)
            issues, _ = evaluate_candidate_voice_streams(voice_streams, check_weak_dissonances=True)
            score = score_generated_solution(
                seed_stream,
                voice_streams,
                target_voice_id,
                prev_pitch,
                state_name,
                issue_map=issues,
            )

            if any(issues.values()):
                score += sum(len(issue_list) for issue_list in issues.values()) * 100

            candidate = (score, seed_stream, voice_streams, issues)
            if best_candidate is None or candidate[0] < best_candidate[0]:
                best_candidate = candidate

    if best_candidate is None:
        return None, None, ["Middle-entry fallback could not build any candidate."]

    _, seed_stream, voice_streams, issues = best_candidate
    if any(issues.values()):
        return None, None, summarize_issues(issues)

    return seed_stream, voice_streams, []

def resolve_role_stream(role, voice_id, state_name, sequence_step=0, episode_plan=None):
    base_stream = blueprint.motives.get(role)
    if not base_stream:
        return stream.Stream()

    source_v_id = blueprint.motive_sources.get(role, ROLE_SOURCE_VOICE.get(role, 0))
    semitones = (source_v_id - voice_id) * 12
    resolved_stream = transpose_stream(base_stream, semitones)
    reference_pitch = get_previous_voice_pitch(voice_id) if sequence_step == 0 else None
    resolved_stream = fit_stream_to_voice_range(resolved_stream, voice_id, reference_pitch=reference_pitch)

    if state_name == 'EPISODE' and sequence_step > 0:
        resolved_stream = apply_episode_step(resolved_stream, episode_plan, voice_id, sequence_step)

    return resolved_stream

def assemble_voice_streams(instructions, target_gen_role, generated_stream, state_name, episode_plan=None):
    step_count = 3 if state_name == 'EPISODE' else 1
    if state_name == 'EPISODE' and episode_plan:
        step_count = max(2, int(episode_plan.get("step_count", 3)))
    voice_parts = {0: [], 1: [], 2: []}

    generated_steps = [generated_stream]
    if state_name == 'EPISODE':
        target_voice_id = next(
            (voice_id for voice_id, role in instructions.items() if role == target_gen_role),
            0,
        )
        generated_steps = [
            apply_episode_step(generated_stream, episode_plan, target_voice_id, step)
            for step in range(step_count)
        ]

    for step_index in range(step_count):
        step_stream = generated_steps[step_index]
        step_length = get_stream_length_16ths(step_stream)

        for voice_id, role in instructions.items():
            if role == target_gen_role:
                current_stream = step_stream
            elif role == 'rest':
                current_stream = make_rest_stream(step_length)
            else:
                current_stream = resolve_role_stream(
                    role,
                    voice_id,
                    state_name,
                    step_index,
                    episode_plan=episode_plan,
                )

            voice_parts[voice_id].append(current_stream)

    return {
        voice_id: concatenate_streams(parts)
        for voice_id, parts in voice_parts.items()
    }

def stream_to_payload(m21_stream):
    notes = []
    for n in m21_stream.flatten().notesAndRests:
        pitch = -1 if n.isRest else n.pitch.midi
        ticks = int(n.quarterLength * 480)
        notes.append({"pitch": pitch, "ticks": ticks})
    return notes

def prepare_generation_context(instructions, target_gen_role, state_name):
    existing_streams = []
    prev_gen_pitch = -1
    prev_ext_pitches = []
    active_voice_id = 0

    for v_id in sorted(instructions.keys()):
        role = instructions[v_id]
        if role == target_gen_role:
            prev_gen_pitch = get_previous_voice_pitch(v_id)
            active_voice_id = v_id
        elif role == 'rest':
            prev_ext_pitches.append(-1)
        else:
            role_stream = resolve_role_stream(role, v_id, state_name)
            existing_streams.append(role_stream)
            prev_ext_pitches.append(get_last_pitch(role_stream))

    return existing_streams, prev_ext_pitches, active_voice_id, prev_gen_pitch

def build_solver_for_attempt(existing_streams, active_voice_id, prev_gen_pitch, prev_ext_pitches, state_name, episode_plan=None):
    locked_prefix = None
    note_budget = get_note_budget(state_name)
    if state_name == 'EPISODE':
        locked_prefix = build_episode_locked_prefix(episode_plan, active_voice_id)
        if locked_prefix:
            locked_note_count = len(list(locked_prefix.flatten().notesAndRests))
            note_budget = max(note_budget, locked_note_count + 2)

    solver = FugueSolver(
        existing_streams,
        target_notes=note_budget,
        prev_gen_pitch=prev_gen_pitch,
        prev_ext_pitches=prev_ext_pitches,
        strict_invertible=(state_name == 'EXPO_2'),
        voice_id=active_voice_id,
        target_chord=blueprint.current_harmony,
        allowed_durations=get_allowed_durations(state_name),
        locked_prefix=locked_prefix,
    )
    solver.setup_rules()
    return solver

def collect_valid_solutions(fs, instructions, target_gen_role, state_name, active_voice_id, prev_pitch, used_signatures=None, max_attempts=None, desired_count=1, episode_plan=None, plan_penalty=0.0):
    last_issues = {}
    check_weak_dissonances = True
    if max_attempts is not None:
        attempt_limit = max_attempts
    elif state_name == 'MIDDLE_ENTRY':
        attempt_limit = 160
    elif state_name == 'EPISODE':
        attempt_limit = 120
    else:
        attempt_limit = 72
    candidate_goal = max(1, desired_count)
    valid_candidates = []
    used_signatures = used_signatures or set()

    for _ in range(attempt_limit):
        generated_stream = fs.generate_next_solution()
        if not generated_stream:
            break

        voice_streams = assemble_voice_streams(
            instructions,
            target_gen_role,
            generated_stream,
            state_name,
            episode_plan=episode_plan,
        )
        signature = voice_streams_signature(voice_streams)
        if signature in used_signatures:
            continue

        issues, _ = evaluate_candidate_voice_streams(
            voice_streams,
            check_weak_dissonances=check_weak_dissonances,
        )
        if not any(issues.values()):
            score = score_generated_solution(
                generated_stream,
                voice_streams,
                active_voice_id,
                prev_pitch,
                state_name,
                issue_map=issues,
            )
            valid_candidates.append(
                build_candidate_entry(
                    generated_stream,
                    voice_streams,
                    score + plan_penalty,
                    episode_plan=episode_plan,
                )
            )
            if len(valid_candidates) >= candidate_goal:
                break
            continue

        last_issues = issues

    if valid_candidates:
        ordered = []
        ordered_signatures = set()
        for candidate in sorted(valid_candidates, key=lambda item: item["score"]):
            signature = candidate_signature(candidate)
            if signature in ordered_signatures:
                continue
            ordered.append(candidate)
            ordered_signatures.add(signature)
            if len(ordered) >= desired_count:
                break
        return ordered, []

    if state_name == 'EPISODE':
        if episode_plan and episode_plan.get("kind") == "false_entry":
            return [], summarize_issues(last_issues)
        fallback_stream, fallback_voices, fallback_issues = build_episode_fallback(
            instructions,
            target_gen_role,
            state_name,
            episode_plan=episode_plan,
        )
        fallback_signature = voice_streams_signature(fallback_voices) if fallback_voices else None
        if fallback_stream and fallback_voices and fallback_signature not in used_signatures:
            fallback_score = score_generated_solution(
                fallback_stream,
                fallback_voices,
                active_voice_id,
                prev_pitch,
                state_name,
            )
            return [
                build_candidate_entry(
                    fallback_stream,
                    fallback_voices,
                    fallback_score + plan_penalty,
                    episode_plan=episode_plan,
                )
            ], []
        if fallback_issues:
            return [], fallback_issues

    if state_name == 'MIDDLE_ENTRY':
        fallback_stream, fallback_voices, fallback_issues = build_middle_entry_fallback(instructions, target_gen_role, state_name)
        fallback_signature = voice_streams_signature(fallback_voices) if fallback_voices else None
        if fallback_stream and fallback_voices and fallback_signature not in used_signatures:
            fallback_score = score_generated_solution(
                fallback_stream,
                fallback_voices,
                active_voice_id,
                prev_pitch,
                state_name,
            )
            return [build_candidate_entry(fallback_stream, fallback_voices, fallback_score)], []
        if fallback_issues:
            return [], fallback_issues

    return [], summarize_issues(last_issues)

def collect_state_candidates(instructions, target_gen_role, state_name, active_voice_id, prev_pitch, existing_streams, prev_ext_pitches, used_signatures=None, desired_count=1):
    used_signatures = used_signatures or set()
    if state_name != 'EPISODE':
        solver = build_solver_for_attempt(
            existing_streams,
            active_voice_id,
            prev_pitch,
            prev_ext_pitches,
            state_name,
        )
        return collect_valid_solutions(
            solver,
            instructions,
            target_gen_role,
            state_name,
            active_voice_id,
            prev_pitch,
            used_signatures=used_signatures,
            desired_count=desired_count,
        )

    all_candidates = []
    last_issues = []
    seen_signatures = set(used_signatures)
    ranked_plans = rank_episode_plans(instructions, active_voice_id)
    primary_plan_count = min(4, len(ranked_plans))

    def extend_with_plan(plan, plan_index, per_plan_goal):
        nonlocal all_candidates, last_issues, seen_signatures
        solver = build_solver_for_attempt(
            existing_streams,
            active_voice_id,
            prev_pitch,
            prev_ext_pitches,
            state_name,
            episode_plan=plan,
        )
        candidates, issues = collect_valid_solutions(
            solver,
            instructions,
            target_gen_role,
            state_name,
            active_voice_id,
            prev_pitch,
            used_signatures=seen_signatures,
            desired_count=per_plan_goal,
            episode_plan=plan,
            plan_penalty=plan_index * 6.0,
        )
        if candidates:
            for candidate in candidates:
                signature = candidate_signature(candidate)
                if signature in seen_signatures:
                    continue
                all_candidates.append(candidate)
                seen_signatures.add(signature)
        elif issues:
            last_issues = issues

    for plan_index, plan in enumerate(ranked_plans[:primary_plan_count]):
        extend_with_plan(plan, plan_index, 2)

    if len(all_candidates) < desired_count:
        for plan_index, plan in enumerate(ranked_plans[primary_plan_count:], start=primary_plan_count):
            extend_with_plan(plan, plan_index, 1)
            if len(all_candidates) >= desired_count:
                break

    if all_candidates:
        ordered = []
        ordered_signatures = set()
        for candidate in sorted(all_candidates, key=lambda item: item["score"]):
            signature = candidate_signature(candidate)
            if signature in ordered_signatures:
                continue
            ordered.append(candidate)
            ordered_signatures.add(signature)
            if len(ordered) >= desired_count:
                break
        return ordered, []

    return [], last_issues

def find_valid_solution(fs, instructions, target_gen_role, state_name, active_voice_id, prev_pitch, used_signatures=None, max_attempts=None):
    candidates, issues = collect_valid_solutions(
        fs,
        instructions,
        target_gen_role,
        state_name,
        active_voice_id,
        prev_pitch,
        used_signatures=used_signatures,
        max_attempts=max_attempts,
        desired_count=1,
    )
    if candidates:
        generated_stream = candidates[0]["generated_stream"]
        voice_streams = candidates[0]["voice_streams"]
        return generated_stream, voice_streams, []
    return None, None, issues

def build_solution_response(voice_streams):
    multiplier = 3 if blueprint.state == 'EPISODE' else 1
    measure_voices = {
        voice_id: stream_to_payload(voice_streams[voice_id])
        for voice_id in voice_streams
    }

    return jsonify({
        "status": "success",
        "next_state": blueprint.state,
        "duration_multiplier": multiplier,
        "solution": {"voice_0": measure_voices[0], "voice_1": measure_voices[1], "voice_2": measure_voices[2]}
    })

# --- ENDPOINTS ---
@app.route('/generate', methods=['POST'])
def generate_measure():
    global blueprint

    data = request.json
    action = data.get('action', 'new')
    decision = data.get('decision', 'auto')
    selected_idx = int(data.get('selected_index', 0) or 0)
    meter_payload = data.get('meter_info') or {}
    blueprint_snapshot = None
    state_snapshot = None
    active_voice_id = global_state.get('active_voice_id', 0)
    prev_gen_pitch = global_state.get('anchor_pitch', -1)
    
    if decision == 'reset':
        reset_runtime()
        return jsonify({"status": "success", "next_state": "INITIAL", "duration_multiplier": 1, "solution": None})
        
    if decision in ['real_answer', 'tonal_answer']:
        reset_runtime()
    elif blueprint.state == 'INITIAL' and decision not in ['real_answer', 'tonal_answer', 'auto']:
        return jsonify({"status": "error", "message": "Server memory was lost. Please click 'Reset Generator' in the UI."})
    
    if action == 'new':
        if global_state.get('generated_streams'):
            if len(global_state['generated_streams']) > selected_idx:
                chosen_entry = global_state['generated_streams'][selected_idx]
                chosen = chosen_entry['generated_stream']
                committed_voice_streams = chosen_entry['voice_streams']
                global_state['last_voice_streams'] = copy_voice_streams(committed_voice_streams)
                global_state['committed_history_streams'] = extend_voice_streams(
                    global_state.get('committed_history_streams', make_empty_voice_streams()),
                    committed_voice_streams,
                )
                if blueprint.state == 'EXPO_2':
                    blueprint.motives['cs1'] = chosen
                    blueprint.motive_sources['cs1'] = global_state.get('active_voice_id', 0)
                elif blueprint.state == 'EXPO_3':
                    blueprint.motives['cs2'] = chosen
                    blueprint.motive_sources['cs2'] = global_state.get('active_voice_id', 0)
                elif blueprint.state in ['EPISODE', 'MIDDLE_ENTRY']:
                    blueprint.motives['free_melody'] = chosen
                    blueprint.motive_sources['free_melody'] = global_state.get('active_voice_id', 0)
                    if blueprint.state == 'EPISODE':
                        blueprint.motives['episode_line'] = chosen
                        blueprint.motive_sources['episode_line'] = global_state.get('active_voice_id', 0)

        if blueprint.state == 'INITIAL':
            blueprint.motives['subject'] = convert_json_to_stream(data)
            blueprint.motive_sources['subject'] = 1
            if decision == 'tonal_answer':
                blueprint.motives['answer'] = create_tonal_answer(blueprint.motives['subject'])
            else:
                blueprint.motives['answer'] = transpose_stream(blueprint.motives['subject'], -5)
            blueprint.motive_sources['answer'] = 2
            global_state['meter_info'] = {
                "numerator": max(1, int(meter_payload.get("numerator", 4))),
                "denominator": max(1, int(meter_payload.get("denominator", 4))),
            }
            global_state['last_voice_streams'] = {
                0: clone_stream(blueprint.motives['subject']),
                1: stream.Stream(),
                2: stream.Stream(),
            }
            subject_length = get_stream_length_16ths(blueprint.motives['subject'])
            global_state['committed_history_streams'] = {
                0: clone_stream(blueprint.motives['subject']),
                1: make_rest_stream(subject_length),
                2: make_rest_stream(subject_length),
            }

        blueprint_snapshot = copy.deepcopy(blueprint)
        state_snapshot = {
            'solver': global_state.get('solver'),
            'generated_streams': copy_candidate_entries(global_state.get('generated_streams', [])),
            'last_voice_streams': copy_voice_streams(global_state.get('last_voice_streams', {})),
            'committed_history_streams': copy_voice_streams(global_state.get('committed_history_streams', {})),
            'meter_info': dict(global_state.get('meter_info', {})),
            'instructions': global_state.get('instructions'),
            'target_gen_role': global_state.get('target_gen_role'),
            'active_voice_id': global_state.get('active_voice_id'),
            'anchor_pitch': global_state.get('anchor_pitch'),
        }

        instructions = blueprint.advance(decision)
        global_state['generated_streams'] = []
        
        gen_role_map = {'EXPO_2': 'cs1', 'EXPO_3': 'cs2', 'EPISODE': 'free_melody', 'MIDDLE_ENTRY': 'free_melody'}
        target_gen_role = gen_role_map.get(blueprint.state, 'free_melody')
        _, _, active_voice_id, prev_gen_pitch = prepare_generation_context(
            instructions,
            target_gen_role,
            blueprint.state,
        )

        global_state['solver'] = None
        global_state['instructions'] = instructions
        global_state['target_gen_role'] = target_gen_role
        global_state['active_voice_id'] = active_voice_id
        global_state['anchor_pitch'] = prev_gen_pitch

    instructions = global_state['instructions']
    target_gen_role = global_state['target_gen_role']
    active_voice_id = global_state.get('active_voice_id', active_voice_id)
    prev_gen_pitch = global_state.get('anchor_pitch', prev_gen_pitch)
    existing_streams, prev_ext_pitches, _, _ = prepare_generation_context(
        instructions,
        target_gen_role,
        blueprint.state,
    )

    if action == 'next' and len(global_state.get('generated_streams', [])) > selected_idx + 1:
        return build_solution_response(global_state['generated_streams'][selected_idx + 1]['voice_streams'])

    used_signatures = {
        candidate_signature(candidate)
        for candidate in global_state.get('generated_streams', [])
    }

    desired_count = 4 if action == 'new' else 3
    candidate_solutions, issue_summary = collect_state_candidates(
        instructions,
        target_gen_role,
        blueprint.state,
        active_voice_id,
        prev_gen_pitch,
        existing_streams,
        prev_ext_pitches,
        used_signatures=used_signatures,
        desired_count=desired_count,
    )
    if not candidate_solutions:
        if action == 'new' and blueprint_snapshot is not None and state_snapshot is not None:
            blueprint = blueprint_snapshot
            global_state['solver'] = state_snapshot['solver']
            global_state['generated_streams'] = copy_candidate_entries(state_snapshot['generated_streams'])
            global_state['last_voice_streams'] = state_snapshot['last_voice_streams']
            global_state['committed_history_streams'] = state_snapshot['committed_history_streams']
            global_state['meter_info'] = state_snapshot['meter_info']
            if state_snapshot['instructions'] is not None:
                global_state['instructions'] = state_snapshot['instructions']
            else:
                global_state.pop('instructions', None)
            if state_snapshot['target_gen_role'] is not None:
                global_state['target_gen_role'] = state_snapshot['target_gen_role']
            else:
                global_state.pop('target_gen_role', None)
            if state_snapshot['active_voice_id'] is not None:
                global_state['active_voice_id'] = state_snapshot['active_voice_id']
            else:
                global_state.pop('active_voice_id', None)
            if state_snapshot['anchor_pitch'] is not None:
                global_state['anchor_pitch'] = state_snapshot['anchor_pitch']
            else:
                global_state.pop('anchor_pitch', None)

        message = "Z3 exhausted solutions."
        if issue_summary:
            message = f"No valid solution survived the post-checks. Last issues: {' | '.join(issue_summary)}"
        return jsonify({"status": "error", "message": message})

    existing_signatures = {
        candidate_signature(candidate)
        for candidate in global_state.get('generated_streams', [])
    }
    new_candidates = []
    for candidate in candidate_solutions:
        signature = candidate_signature(candidate)
        if signature in existing_signatures:
            continue
        new_candidates.append(candidate)
        existing_signatures.add(signature)

    if action == 'new':
        global_state['generated_streams'] = copy_candidate_entries(new_candidates)
    else:
        global_state['generated_streams'].extend(copy_candidate_entries(new_candidates))

    ranked_candidates = sorted(
        candidate_solutions,
        key=lambda item: (
            item['score'] - (score_solution(item['voice_streams'], blueprint) / 25.0),
            item['score'],
        ),
    )

    voice_streams = ranked_candidates[0]['voice_streams']
    if action == 'new':
        global_state['last_voice_streams'] = copy_voice_streams(voice_streams)

    return build_solution_response(voice_streams)

@app.route('/evaluate', methods=['POST'])
def evaluate_fugue():
    data = request.json
    voice_streams = {
        0: convert_json_to_stream({'subject': data.get('voice_0', [])}),
        1: convert_json_to_stream({'subject': data.get('voice_1', [])}),
        2: convert_json_to_stream({'subject': data.get('voice_2', [])}),
    }
    meter_info = {
        "numerator": max(1, int(data.get('timesig_numerator', 4) or 4)),
        "denominator": max(1, int(data.get('timesig_denominator', 4) or 4)),
    }

    issue_events = evaluate_counterpoint_issues(
        voice_streams,
        meter_info=meter_info,
    )
    all_mistakes = format_issue_report([
        {
            "offset": issue["offset"],
            "location": issue["location"],
            "message": issue["summary"],
        }
        for issue in issue_events
    ])

    return jsonify({
        "mistakes": all_mistakes,
        "issues": issue_events,
    })

if __name__ == '__main__':
    app.run(debug=True)
