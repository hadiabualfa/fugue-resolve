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
VOICE_IDS = (0, 1, 2)
DEFAULT_METER_INFO = {"numerator": 4, "denominator": 4}
DEFAULT_KEY_INFO = {"accidentals": 0}
ALL_DURATIONS = [1, 2, 4, 8]
MAJOR_SCALE_STEPS = (0, 2, 4, 5, 7, 9, 11)
KEYSIG_TO_TONIC_PC = {
    -7: 11, -6: 6, -5: 1, -4: 8, -3: 3, -2: 10, -1: 5,
     0: 0,
     1: 7,  2: 2,  3: 9,  4: 4,  5: 11,  6: 6,  7: 1,
}
GEN_ROLE_BY_STATE = {
    'EXPO_2': 'cs1',
    'EXPO_3': 'cs2',
    'EPISODE': 'free_melody',
    'MIDDLE_ENTRY': 'free_melody',
}
ATTEMPT_LIMITS = {
    'DEFAULT': 72,
    'EPISODE': 120,
    'MIDDLE_ENTRY': 160,
}
EPISODE_PLAN_TEMPLATES = [
    {"kind": "sequence", "direction": "down", "support_direction": "down", "generated_direction": "down", "step_size": 1, "step_count": 3, "label": "descending_step_1"},
    {"kind": "sequence", "direction": "down", "support_direction": "down", "generated_direction": "down", "step_size": 2, "step_count": 3, "label": "descending_step_2"},
    {"kind": "sequence", "direction": "up", "support_direction": "up", "generated_direction": "up", "step_size": 1, "step_count": 3, "label": "ascending_step_1"},
    {"kind": "sequence", "direction": "up", "support_direction": "up", "generated_direction": "up", "step_size": 2, "step_count": 3, "label": "ascending_step_2"},
    {"kind": "sequence", "direction": "down", "support_direction": "down", "generated_direction": "up", "step_size": 1, "step_count": 3, "label": "contrary_support_down_step_1"},
    {"kind": "sequence", "direction": "up", "support_direction": "up", "generated_direction": "down", "step_size": 1, "step_count": 3, "label": "contrary_support_up_step_1"},
    {"kind": "false_entry", "direction": "down", "support_direction": "down", "generated_direction": "down", "step_size": 1, "step_count": 3, "label": "false_entry_down"},
    {"kind": "false_entry", "direction": "up", "support_direction": "up", "generated_direction": "up", "step_size": 1, "step_count": 3, "label": "false_entry_up"},
]

# --- HELPERS ---
# Build an empty three-voice stream bundle for runtime state.
def make_empty_voice_streams():
    return {voice_id: stream.Stream() for voice_id in VOICE_IDS}

