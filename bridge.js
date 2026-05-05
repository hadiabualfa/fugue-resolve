var pluginXhr = null;
var evalXhr = null;

// Compute the greatest common divisor used for MuseScore duration fractions.
function getGCD(a, b) {
    return b === 0 ? a : getGCD(b, a % b);
}

// Extract the selected subject as note/rest events with offsets and ties.
function extractSubject(cursor) {
    var notes = [];

    cursor.rewind(2);
    var endTick = cursor.tick;
    cursor.rewind(1);
    var startTick = cursor.tick; 

    while (cursor.segment && cursor.tick < endTick) {
        if (cursor.element && cursor.element.duration) {
            var relativeTick = Math.max(0, cursor.tick - startTick); 

            if (cursor.element.notes && cursor.element.notes.length > 0) {
                var noteObj = cursor.element.notes[0];
                var isTied = !!noteObj.tieForward; 
                notes.push({ 
                    "pitch": noteObj.pitch, 
                    "ticks": cursor.element.duration.ticks, 
                    "offset_ticks": relativeTick,
                    "tie": isTied 
                });
            } else {
                notes.push({ 
                    "pitch": -1, 
                    "ticks": cursor.element.duration.ticks, 
                    "offset_ticks": relativeTick,
                    "tie": false 
                });
            }
        }
        cursor.next();
    }

    return notes;
}

// Read the active time signature at the start of a selected range.
function getSelectionMeterInfo(curScore, selectionRange) {
    var cursor = curScore.newCursor();
    cursor.track = 0;
    if (selectionRange) {
        cursor.rewindToTick(selectionRange.startTick);
    } else {
        cursor.rewind(1);
    }

    if (cursor.measure && cursor.measure.timesigActual) {
        return {
            "numerator": cursor.measure.timesigActual.numerator || 4,
            "denominator": cursor.measure.timesigActual.denominator || 4,
            "measure_ticks": cursor.measure.timesigActual.ticks || 1920
        };
    }

    return { "numerator": 4, "denominator": 4, "measure_ticks": 1920 };
}

// Read the active key signature at the selection start in number of accidentals.
function getSelectionKeyInfo(curScore) {
    var cursor = curScore.newCursor();
    cursor.track = 0;

    var selectionRange = getSelectionRange(curScore);
    if (selectionRange) {
        cursor.rewindToTick(selectionRange.startTick);
    } else {
        cursor.rewind(1);
    }

    if (cursor && cursor.keySignature !== undefined) {
        return { "accidentals": cursor.keySignature };
    }

    if (curScore && curScore.keysig !== undefined) {
        return { "accidentals": curScore.keysig };
    }

    return { "accidentals": 0 };
}

// Map the fugue's top, middle, and bass voices to MuseScore tracks.
function getVoiceTracks(curScore) {
    return {
        "top": 0,
        "middle": (curScore.nstaves >= 3) ? 4 : 1,
        "bass": (curScore.nstaves >= 3) ? 8 : ((curScore.nstaves === 2) ? 4 : 2)
    };
}

// Build a voice-id-to-track map for score reading and writing.
function getVoiceTrackMap(curScore) {
    var tracks = getVoiceTracks(curScore);
    return {
        0: tracks.top,
        1: tracks.middle,
        2: tracks.bass
    };
}

// Return the selected score range that should be generated or evaluated.
function getSelectionRange(curScore) {
    if (!curScore || !curScore.selection || !curScore.selection.isRange || !curScore.selection.startSegment) {
        return null;
    }

    var cursor = curScore.newCursor();
    cursor.track = 0;
    cursor.rewind(2);
    var fallbackEndTick = cursor.tick;
    var endSegment = curScore.selection.endSegment;

    return {
        "startTick": curScore.selection.startSegment.tick,
        "endTick": endSegment ? endSegment.tick : fallbackEndTick,
        "startStaff": curScore.selection.startStaff || 0,
        "endStaff": (curScore.selection.endStaff !== undefined) ? curScore.selection.endStaff : (curScore.nstaves - 1)
    };
}

