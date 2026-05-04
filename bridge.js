var pluginXhr = null;
var evalXhr = null;

function getGCD(a, b) {
    return b === 0 ? a : getGCD(b, a % b);
}

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

function getSelectionMeterInfo(curScore) {
    var cursor = curScore.newCursor();
    cursor.track = 0;
    cursor.rewind(1);

    if (cursor.measure && cursor.measure.timesigActual) {
        return {
            "numerator": cursor.measure.timesigActual.numerator || 4,
            "denominator": cursor.measure.timesigActual.denominator || 4,
            "measure_ticks": cursor.measure.timesigActual.ticks || 1920
        };
    }

    return { "numerator": 4, "denominator": 4, "measure_ticks": 1920 };
}

function getVoiceTracks(curScore) {
    return {
        "top": 0,
        "middle": (curScore.nstaves >= 3) ? 4 : 1,
        "bass": (curScore.nstaves >= 3) ? 8 : ((curScore.nstaves === 2) ? 4 : 2)
    };
}

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

function extractSelectionTrack(curScore, trackNum, selectionRange) {
    var notes = [];
    if (!selectionRange) {
        return notes;
    }

    var cursor = curScore.newCursor();
    cursor.track = 0;
    cursor.rewindToTick(selectionRange.startTick);

    while (cursor.segment && cursor.tick < selectionRange.endTick) {
        var element = cursor.segment.elementAt(trackNum);
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

function nextMeasure(subjectData, decision, action, selectedIdx, meterInfo, callback) {
    pluginXhr = new XMLHttpRequest();
    pluginXhr.open("POST", "http://127.0.0.1:5000/generate", true);
    pluginXhr.setRequestHeader("Content-Type", "application/json");

    pluginXhr.onreadystatechange = function () {
        if (pluginXhr.readyState === XMLHttpRequest.DONE) {
            if (pluginXhr.status === 200) {
                try {
                    var response = JSON.parse(pluginXhr.responseText);
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
        "meter_info": meterInfo || null
    }));
}

function evaluateFugue(curScore, selectionRangeOverride, callback) {
    var selectionRange = selectionRangeOverride || getSelectionRange(curScore);
    if (!selectionRange) {
        callback([], ["Highlight a range before evaluating."], "Highlight a range before evaluating.");
        return;
    }

    var tracks = getVoiceTracks(curScore);

    var payload = {
        "voice_0": extractSelectionTrack(curScore, tracks.top, selectionRange),
        "voice_1": extractSelectionTrack(curScore, tracks.middle, selectionRange),
        "voice_2": extractSelectionTrack(curScore, tracks.bass, selectionRange)
    };
    var meterInfo = getSelectionMeterInfo(curScore);
    payload.timesig_numerator = meterInfo.numerator;
    payload.timesig_denominator = meterInfo.denominator;
    payload.measure_ticks = meterInfo.measure_ticks;

    evalXhr = new XMLHttpRequest();
    evalXhr.open("POST", "http://127.0.0.1:5000/evaluate", true);
    evalXhr.setRequestHeader("Content-Type", "application/json");

    evalXhr.onreadystatechange = function () {
        if (evalXhr.readyState === XMLHttpRequest.DONE) {
            if (evalXhr.status === 200) {
                var response = JSON.parse(evalXhr.responseText);
                var issues = response.issues || [];
                for (var i = 0; i < issues.length; i++) {
                    issues[i].selection_start_tick = selectionRange.startTick;
                    issues[i].measure_ticks = meterInfo.measure_ticks;
                }
                callback(issues, response.mistakes || [], null, selectionRange);
            } else {
                callback([], ["Error: Could not connect to Python server."], "Error: Could not connect to Python server.", selectionRange);
            }
        }
    };
    evalXhr.send(JSON.stringify(payload));
}

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

function clearEvaluationSelection(curScore) {
    if (curScore && curScore.selection && curScore.selection.clear) {
        curScore.selection.clear();
    }
}

function focusEvaluationIssue(curScore, issue) {
    if (!curScore || !issue) {
        return false;
    }

    clearEvaluationSelection(curScore);

    var tracks = getVoiceTracks(curScore);
    var voiceTrackMap = {
        0: tracks.top,
        1: tracks.middle,
        2: tracks.bass
    };
    
    var selectedAny = false;
    var refs = issue.note_refs || [];
    var baseTick = issue.selection_start_tick || 0;

    for (var i = 0; i < refs.length; i++) {
        var ref = refs[i];
        var trackNum = voiceTrackMap[ref.voice_id];
        var absoluteTick = baseTick + (ref.start_offset * 120);
        var element = findTrackElementAtTick(curScore, trackNum, absoluteTick);
        
        if (!element) {
            continue;
        }

        var selectable = element;
        if (element.notes && element.notes.length > 0) {
            selectable = element.notes[0];
        }

        if (curScore.selection && curScore.selection.select && selectable) {
            curScore.selection.select(selectable, selectedAny);
            selectedAny = true;
        }
    }

    if (!selectedAny && curScore.selection && curScore.selection.selectRange) {
        var issueTick = baseTick + (issue.offset * 120);
        var rangeEnd = issueTick + 120;

        curScore.selection.selectRange(issueTick, rangeEnd, 0, curScore.nstaves);
        selectedAny = true;
    }

    return selectedAny;
}

function writeNotesToScore(voice0, voice1, voice2, targetScore, pasteTick) {
    targetScore.startCmd();

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
    
    var middleTrack = (targetScore.nstaves >= 3) ? 4 : 1;
    var bassTrack = (targetScore.nstaves >= 3) ? 8 : ((targetScore.nstaves === 2) ? 4 : 2);

    writeVoiceToTrack(voice0, 0);
    writeVoiceToTrack(voice1, middleTrack);
    writeVoiceToTrack(voice2, bassTrack);
    
    targetScore.endCmd();
}