global_state = {
    "generated_streams": [],
    "last_voice_streams": {},
    "committed_history_streams": make_empty_voice_streams(),
    "section_history": [],
    "meter_info": DEFAULT_METER_INFO.copy(),
    "key_info": DEFAULT_KEY_INFO.copy(),
    "tonic_pc": 0,
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

# Return the last sounding pitch in a stream, or -1 if none exists.
def get_last_pitch(m21_stream):
    if not m21_stream: return -1
    notes = list(m21_stream.flatten().notesAndRests)
    for n in reversed(notes):
        if not n.isRest: return n.pitch.midi
    return -1

# Normalize incoming key data to a supported key-signature accidentals count.
def normalize_key_info(key_info=None):
    key_info = key_info or {}
    accidentals = int(key_info.get('accidentals', DEFAULT_KEY_INFO['accidentals']))
    return {"accidentals": max(-7, min(7, accidentals))}

# Infer a tonic pitch class from the first sounding note of the subject.
def infer_subject_tonic_pc(subject_stream):
    first_pitch = get_first_pitch(subject_stream)
    return 0 if first_pitch == -1 else first_pitch % 12

# Read the active tonic pitch class from runtime state.
def get_tonic_pc():
    return int(global_state.get('tonic_pc', 0)) % 12

# Return the major-scale pitch classes for the active fugue key.
def get_scale_pitch_classes(tonic_pc=None):
    tonic_pc = get_tonic_pc() if tonic_pc is None else (tonic_pc % 12)
    return tuple((tonic_pc + step) % 12 for step in MAJOR_SCALE_STEPS)

# Return the tonic, subdominant, or dominant triad pitch classes for the active key.
def get_harmony_pitch_classes(harmony_name, tonic_pc=None):
    tonic_pc = get_tonic_pc() if tonic_pc is None else (tonic_pc % 12)
    if harmony_name == 'I':
        offsets = (0, 4, 7)
    elif harmony_name == 'IV':
        offsets = (5, 9, 0)
    elif harmony_name == 'V':
        offsets = (7, 11, 2)
    else:
        return []
    return [((tonic_pc + offset) % 12) for offset in offsets]

# Build an ordered ladder of in-key pitches across several octaves.
def get_scale_pitch_ladder(tonic_pc=None, low=-24, high=151):
    tonic_pc = get_tonic_pc() if tonic_pc is None else (tonic_pc % 12)
    ladder = []
    for octave in range(-2, 13):
        base = tonic_pc + (12 * octave)
        for step in MAJOR_SCALE_STEPS:
            pitch = base + step
            if low <= pitch <= high:
                ladder.append(pitch)
    return sorted(set(ladder))

# Score a full three-voice candidate using high-level musical heuristics.
def score_solution(voice_streams, blueprint):
    score = 0
    depth = blueprint.episode_count + blueprint.middle_entry_count
    is_developing_stage = (depth >= 2)
    tonic_pc = get_tonic_pc()
    leading_pc = (tonic_pc + 11) % 12
    dominant_pc = (tonic_pc + 7) % 12

    v0_notes = list(voice_streams[0].flatten().notesAndRests) if voice_streams.get(0) else []
    v2_notes = list(voice_streams[2].flatten().notesAndRests) if voice_streams.get(2) else []

    for m21_stream in voice_streams.values():
        if not m21_stream: continue
        for n in m21_stream.flatten().notesAndRests:
            if not n.isRest:
                if n.quarterLength <= 0.25:
                    if is_developing_stage: score += 15
                    else: score -= 15

    if blueprint.current_harmony == 'I':
        if len(v0_notes) > 1:
            for i in range(len(v0_notes) - 1):
                if not v0_notes[i].isRest and not v0_notes[i+1].isRest:
                    if v0_notes[i].pitch.midi % 12 == leading_pc and v0_notes[i+1].pitch.midi % 12 == tonic_pc:
                        score += 50

        if len(v2_notes) > 1:
            for i in range(len(v2_notes) - 1):
                if not v2_notes[i].isRest and not v2_notes[i+1].isRest:
                    if v2_notes[i].pitch.midi % 12 == dominant_pc and v2_notes[i+1].pitch.midi % 12 == tonic_pc:
                        score += 50

    if len(v0_notes) > 1 and len(v2_notes) > 1:
        limit = min(len(v0_notes), len(v2_notes))
        for i in range(limit - 1):
            if not v0_notes[i].isRest and not v0_notes[i+1].isRest and not v2_notes[i].isRest and not v2_notes[i+1].isRest:
                top_int = v0_notes[i+1].pitch.midi - v0_notes[i].pitch.midi
                bass_int = v2_notes[i+1].pitch.midi - v2_notes[i].pitch.midi
                if (top_int > 0 and bass_int < 0) or (top_int < 0 and bass_int > 0):
                    score += 20

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

# Reset the blueprint and all cached generation state.
def reset_runtime():
    global blueprint
    blueprint = FugueBlueprint()
    global_state['generated_streams'] = []
    global_state['last_voice_streams'] = {}
    global_state['committed_history_streams'] = make_empty_voice_streams()
    global_state['section_history'] = []
    global_state['meter_info'] = DEFAULT_METER_INFO.copy()
    global_state['key_info'] = DEFAULT_KEY_INFO.copy()
    global_state['tonic_pc'] = 0
    global_state.pop('instructions', None)
    global_state.pop('target_gen_role', None)
    global_state.pop('active_voice_id', None)
    global_state.pop('anchor_pitch', None)

# Normalize incoming meter data to a safe numerator/denominator pair.
def normalize_meter_info(meter_info=None):
    meter_info = meter_info or {}
    return {
        "numerator": max(1, int(meter_info.get('numerator', DEFAULT_METER_INFO['numerator']))),
        "denominator": max(1, int(meter_info.get('denominator', DEFAULT_METER_INFO['denominator']))),
    }

# Convert a per-voice issue map into short display summaries.
def summarize_issues(issue_map):
    summaries = []
    for voice_id in sorted(issue_map):
        if issue_map[voice_id]:
            summaries.append(f"{VOICE_NAMES[voice_id]}: {'; '.join(issue_map[voice_id][:3])}")
    return summaries

# Format a generation failure message as a short bulleted block for the plugin UI.
def format_generation_message(title, details=None):
    details = details or []
    if not details:
        return title
    return "\n".join([title] + [f"- {detail}" for detail in details])

# Read the currently active meter information from runtime state.
def get_meter_info():
    return normalize_meter_info(global_state.get('meter_info'))

# Strip measure and beat text from an issue string for grouped reporting.
def strip_issue_location(message):
    for marker in [" at m.", " near m."]:
        index = message.find(marker)
        if index != -1:
            return message[:index].rstrip(".")
    return message.rstrip(".")

# Group structured issue events into a compact textual report.
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

# Choose a note budget for the next solve based on subject length and rhythm.
def get_note_budget(state_name):
    subject_stream = blueprint.motives.get('subject')
    if not subject_stream:
        return 4

    allowed_durations = get_allowed_durations(state_name)
    shortest_allowed = min(allowed_durations) if allowed_durations else 1
    total_16ths = max(1, get_stream_length_16ths(subject_stream))
    return max(1, total_16ths // shortest_allowed)

# Extract the distinct note lengths used by the stored subject.
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

# Detect whether the current subject mixes multiple rhythmic values.
def subject_has_mixed_rhythm():
    return len(get_subject_durations()) > 1

# Limit allowed durations for a state based on the subject's rhythm profile.
def get_allowed_durations(state_name):
    subject_durations = get_subject_durations()
    shortest_subject = min(subject_durations) if subject_durations else 2

    if state_name == 'EPISODE':
        shortest_allowed = max(1, shortest_subject // 2)
    else:
        shortest_allowed = shortest_subject

    return [value for value in ALL_DURATIONS if value >= shortest_allowed]

# Return the sounding MIDI pitches from a stream.
def get_pitch_list(m21_stream):
    return [n.pitch.midi for n in m21_stream.flatten().notesAndRests if not n.isRest]

# Return the first sounding pitch in a stream, or -1 if none exists.
def get_first_pitch(m21_stream):
    if not m21_stream:
        return -1

    for n in m21_stream.flatten().notesAndRests:
        if not n.isRest:
            return n.pitch.midi
    return -1

# Deep-copy a dictionary of per-voice streams.
def copy_voice_streams(voice_streams):
    return {
        voice_id: clone_stream(m21_stream)
        for voice_id, m21_stream in voice_streams.items()
    }

# Build a hashable signature for a full three-voice texture.
def voice_streams_signature(voice_streams):
    return tuple(
        (
            voice_id,
            stream_signature(voice_streams.get(voice_id, stream.Stream())),
        )
        for voice_id in VOICE_IDS
    )

# Package a generated stream, rendered voices, and score into one candidate record.
def build_candidate_entry(generated_stream, voice_streams, score, episode_plan=None):
    return {
        "generated_stream": clone_stream(generated_stream),
        "voice_streams": copy_voice_streams(voice_streams),
        "score": float(score),
        "episode_plan": copy.deepcopy(episode_plan),
    }

# Deep-copy one committed section entry.
def copy_section_entry(section_entry):
    return {
        "state_name": section_entry["state_name"],
        "source_voice_id": section_entry["source_voice_id"],
        "target_role": section_entry["target_role"],
        "section_length_16ths": section_entry["section_length_16ths"],
        "generated_length_16ths": section_entry["generated_length_16ths"],
        "generated_stream": clone_stream(section_entry["generated_stream"]),
        "voice_streams": copy_voice_streams(section_entry["voice_streams"]),
    }

# Deep-copy the committed section history.
def copy_section_history(section_history):
    return [copy_section_entry(entry) for entry in section_history]

# Return a stable signature for one candidate record.
def candidate_signature(candidate):
    return voice_streams_signature(candidate.get("voice_streams", {}))

# Deep-copy a list of candidate records.
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

# Snapshot the mutable runtime state before a risky generation step.
def snapshot_runtime_state():
    return {
        'generated_streams': copy_candidate_entries(global_state.get('generated_streams', [])),
        'last_voice_streams': copy_voice_streams(global_state.get('last_voice_streams', {})),
        'committed_history_streams': copy_voice_streams(global_state.get('committed_history_streams', {})),
        'section_history': copy_section_history(global_state.get('section_history', [])),
        'meter_info': normalize_meter_info(global_state.get('meter_info')),
        'key_info': normalize_key_info(global_state.get('key_info')),
        'tonic_pc': get_tonic_pc(),
        'instructions': global_state.get('instructions'),
        'target_gen_role': global_state.get('target_gen_role'),
        'active_voice_id': global_state.get('active_voice_id'),
        'anchor_pitch': global_state.get('anchor_pitch'),
    }

# Restore a previously saved runtime snapshot after a failed generation step.
def restore_runtime_state(state_snapshot):
    global_state['generated_streams'] = copy_candidate_entries(state_snapshot['generated_streams'])
    global_state['last_voice_streams'] = state_snapshot['last_voice_streams']
    global_state['committed_history_streams'] = state_snapshot['committed_history_streams']
    global_state['section_history'] = copy_section_history(state_snapshot.get('section_history', []))
    global_state['meter_info'] = state_snapshot['meter_info']
    global_state['key_info'] = state_snapshot.get('key_info', DEFAULT_KEY_INFO.copy())
    global_state['tonic_pc'] = state_snapshot.get('tonic_pc', 0)

    for key in ['instructions', 'target_gen_role', 'active_voice_id', 'anchor_pitch']:
        value = state_snapshot.get(key)
        if value is None:
            global_state.pop(key, None)
        else:
            global_state[key] = value

# Append newly rendered material onto committed voice-history streams.
def extend_voice_streams(base_voice_streams, new_voice_streams):
    return {
        voice_id: concatenate_streams([
            base_voice_streams.get(voice_id, stream.Stream()),
            new_voice_streams.get(voice_id, stream.Stream()),
        ])
        for voice_id in VOICE_IDS
    }

# Keep only the issues introduced by the candidate beyond existing history.
def subtract_issue_maps(base_issue_map, combined_issue_map):
    issue_delta = {voice_id: [] for voice_id in VOICE_IDS}
    for voice_id in issue_delta:
        seen = set(base_issue_map.get(voice_id, []))
        issue_delta[voice_id] = [
            issue for issue in combined_issue_map.get(voice_id, [])
            if issue not in seen
        ]
    return issue_delta

# Evaluate a candidate in full fugue context and return only its new issues.
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

# Penalize awkward handoffs between committed history and a new candidate.
def score_boundary_continuity(history_streams, voice_streams, state_name):
    score = 0.0
    subject_voice = blueprint.last_subject_voice if state_name == 'MIDDLE_ENTRY' else None

    for voice_id in VOICE_IDS:
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

# Find the most recent pitch available for a voice across history and motives.
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

# Expand a stream into a per-16th pitch grid for local registral checks.
def stream_to_pitch_grid(m21_stream, total_16ths):
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

# Fit a stream into a voice's register while minimizing awkward displacement.
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

# Penalize simultaneous crossings between an upper and lower stream.
def stream_crossing_penalty(upper_stream, lower_stream):
    total_16ths = max(
        get_stream_length_16ths(upper_stream),
        get_stream_length_16ths(lower_stream),
    )
    if total_16ths <= 0:
        return 0

    upper_grid = stream_to_pitch_grid(upper_stream, total_16ths)
    lower_grid = stream_to_pitch_grid(lower_stream, total_16ths)
    penalty = 0
    for upper_pitch, lower_pitch in zip(upper_grid, lower_grid):
        if -1 in (upper_pitch, lower_pitch):
            continue
        if upper_pitch <= lower_pitch:
            penalty += 200 + ((lower_pitch - upper_pitch) * 12)
    return penalty

# Refit one stream relative to a neighbor so their registral order is preserved.
def fit_stream_relative_to_neighbor(m21_stream, voice_id, neighbor_stream, should_be_above, reference_pitch=None):
    pitches = get_pitch_list(m21_stream)
    if not pitches:
        return clone_stream(m21_stream)

    low, high = VOICE_RANGES[voice_id]
    center = VOICE_CENTERS[voice_id]
    best_candidate = None

    for octave_shift in range(-2, 3):
        shifted_stream = transpose_stream(m21_stream, octave_shift * 12)
        shifted_pitches = get_pitch_list(shifted_stream)
        if not shifted_pitches:
            continue
        if min(shifted_pitches) < low or max(shifted_pitches) > high:
            continue

        score = sum(abs(pitch - center) for pitch in shifted_pitches)
        if reference_pitch not in (-1, None):
            score += abs(shifted_pitches[0] - reference_pitch) * 2

        if should_be_above:
            score += stream_crossing_penalty(shifted_stream, neighbor_stream)
        else:
            score += stream_crossing_penalty(neighbor_stream, shifted_stream)

        candidate = (score, shifted_stream)
        if best_candidate is None or candidate[0] < best_candidate[0]:
            best_candidate = candidate

    if best_candidate:
        return best_candidate[1]

    return fit_stream_to_voice_range(m21_stream, voice_id, reference_pitch=reference_pitch)

# Adjust fixed voice streams to reduce built-in crossings before solving or scoring.
def stabilize_voice_stream_order(voice_streams, protected_voice_ids=None):
    protected_voice_ids = set(protected_voice_ids or [])
    stabilized = copy_voice_streams(voice_streams)

    if 0 in stabilized and 1 in stabilized:
        if 0 in protected_voice_ids and 1 not in protected_voice_ids:
            stabilized[1] = fit_stream_relative_to_neighbor(
                stabilized[1],
                1,
                stabilized[0],
                should_be_above=False,
                reference_pitch=get_previous_voice_pitch(1),
            )
        else:
            stabilized[0] = fit_stream_relative_to_neighbor(
                stabilized[0],
                0,
                stabilized[1],
                should_be_above=True,
                reference_pitch=get_previous_voice_pitch(0),
            )

    if 1 in stabilized and 2 in stabilized:
        if 2 in protected_voice_ids and 1 not in protected_voice_ids:
            stabilized[1] = fit_stream_relative_to_neighbor(
                stabilized[1],
                1,
                stabilized[2],
                should_be_above=True,
                reference_pitch=get_previous_voice_pitch(1),
            )
        else:
            stabilized[2] = fit_stream_relative_to_neighbor(
                stabilized[2],
                2,
                stabilized[1],
                should_be_above=False,
                reference_pitch=get_previous_voice_pitch(2),
            )

    return stabilized

# Build a compact pitch-and-duration signature for one stream.
def stream_signature(m21_stream):
    return tuple(
        (
            -1 if n.isRest else n.pitch.midi,
            int(n.quarterLength / 0.25),
        )
        for n in m21_stream.flatten().notesAndRests
    )

# Check whether an interval is consonant for a specific voice pair.
def is_consonant_for_pair(voice_a, voice_b, interval):
    if 2 in (voice_a, voice_b):
        return interval in BASS_CONSONANCES
    return interval in UPPER_VOICE_CONSONANCES

# Copy the opening portion of a stream measured in 16th-note units.
def take_stream_prefix(m21_stream, prefix_16ths):
    prefix = stream.Stream()
    copied = 0
    for n in clone_stream(m21_stream).flatten().notesAndRests:
        dur_16ths = int(n.quarterLength / 0.25)
        if copied >= prefix_16ths or dur_16ths <= 0:
            break
        prefix.append(n)
        copied += dur_16ths
    return prefix

# Measure the shared section span of a rendered three-voice bundle.
def get_voice_streams_length_16ths(voice_streams):
    return max((get_stream_length_16ths(m21_stream) for m21_stream in voice_streams.values()), default=0)

# Slice a monophonic stream by 16th-note offsets, splitting notes at boundaries.
def slice_stream_16ths(m21_stream, start_16ths, length_16ths):
    sliced = stream.Stream()
    if not m21_stream or length_16ths <= 0:
        return sliced

    end_16ths = start_16ths + length_16ths
    offset = 0
    for n in m21_stream.flatten().notesAndRests:
        dur_16ths = int(n.quarterLength / 0.25)
        event_start = offset
        event_end = offset + dur_16ths
        overlap_start = max(start_16ths, event_start)
        overlap_end = min(end_16ths, event_end)
        overlap_16ths = overlap_end - overlap_start
        if overlap_16ths > 0:
            if n.isRest:
                sliced.append(note.Rest(quarterLength=overlap_16ths * 0.25))
            else:
                sliced.append(note.Note(n.pitch.midi, quarterLength=overlap_16ths * 0.25))
        offset = event_end
        if offset >= end_16ths:
            break

    return sliced

# Slice all three voices over the same 16th-note span.
def slice_voice_streams(voice_streams, start_16ths, length_16ths):
    return {
        voice_id: slice_stream_16ths(voice_streams.get(voice_id, stream.Stream()), start_16ths, length_16ths)
        for voice_id in VOICE_IDS
    }

# Tie repeated weak-beat reattacks when they introduce a dissonant crunch.
def smooth_repeated_weak_dissonances(voice_streams, active_voice_id):
    active_stream = voice_streams.get(active_voice_id)
    if not active_stream:
        return stream.Stream()

    notes = list(active_stream.flatten().notesAndRests)
    if len(notes) < 2:
        return clone_stream(active_stream)

    meter_info = get_meter_info()
    beat_16ths = max(1, int(round(16.0 / meter_info["denominator"])))
    total_16ths = max((get_stream_length_16ths(m21_stream) for m21_stream in voice_streams.values()), default=0)
    other_grids = {
        voice_id: stream_to_pitch_grid(m21_stream, total_16ths)
        for voice_id, m21_stream in voice_streams.items()
        if voice_id != active_voice_id
    }

    flattened = []
    offset = 0
    for n in notes:
        dur_16ths = int(n.quarterLength / 0.25)
        flattened.append({
            "pitch": -1 if n.isRest else n.pitch.midi,
            "quarter_length": n.quarterLength,
            "start": offset,
            "duration_16ths": dur_16ths,
            "is_rest": n.isRest,
        })
        offset += dur_16ths

    smoothed = []
    for current in flattened:
        if smoothed:
            previous = smoothed[-1]
            is_repeated_pitch = (
                not previous["is_rest"]
                and not current["is_rest"]
                and previous["pitch"] == current["pitch"]
            )
            starts_on_weak_beat = (current["start"] % beat_16ths) != 0
            if is_repeated_pitch and starts_on_weak_beat:
                for voice_id, grid in other_grids.items():
                    if current["start"] >= len(grid):
                        continue
                    other_pitch = grid[current["start"]]
                    if other_pitch == -1:
                        continue
                    interval = abs(current["pitch"] - other_pitch) % 12
                    if not is_consonant_for_pair(active_voice_id, voice_id, interval):
                        previous["quarter_length"] += current["quarter_length"]
                        previous["duration_16ths"] += current["duration_16ths"]
                        break
                else:
                    smoothed.append(current.copy())
                continue
        smoothed.append(current.copy())

    new_stream = stream.Stream()
    for event in smoothed:
        if event["is_rest"]:
            new_stream.append(note.Rest(quarterLength=event["quarter_length"]))
        else:
            new_stream.append(note.Note(event["pitch"], quarterLength=event["quarter_length"]))
    return new_stream

# Recover the stored generated line from the rendered voice bundle.
def rendered_generated_stream(generated_stream, voice_streams, active_voice_id, state_name):
    active_stream = voice_streams.get(active_voice_id)
    if not active_stream:
        return clone_stream(generated_stream)
    if state_name == 'EPISODE':
        return take_stream_prefix(active_stream, get_stream_length_16ths(generated_stream))
    return clone_stream(active_stream)

# Score a generated line with melodic, registral, and issue-based penalties.
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

    if state_name == 'EPISODE':
        support_voice_id = next(
            (voice_id for voice_id in VOICE_IDS if voice_id != active_voice_id and get_pitch_list(voice_streams.get(voice_id))),
            None,
        )
        if support_voice_id is not None:
            support_prefix = take_stream_prefix(
                voice_streams[support_voice_id],
                get_stream_length_16ths(generated_stream),
            )
            support_pitches = get_pitch_list(support_prefix)
            generated_durations = [
                int(n.quarterLength / 0.25)
                for n in generated_stream.flatten().notesAndRests
                if n.quarterLength > 0
            ]
            support_durations = [
                int(n.quarterLength / 0.25)
                for n in support_prefix.flatten().notesAndRests
                if n.quarterLength > 0
            ]

            if active_voice_id == 2:
                if blueprint.episode_count <= 1:
                    if generated_durations != support_durations:
                        score += 8
                else:
                    if generated_durations == support_durations:
                        score += 9
                    else:
                        score -= 4
                    if len(set(generated_durations)) > 1:
                        score -= 3

                if support_pitches and len(pitches) > 1:
                    bass_motion = pitches[-1] - pitches[0]
                    support_motion = support_pitches[-1] - support_pitches[0]
                    if bass_motion != 0 and support_motion != 0:
                        if blueprint.episode_count <= 1 and ((bass_motion > 0) != (support_motion > 0)):
                            score += 5
                        elif blueprint.episode_count > 1 and ((bass_motion > 0) != (support_motion > 0)):
                            score -= 5

    voice_issues = issue_map if issue_map is not None else analyze_voice_streams(
        voice_streams,
        meter_info=get_meter_info(),
    )
    score += sum(len(issues) for issues in voice_issues.values()) * 40

    return score

# Convert a note-event payload into a music21 stream.
def convert_note_events_to_stream(note_events, use_offsets=False):
    result = stream.Stream()
    current_offset_ticks = 0
    
    i = 0
    while i < len(note_events):
        n = note_events[i]
        pitch = n.get('pitch')
        q_len = n.get('ticks') / 480.0 
        offset_ticks = n.get('offset_ticks', current_offset_ticks) if use_offsets else current_offset_ticks
        offset_ql = offset_ticks / 480.0
        
        is_tied = n.get('tie', False)
        
        while is_tied and i + 1 < len(note_events) and note_events[i+1].get('pitch') == pitch:
            i += 1
            next_n = note_events[i]
            q_len += (next_n.get('ticks') / 480.0)
            is_tied = next_n.get('tie', False)
            
        if pitch == -1:
            new_note = note.Rest(quarterLength=q_len)
        else:
            new_note = note.Note(pitch, quarterLength=q_len)

        result.insert(offset_ql, new_note) 
        current_offset_ticks = offset_ticks + int(q_len * 480)
        i += 1

    return result

# Convert the plugin's JSON note payload into a music21 stream.
def convert_json_to_stream(json_payload):
    return convert_note_events_to_stream(json_payload.get('subject', []), use_offsets=True)

# Build a tonal answer by transposing the subject with standard adjustment.
def create_tonal_answer(m21_stream):
    new_stream = stream.Stream()
    adjustment_active = True
    dominant_pc = (get_tonic_pc() + 7) % 12
    for n in m21_stream.flatten().notesAndRests:
        if n.isRest:
            new_stream.append(note.Rest(quarterLength=n.quarterLength))
        else:
            p = n.pitch.midi
            new_p = p - 5 
            if adjustment_active and p % 12 == dominant_pc:
                new_p = p - 7 
                adjustment_active = False 
            new_stream.append(note.Note(new_p, quarterLength=n.quarterLength))
    return new_stream

# Move a stream through a diatonic sequence by a given number of steps.
def diatonic_sequence(m21_stream, steps_down):
    if not m21_stream: return stream.Stream()
    scale_ladder = get_scale_pitch_ladder()
    new_stream = stream.Stream()
    for n in m21_stream.flatten().notesAndRests:
        if n.isRest:
            new_stream.append(note.Rest(quarterLength=n.quarterLength))
        else:
            p = n.pitch.midi
            if p in scale_ladder:
                idx = scale_ladder.index(p)
            else:
                idx = min(range(len(scale_ladder)), key=lambda i: abs(scale_ladder[i] - p))
            new_p = scale_ladder[max(0, min(len(scale_ladder) - 1, idx - steps_down))]
            new_stream.append(note.Note(new_p, quarterLength=n.quarterLength))
    return new_stream

# Return fresh copies of the supported episode-plan templates.
def get_episode_plan_templates():
    return [copy.deepcopy(plan) for plan in EPISODE_PLAN_TEMPLATES]

# Convert an episode plan and step index into a diatonic shift amount.
def get_episode_step_shift(episode_plan, step_index, part_kind='generated'):
    if not episode_plan or step_index <= 0:
        return 0

    direction_key = 'generated_direction' if part_kind == 'generated' else 'support_direction'
    direction = episode_plan.get(direction_key, episode_plan.get("direction", "down"))
    step_size = max(1, int(episode_plan.get("step_size", 1)))
    shift = step_index * step_size
    if direction == "up":
        shift *= -1
    return shift

# Apply one sequential episode transformation step to a generated stream.
def apply_episode_step(m21_stream, episode_plan, voice_id, step_index, part_kind='generated'):
    transformed = clone_stream(m21_stream)
    shift = get_episode_step_shift(episode_plan, step_index, part_kind=part_kind)
    if shift:
        transformed = diatonic_sequence(transformed, shift)
        transformed = fit_stream_to_voice_range(transformed, voice_id)
    return transformed

# Extract a short subject-head prefix for false-entry episode plans.
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

# Build the locked prefix used when solving a false-entry episode.
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

# Gather recent voice pitches used to rank episode-plan direction and range.
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

# Score one episode plan against the current registral context.
def score_episode_plan_context(episode_plan, instructions, target_voice_id):
    context_pitches, target_pitch = get_episode_context_pitches(instructions, target_voice_id)
    average_pitch = sum(context_pitches) / float(len(context_pitches))
    low, high = VOICE_RANGES[target_voice_id]
    room_below = max(0, target_pitch - low)
    room_above = max(0, high - target_pitch)

    direction = episode_plan.get("generated_direction", episode_plan.get("direction", "down"))
    support_direction = episode_plan.get("support_direction", direction)
    generated_direction = episode_plan.get("generated_direction", direction)
    step_size = max(1, int(episode_plan.get("step_size", 1)))
    kind = episode_plan.get("kind", "sequence")
    contrary_motion = support_direction != generated_direction

    score = 0.0
    if kind == "false_entry":
        score += 5 if blueprint.episode_count <= 1 else 1
    else:
        score -= 1

    if contrary_motion:
        if target_voice_id == 2 and blueprint.episode_count > 1:
            score -= 6
        else:
            score += 5

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

# Rank supported episode plans from most to least suitable right now.
def rank_episode_plans(instructions, target_voice_id):
    ranked = []
    for plan in get_episode_plan_templates():
        ranked.append((score_episode_plan_context(plan, instructions, target_voice_id), plan))
    ranked.sort(key=lambda item: item[0])
    return [copy.deepcopy(plan) for _, plan in ranked]

# Find which voice is assigned the currently generated role.
def get_target_voice_id(instructions, target_gen_role):
    return next(
        (voice_id for voice_id, role in instructions.items() if role == target_gen_role),
        None,
    )

# Score a fallback seed after rendering it into full voice streams.
def score_seed_candidate(seed_stream, instructions, target_gen_role, state_name, target_voice_id, prev_pitch, episode_plan=None):
    voice_streams = assemble_voice_streams(
        instructions,
        target_gen_role,
        seed_stream,
        state_name,
        episode_plan=episode_plan,
    )
    seed_stream = rendered_generated_stream(
        seed_stream,
        voice_streams,
        target_voice_id,
        state_name,
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

    return score, voice_streams, issues

# Build a non-solver episode candidate when direct solving runs out of options.
def build_episode_fallback(instructions, target_gen_role, state_name, episode_plan=None):
    target_voice_id = get_target_voice_id(instructions, target_gen_role)
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

        plan_direction = episode_plan.get("generated_direction", episode_plan.get("direction")) if episode_plan else None
        if plan_direction == "up":
            shift_options = [2, 1, 3, 4, -1]
        elif plan_direction == "down":
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
            score, voice_streams, issues = score_seed_candidate(
                seed_stream,
                instructions,
                target_gen_role,
                state_name,
                target_voice_id,
                prev_pitch,
                episode_plan=episode_plan,
            )
            candidate = (score, seed_stream, voice_streams, issues)
            if best_candidate is None or candidate[0] < best_candidate[0]:
                best_candidate = candidate

    if best_candidate is None:
        return None, None, ["Episode fallback could not build any candidate."]

    _, seed_stream, voice_streams, issues = best_candidate
    if any(issues.values()):
        return None, None, summarize_issues(issues)

    return seed_stream, voice_streams, []

# Build a non-solver middle-entry candidate when direct solving fails.
def build_middle_entry_fallback(instructions, target_gen_role, state_name):
    target_voice_id = get_target_voice_id(instructions, target_gen_role)
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
            score, voice_streams, issues = score_seed_candidate(
                seed_stream,
                instructions,
                target_gen_role,
                state_name,
                target_voice_id,
                prev_pitch,
            )
            candidate = (score, seed_stream, voice_streams, issues)
            if best_candidate is None or candidate[0] < best_candidate[0]:
                best_candidate = candidate

    if best_candidate is None:
        return None, None, ["Middle-entry fallback could not build any candidate."]

    _, seed_stream, voice_streams, issues = best_candidate
    if any(issues.values()):
        return None, None, summarize_issues(issues)

    return seed_stream, voice_streams, []

# Resolve a role name into a concrete stream for a specific voice and step.
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
        resolved_stream = apply_episode_step(
            resolved_stream,
            episode_plan,
            voice_id,
            sequence_step,
            part_kind='support',
        )

    return resolved_stream

# Render one generated line plus fixed roles into full voice streams.
def assemble_voice_streams(instructions, target_gen_role, generated_stream, state_name, episode_plan=None):
    step_count = 3 if state_name == 'EPISODE' else 1
    if state_name == 'EPISODE' and episode_plan:
        step_count = max(2, int(episode_plan.get("step_count", 3)))
    voice_parts = {voice_id: [] for voice_id in VOICE_IDS}
    target_voice_id = get_target_voice_id(instructions, target_gen_role) or 0

    generated_steps = [generated_stream]
    if state_name == 'EPISODE':
        generated_steps = [
            apply_episode_step(generated_stream, episode_plan, target_voice_id, step, part_kind='generated')
            for step in range(step_count)
        ]

    for step_index in range(step_count):
        step_stream = generated_steps[step_index]
        step_length = get_stream_length_16ths(step_stream)
        step_voice_streams = {}

        for voice_id, role in instructions.items():
            if role == target_gen_role:
                step_voice_streams[voice_id] = step_stream
            elif role == 'rest':
                step_voice_streams[voice_id] = make_rest_stream(step_length)
            else:
                step_voice_streams[voice_id] = resolve_role_stream(
                    role,
                    voice_id,
                    state_name,
                    step_index,
                    episode_plan=episode_plan,
                )

        step_voice_streams = stabilize_voice_stream_order(
            step_voice_streams,
            protected_voice_ids={target_voice_id},
        )
        step_voice_streams[target_voice_id] = smooth_repeated_weak_dissonances(
            step_voice_streams,
            target_voice_id,
        )
        for voice_id, current_stream in step_voice_streams.items():
            voice_parts[voice_id].append(current_stream)

    return {
        voice_id: concatenate_streams(parts)
        for voice_id, parts in voice_parts.items()
    }

# Convert a music21 stream into the JSON payload expected by the plugin bridge.
def stream_to_payload(m21_stream):
    notes = []
    for n in m21_stream.flatten().notesAndRests:
        pitch = -1 if n.isRest else n.pitch.midi
        ticks = int(n.quarterLength * 480)
        notes.append({"pitch": pitch, "ticks": ticks})
    return notes

# Prepare solver inputs and anchors for the next generation attempt.
def prepare_generation_context(instructions, target_gen_role, state_name):
    existing_voice_streams = {}
    existing_voice_ids = []
    prev_gen_pitch = -1
    active_voice_id = get_target_voice_id(instructions, target_gen_role) or 0

    for v_id in sorted(instructions.keys()):
        role = instructions[v_id]
        if role == target_gen_role:
            prev_gen_pitch = get_previous_voice_pitch(v_id)
        elif role != 'rest':
            existing_voice_streams[v_id] = resolve_role_stream(role, v_id, state_name)

    existing_voice_streams = stabilize_voice_stream_order(existing_voice_streams)
    existing_streams = []
    prev_ext_pitches = []
    for v_id in sorted(instructions.keys()):
        role = instructions[v_id]
        if role == target_gen_role:
            continue
        if role == 'rest':
            prev_ext_pitches.append(-1)
            continue
        role_stream = existing_voice_streams[v_id]
        existing_streams.append(role_stream)
        existing_voice_ids.append(v_id)
        prev_ext_pitches.append(get_last_pitch(role_stream))

    return existing_streams, existing_voice_ids, prev_ext_pitches, active_voice_id, prev_gen_pitch

# Construct and configure a FugueSolver for one generation attempt.
def build_solver_for_attempt(existing_streams, existing_voice_ids, active_voice_id, prev_gen_pitch, prev_ext_pitches, state_name, episode_plan=None):
    locked_prefix = None
    note_budget = get_note_budget(state_name)
    if state_name == 'EPISODE':
        locked_prefix = build_episode_locked_prefix(episode_plan, active_voice_id)
        if active_voice_id == 2 and blueprint.episode_count > 1:
            subject_length = max(1, get_stream_length_16ths(blueprint.motives.get('subject')))
            note_budget = min(subject_length, note_budget + 2)
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
        tonic_pc=get_tonic_pc(),
        meter_info=get_meter_info(),
        fixed_voice_ids=existing_voice_ids,
    )
    solver.setup_rules()
    return solver

# Keep the best-scoring candidates while removing duplicate textures.
def ordered_unique_candidates(candidates, desired_count):
    ordered = []
    ordered_signatures = set()
    for candidate in sorted(candidates, key=lambda item: item["score"]):
        signature = candidate_signature(candidate)
        if signature in ordered_signatures:
            continue
        ordered.append(candidate)
        ordered_signatures.add(signature)
        if len(ordered) >= desired_count:
            break
    return ordered

# Wrap a fallback stream into the same candidate format as solver outputs.
def build_candidate_from_fallback(fallback_stream, fallback_voices, active_voice_id, prev_pitch, state_name, episode_plan=None, plan_penalty=0.0):
    fallback_stream = rendered_generated_stream(
        fallback_stream,
        fallback_voices,
        active_voice_id,
        state_name,
    )
    fallback_score = score_generated_solution(
        fallback_stream,
        fallback_voices,
        active_voice_id,
        prev_pitch,
        state_name,
    )
    return build_candidate_entry(
        fallback_stream,
        fallback_voices,
        fallback_score + plan_penalty,
        episode_plan=episode_plan,
    )

# Collect valid solver outputs for one state, including fallback recovery.
def collect_valid_solutions(fs, instructions, target_gen_role, state_name, active_voice_id, prev_pitch, used_signatures=None, max_attempts=None, desired_count=1, episode_plan=None, plan_penalty=0.0, check_weak_dissonances=True, score_strict_issues=False):
    last_issues = {}
    if max_attempts is not None:
        attempt_limit = max_attempts
    else:
        attempt_limit = ATTEMPT_LIMITS.get(state_name, ATTEMPT_LIMITS['DEFAULT'])
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
        generated_stream = rendered_generated_stream(
            generated_stream,
            voice_streams,
            active_voice_id,
            state_name,
        )
        signature = voice_streams_signature(voice_streams)
        if signature in used_signatures:
            continue

        issues, _ = evaluate_candidate_voice_streams(
            voice_streams,
            check_weak_dissonances=check_weak_dissonances,
        )
        if not any(issues.values()):
            score_issue_map = issues
            if score_strict_issues and not check_weak_dissonances:
                score_issue_map, _ = evaluate_candidate_voice_streams(
                    voice_streams,
                    check_weak_dissonances=True,
                )
            score = score_generated_solution(
                generated_stream,
                voice_streams,
                active_voice_id,
                prev_pitch,
                state_name,
                issue_map=score_issue_map,
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
        return ordered_unique_candidates(valid_candidates, desired_count), []

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
            return [
                build_candidate_from_fallback(
                    fallback_stream,
                    fallback_voices,
                    active_voice_id,
                    prev_pitch,
                    state_name,
                    episode_plan=episode_plan,
                    plan_penalty=plan_penalty,
                )
            ], []
        if fallback_issues:
            return [], fallback_issues

    if state_name == 'MIDDLE_ENTRY':
        fallback_stream, fallback_voices, fallback_issues = build_middle_entry_fallback(instructions, target_gen_role, state_name)
        fallback_signature = voice_streams_signature(fallback_voices) if fallback_voices else None
        if fallback_stream and fallback_voices and fallback_signature not in used_signatures:
            return [build_candidate_from_fallback(fallback_stream, fallback_voices, active_voice_id, prev_pitch, state_name)], []
        if fallback_issues:
            return [], fallback_issues

    return [], summarize_issues(last_issues)

# Collect and rank candidates for the current state across its plan options.
def collect_state_candidates(instructions, target_gen_role, state_name, active_voice_id, prev_pitch, existing_streams, existing_voice_ids, prev_ext_pitches, used_signatures=None, desired_count=1):
    used_signatures = used_signatures or set()
    allow_relaxed_weak_checks = subject_has_mixed_rhythm()

    if state_name != 'EPISODE':
        solver = build_solver_for_attempt(
            existing_streams,
            existing_voice_ids,
            active_voice_id,
            prev_pitch,
            prev_ext_pitches,
            state_name,
        )
        candidates, issues = collect_valid_solutions(
            solver,
            instructions,
            target_gen_role,
            state_name,
            active_voice_id,
            prev_pitch,
            used_signatures=used_signatures,
            desired_count=desired_count,
        )
        if candidates or not allow_relaxed_weak_checks:
            return candidates, issues

        relaxed_solver = build_solver_for_attempt(
            existing_streams,
            existing_voice_ids,
            active_voice_id,
            prev_pitch,
            prev_ext_pitches,
            state_name,
        )
        return collect_valid_solutions(
            relaxed_solver,
            instructions,
            target_gen_role,
            state_name,
            active_voice_id,
            prev_pitch,
            used_signatures=used_signatures,
            desired_count=desired_count,
            check_weak_dissonances=False,
            score_strict_issues=True,
        )
    all_candidates = []
    last_issues = []
    seen_signatures = set(used_signatures)
    ranked_plans = rank_episode_plans(instructions, active_voice_id)
    primary_plan_count = min(4, len(ranked_plans))

    # Extend the candidate pool with solver results from one episode plan.
    def extend_with_plan(plan, plan_index, per_plan_goal, allow_relaxed_pass=False):
        nonlocal all_candidates, last_issues, seen_signatures
        solver = build_solver_for_attempt(
            existing_streams,
            existing_voice_ids,
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
        if not candidates and allow_relaxed_pass:
            relaxed_solver = build_solver_for_attempt(
                existing_streams,
                existing_voice_ids,
                active_voice_id,
                prev_pitch,
                prev_ext_pitches,
                state_name,
                episode_plan=plan,
            )
            candidates, issues = collect_valid_solutions(
                relaxed_solver,
                instructions,
                target_gen_role,
                state_name,
                active_voice_id,
                prev_pitch,
                used_signatures=seen_signatures,
                desired_count=per_plan_goal,
                episode_plan=plan,
                plan_penalty=plan_index * 6.0,
                check_weak_dissonances=False,
                score_strict_issues=True,
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
        extend_with_plan(plan, plan_index, 2, allow_relaxed_pass=allow_relaxed_weak_checks)

    if len(all_candidates) < desired_count:
        for plan_index, plan in enumerate(ranked_plans[primary_plan_count:], start=primary_plan_count):
            extend_with_plan(plan, plan_index, 1, allow_relaxed_pass=allow_relaxed_weak_checks)
            if len(all_candidates) >= desired_count:
                break

    if all_candidates:
        return ordered_unique_candidates(all_candidates, desired_count), []

    return [], last_issues

# Build the JSON response returned to the MuseScore plugin after generation.
def build_solution_response(voice_streams):
    multiplier = 3 if blueprint.state == 'EPISODE' else 1
    measure_voices = {
        voice_id: stream_to_payload(voice_streams[voice_id])
        for voice_id in VOICE_IDS
    }
    solution = {
        f"voice_{voice_id}": measure_voices[voice_id]
        for voice_id in VOICE_IDS
    }

    return jsonify({
        "status": "success",
        "next_state": blueprint.state,
        "duration_multiplier": multiplier,
        "solution": solution,
    })

# Store newly committed material back into the blueprint's motive cache.
def update_generated_motive(state_name, generated_stream, source_voice_id):
    if state_name == 'EXPO_2':
        blueprint.motives['cs1'] = generated_stream
        blueprint.motive_sources['cs1'] = source_voice_id
        return

    if state_name == 'EXPO_3':
        blueprint.motives['cs2'] = generated_stream
        blueprint.motive_sources['cs2'] = source_voice_id
        return

    if state_name in ['EPISODE', 'MIDDLE_ENTRY']:
        blueprint.motives['free_melody'] = generated_stream
        blueprint.motive_sources['free_melody'] = source_voice_id
        if state_name == 'EPISODE':
            blueprint.motives['episode_line'] = generated_stream
            blueprint.motive_sources['episode_line'] = source_voice_id

# Build one committed section entry for history syncing and restore.
def make_section_history_entry(state_name, generated_stream, voice_streams, source_voice_id, target_role):
    return {
        "state_name": state_name,
        "source_voice_id": source_voice_id,
        "target_role": target_role,
        "section_length_16ths": get_voice_streams_length_16ths(voice_streams),
        "generated_length_16ths": get_stream_length_16ths(generated_stream),
        "generated_stream": clone_stream(generated_stream),
        "voice_streams": copy_voice_streams(voice_streams),
    }

# Record one committed section so edited score history can be re-imported later.
def append_section_history_entry(state_name, generated_stream, voice_streams, source_voice_id, target_role):
    global_state.setdefault('section_history', []).append(
        make_section_history_entry(state_name, generated_stream, voice_streams, source_voice_id, target_role)
    )

# Rebuild the stored motive cache from the committed section history.
def rebuild_motives_from_section_history():
    section_history = global_state.get('section_history', [])
    blueprint.motives['cs1'] = None
    blueprint.motives['cs2'] = None
    blueprint.motives['free_melody'] = None
    blueprint.motives['episode_line'] = None

    for entry in section_history:
        if entry['state_name'] == 'SUBJECT':
            blueprint.motives['subject'] = clone_stream(entry['generated_stream'])
            blueprint.motive_sources['subject'] = ROLE_SOURCE_VOICE['subject']
            continue
        update_generated_motive(
            entry['state_name'],
            clone_stream(entry['generated_stream']),
            entry['source_voice_id'],
        )

# Render a committed section-history list back into continuous three-voice streams.
def render_section_history_streams(section_history):
    rendered = make_empty_voice_streams()
    for entry in section_history:
        rendered = extend_voice_streams(rendered, entry['voice_streams'])
    return rendered

# Build a stable comparison key for one structured evaluator issue.
def issue_event_key(issue_event):
    return (
        issue_event.get('id', ''),
        int(issue_event.get('offset', -1)),
        tuple(issue_event.get('voices', [])),
    )

# Import the current score history, validate edits, and refresh cached motives.
def sync_committed_history_from_payload(history_payload, pending_section_entry=None, skip_issue_validation=False):
    section_history = copy_section_history(global_state.get('section_history', []))
    if pending_section_entry is not None:
        section_history.append(copy_section_entry(pending_section_entry))
    if not history_payload or not section_history:
        return []

    history_streams = {
        voice_id: convert_note_events_to_stream(history_payload.get(f'voice_{voice_id}', []))
        for voice_id in VOICE_IDS
    }
    expected_length = sum(entry['section_length_16ths'] for entry in section_history)
    actual_length = get_voice_streams_length_16ths(history_streams)
    if actual_length != expected_length:
        return [
            "Edited score length no longer matches the generated layout. Keep the same total durations for now."
        ]

    window_start_16ths = 0
    if len(section_history) > 1:
        window_start_16ths = sum(
            entry['section_length_16ths']
            for entry in section_history[:-2]
        )

    def issue_in_sync_window(issue_event):
        return int(issue_event.get('offset', -1)) >= window_start_16ths

    if not skip_issue_validation:
        baseline_streams = render_section_history_streams(section_history)
        baseline_issue_events = evaluate_counterpoint_issues(
            baseline_streams,
            meter_info=get_meter_info(),
        )
        baseline_issue_keys = {
            issue_event_key(issue_event)
            for issue_event in baseline_issue_events
            if issue_in_sync_window(issue_event)
        }

        edited_issue_events = evaluate_counterpoint_issues(
            history_streams,
            meter_info=get_meter_info(),
        )
        introduced_issues = [
            issue_event
            for issue_event in edited_issue_events
            if issue_in_sync_window(issue_event) and issue_event_key(issue_event) not in baseline_issue_keys
        ]
        if introduced_issues:
            return [
                f"{issue.get('location', 'Unknown location')}: {issue.get('summary', 'Counterpoint issue.')}"
                for issue in introduced_issues[:3]
            ]

    synced_sections = []
    start_16ths = 0
    for entry in section_history:
        synced_entry = copy_section_entry(entry)
        synced_entry['voice_streams'] = slice_voice_streams(
            history_streams,
            start_16ths,
            entry['section_length_16ths'],
        )
        active_stream = synced_entry['voice_streams'].get(entry['source_voice_id'], stream.Stream())
        synced_entry['generated_stream'] = take_stream_prefix(
            active_stream,
            entry['generated_length_16ths'],
        )
        synced_sections.append(synced_entry)
        start_16ths += entry['section_length_16ths']

    global_state['section_history'] = synced_sections
    global_state['committed_history_streams'] = copy_voice_streams(history_streams)
    global_state['last_voice_streams'] = (
        copy_voice_streams(synced_sections[-1]['voice_streams'])
        if synced_sections else {}
    )
    rebuild_motives_from_section_history()
    return []

# Commit one cached candidate into the fugue history and motive store.
def commit_candidate_at_index(selected_idx):
    if len(global_state.get('generated_streams', [])) <= selected_idx:
        return

    chosen_entry = global_state['generated_streams'][selected_idx]
    chosen_stream = chosen_entry['generated_stream']
    committed_voice_streams = chosen_entry['voice_streams']
    source_voice_id = global_state.get('active_voice_id', 0)

    global_state['last_voice_streams'] = copy_voice_streams(committed_voice_streams)
    global_state['committed_history_streams'] = extend_voice_streams(
        global_state.get('committed_history_streams', make_empty_voice_streams()),
        committed_voice_streams,
    )
    update_generated_motive(blueprint.state, chosen_stream, source_voice_id)
    append_section_history_entry(
        blueprint.state,
        chosen_stream,
        committed_voice_streams,
        source_voice_id,
        global_state.get('target_gen_role', GEN_ROLE_BY_STATE.get(blueprint.state, 'free_melody')),
    )

# Build the section-history metadata for the currently displayed candidate.
def build_pending_section_entry(selected_idx):
    if len(global_state.get('generated_streams', [])) <= selected_idx:
        return None

    chosen_entry = global_state['generated_streams'][selected_idx]
    return make_section_history_entry(
        blueprint.state,
        chosen_entry['generated_stream'],
        chosen_entry['voice_streams'],
        global_state.get('active_voice_id', 0),
        global_state.get('target_gen_role', GEN_ROLE_BY_STATE.get(blueprint.state, 'free_melody')),
    )

# Initialize subject, answer, and committed history from the first user input.
def initialize_subject(decision, data, meter_payload, key_payload):
    blueprint.motives['subject'] = convert_json_to_stream(data)
    blueprint.motive_sources['subject'] = 1
    normalized_key = normalize_key_info(key_payload)
    global_state['key_info'] = normalized_key
    global_state['tonic_pc'] = KEYSIG_TO_TONIC_PC.get(
        normalized_key['accidentals'],
        infer_subject_tonic_pc(blueprint.motives['subject']),
    )
    blueprint.motives['answer'] = (
        create_tonal_answer(blueprint.motives['subject'])
        if decision == 'tonal_answer'
        else transpose_stream(blueprint.motives['subject'], -5)
    )
    blueprint.motive_sources['answer'] = 2
    global_state['meter_info'] = normalize_meter_info(meter_payload)
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
    global_state['section_history'] = []
    append_section_history_entry(
        'SUBJECT',
        blueprint.motives['subject'],
        global_state['committed_history_streams'],
        0,
        'subject',
    )

# Cache the current generation instructions and pitch anchor for reuse.
def store_generation_context(instructions, target_gen_role, active_voice_id, prev_gen_pitch):
    global_state['instructions'] = instructions
    global_state['target_gen_role'] = target_gen_role
    global_state['active_voice_id'] = active_voice_id
    global_state['anchor_pitch'] = prev_gen_pitch

# Replace or extend the cached candidate pool for Next/Prev navigation.
def set_candidate_pool(candidates, action):
    if action == 'new':
        global_state['generated_streams'] = copy_candidate_entries(candidates)
    else:
        global_state['generated_streams'].extend(copy_candidate_entries(candidates))

# Remove candidates whose voice layouts already exist in the cache.
def unique_new_candidates(candidate_solutions):
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
    return new_candidates

# --- ENDPOINTS ---
# Generate the next fugue section or an alternative for the current section.
@app.route('/generate', methods=['POST'])
def generate_measure():
    global blueprint

    data = request.json
    action = data.get('action', 'new')
    decision = data.get('decision', 'auto')
    selected_idx = int(data.get('selected_index', 0) or 0)
    meter_payload = data.get('meter_info') or {}
    key_payload = data.get('key_info') or {}
    history_payload = data.get('history_excerpt') or None
    history_validated = bool(data.get('history_validated'))
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
            if blueprint.state != 'INITIAL' and history_payload:
                sync_issues = sync_committed_history_from_payload(
                    history_payload,
                    pending_section_entry=build_pending_section_entry(selected_idx),
                    skip_issue_validation=history_validated,
                )
                if sync_issues:
                    return jsonify({
                        "status": "error",
                        "message": format_generation_message(
                            "Edited fugue introduces counterpoint issues. Use Evaluate or Restore Last Section before continuing:",
                            sync_issues,
                        ),
                    })
            else:
                commit_candidate_at_index(selected_idx)

        if blueprint.state == 'INITIAL':
            initialize_subject(decision, data, meter_payload, key_payload)

        blueprint_snapshot = copy.deepcopy(blueprint)
        state_snapshot = snapshot_runtime_state()

        instructions = blueprint.advance(decision)
        global_state['generated_streams'] = []

        target_gen_role = GEN_ROLE_BY_STATE.get(blueprint.state, 'free_melody')
        _, _, _, active_voice_id, prev_gen_pitch = prepare_generation_context(
            instructions,
            target_gen_role,
            blueprint.state,
        )

        store_generation_context(
            instructions,
            target_gen_role,
            active_voice_id,
            prev_gen_pitch,
        )

    instructions = global_state['instructions']
    target_gen_role = global_state['target_gen_role']
    active_voice_id = global_state.get('active_voice_id', active_voice_id)
    prev_gen_pitch = global_state.get('anchor_pitch', prev_gen_pitch)
    existing_streams, existing_voice_ids, prev_ext_pitches, _, _ = prepare_generation_context(
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
        existing_voice_ids,
        prev_ext_pitches,
        used_signatures=used_signatures,
        desired_count=desired_count,
    )
    if not candidate_solutions:
        if action == 'new' and blueprint_snapshot is not None and state_snapshot is not None:
            blueprint = blueprint_snapshot
            restore_runtime_state(state_snapshot)

        message = "Z3 exhausted solutions."
        if issue_summary:
            message = format_generation_message(
                "No valid solution survived the post-checks. Last issues:",
                issue_summary,
            )
        return jsonify({"status": "error", "message": message})

    new_candidates = unique_new_candidates(candidate_solutions)
    set_candidate_pool(new_candidates, action)

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

# Evaluate a highlighted fugue excerpt and return structured issues.
@app.route('/evaluate', methods=['POST'])
def evaluate_fugue():
    data = request.json
    voice_streams = {
        voice_id: convert_json_to_stream({'subject': data.get(f'voice_{voice_id}', [])})
        for voice_id in VOICE_IDS
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