// Extract one track from the selected range as note/rest payload data.
function extractSelectionTrack(curScore, trackNum, selectionRange) {
    var notes = [];
    if (!selectionRange) {
        return notes;
    }

    var cursor = curScore.newCursor();
    cursor.track = trackNum;
    cursor.rewindToTick(selectionRange.startTick);

    while (cursor.segment && cursor.tick < selectionRange.startTick) {
        if (!cursor.next()) {
            return notes;
        }
    }

    while (cursor.segment && cursor.tick < selectionRange.endTick) {
        var element = cursor.element;
        if (element && element.duration) {
            if (element.notes && element.notes.length > 0) {
                var noteObj = element.notes[0];
                var isTied = !!noteObj.tieForward;
                notes.push({ "pitch": noteObj.pitch, "ticks": element.duration.ticks, "tie": isTied });
            } else {
                notes.push({ "pitch": -1, "ticks": element.duration.ticks, "tie": false });
            }
        }

        if (!cursor.next()) {
            break;
        }
    }

    return notes;
}

// Parse a JSON response from the local Python server.
function parseJsonResponse(xhr) {
    return JSON.parse(xhr.responseText);
}

// Ask the Python server for the next generated solution or alternative.
function nextMeasure(subjectData, decision, action, selectedIdx, meterInfo, keyInfo, callback) {
    pluginXhr = new XMLHttpRequest();
    pluginXhr.open("POST", "http://127.0.0.1:5000/generate", true);
    pluginXhr.setRequestHeader("Content-Type", "application/json");

    pluginXhr.onreadystatechange = function () {
        if (pluginXhr.readyState === XMLHttpRequest.DONE) {
            if (pluginXhr.status === 200) {
                try {
                    var response = parseJsonResponse(pluginXhr);
                    if (response.status === "error") {
                        callback(null, null, null, response.message);
                    } else {
                        callback(response.solution, response.next_state, response.duration_multiplier, null);
                    }
                } catch (e) {
                    callback(null, null, null, "Failed to parse Python response.");
                }
            } else {
                callback(null, null, null, "Failed to connect to Python server.");
            }
        }
    };
    
    pluginXhr.send(JSON.stringify({ 
        "subject": subjectData, 
        "decision": decision,
        "action": action,
        "selected_index": selectedIdx,
        "meter_info": meterInfo || null,
        "key_info": keyInfo || null
    }));
}

// Build the three-voice payload sent to the evaluator endpoint.
function buildEvaluationPayload(curScore, selectionRange) {
    var trackMap = getVoiceTrackMap(curScore);
    return {
        "voice_0": extractSelectionTrack(curScore, trackMap[0], selectionRange),
        "voice_1": extractSelectionTrack(curScore, trackMap[1], selectionRange),
        "voice_2": extractSelectionTrack(curScore, trackMap[2], selectionRange)
    };
}

// Attach score-relative offsets to issues returned by the evaluator.
function annotateIssues(issues, selectionRange, meterInfo) {
    for (var i = 0; i < issues.length; i++) {
        issues[i].selection_start_tick = selectionRange.startTick;
        issues[i].measure_ticks = meterInfo.measure_ticks;
    }
    return issues;
}

// Send the selected fugue range to Python for evaluation.
function evaluateFugue(curScore, selectionRange, callback) {
    selectionRange = selectionRange || getSelectionRange(curScore);
    if (!selectionRange) {
        callback([], ["Highlight a range before evaluating."], "Highlight a range before evaluating.");
        return;
    }

    var payload = buildEvaluationPayload(curScore, selectionRange);
    var meterInfo = getSelectionMeterInfo(curScore, selectionRange);
    payload.timesig_numerator = meterInfo.numerator;
    payload.timesig_denominator = meterInfo.denominator;
    payload.measure_ticks = meterInfo.measure_ticks;

    evalXhr = new XMLHttpRequest();
    evalXhr.open("POST", "http://127.0.0.1:5000/evaluate", true);
    evalXhr.setRequestHeader("Content-Type", "application/json");

    evalXhr.onreadystatechange = function () {
        if (evalXhr.readyState === XMLHttpRequest.DONE) {
            if (evalXhr.status === 200) {
                try {
                    var response = parseJsonResponse(evalXhr);
                    var issues = annotateIssues(response.issues || [], selectionRange, meterInfo);
                    var filteredIssues = filterIssuesAgainstScore(curScore, issues);
                    var filteredMistakes = filteredIssues.length > 0 ? [] : ["No mistakes found!"];
                    callback(filteredIssues, filteredMistakes, null, selectionRange);
                } catch (e) {
                    callback([], ["Failed to parse evaluator response."], "Failed to parse evaluator response.", selectionRange);
                }
            } else {
                callback([], ["Error: Could not connect to Python server."], "Error: Could not connect to Python server.", selectionRange);
            }
        }
    };
    evalXhr.send(JSON.stringify(payload));
}

// Find the score element that starts at a given tick on a given track.
function findTrackElementAtTick(curScore, trackNum, tick) {
    var cursor = curScore.newCursor();
    cursor.track = 0;
    cursor.rewindToTick(tick);

    while (cursor.segment && cursor.tick < tick) {
        if (!cursor.next()) {
            return null;
        }
    }

    if (!cursor.segment || cursor.tick !== tick) {
        return null;
    }

    return cursor.segment.elementAt(trackNum);
}

// Check whether a score element is a sounding note/chord rather than a rest.
function isSoundingElement(element) {
    return !!(element && element.notes && element.notes.length > 0);
}

// Drop issues whose named voices are not actually sounding in the score.
function issueMatchesScoreVoices(curScore, issue) {
    if (!issue || !issue.voices || issue.voices.length === 0) {
        return true;
    }

    var voiceTrackMap = getVoiceTrackMap(curScore);
    var baseTick = issue.selection_start_tick || 0;
    var absoluteTick = baseTick + (issue.offset * 120);

    for (var i = 0; i < issue.voices.length; i++) {
        var voiceId = issue.voices[i];
        var trackNum = voiceTrackMap[voiceId];
        if (trackNum === undefined) {
            return false;
        }

        var element = findTrackElementAtTick(curScore, trackNum, absoluteTick);
        if (!isSoundingElement(element)) {
            return false;
        }
    }

    return true;
}

// Filter evaluator issues against the live MuseScore score contents.
function filterIssuesAgainstScore(curScore, issues) {
    var filtered = [];
    for (var i = 0; i < issues.length; i++) {
        if (issueMatchesScoreVoices(curScore, issues[i])) {
            filtered.push(issues[i]);
        }
    }
    return filtered;
}

// Write a generated three-voice solution back into the MuseScore score.
function writeNotesToScore(voice0, voice1, voice2, targetScore, pasteTick) {
    targetScore.startCmd();

    // Write one logical voice into its assigned MuseScore track.
    function writeVoiceToTrack(notes, trackNum) {
        if (!notes || notes.length === 0) return;
        
        var cursor = targetScore.newCursor();
        if (trackNum >= targetScore.nstaves * 4) { trackNum = 2; }

        cursor.track = 0;
        cursor.rewind(0);
        while (cursor.segment && cursor.tick < pasteTick) {
            cursor.next();
        }
        cursor.track = trackNum;

        for (var i = 0; i < notes.length; i++) {
            var pitch = notes[i].pitch;
            var ticks = notes[i].ticks;
            if (ticks <= 0) continue;

            var gcd = getGCD(ticks, 1920);
            cursor.setDuration(Math.floor(ticks / gcd), Math.floor(1920 / gcd));
            
            if (pitch === -1) { cursor.addRest(); }
            else { cursor.addNote(pitch); }
        }
    }
    
    var voiceTrackMap = getVoiceTrackMap(targetScore);

    writeVoiceToTrack(voice0, 0);
    writeVoiceToTrack(voice1, voiceTrackMap[1]);
    writeVoiceToTrack(voice2, voiceTrackMap[2]);
    
    targetScore.endCmd();
}
