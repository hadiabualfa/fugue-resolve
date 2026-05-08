import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import MuseScore 3.0 
import "bridge.js" as Bridge

MuseScore {
    menuPath: "Plugins.FugueResolve"
    description: "A tool to assist composers in writing fugues."
    version: "1.0"
    
    pluginType: "dialog"
    width: 320
    height: 400

    property var generatedSolutions: []
    property int targetPasteTick: 0
    property int currentSolutionIndex: 0
    property int subjectDurationTicks: 0
    property string btnState: "ready"
    property string evaluationFeedback: "Highlight a measure and click Evaluate"
    property string currentSection: "INITIAL"
    property int currentDurationMultiplier: 1
    property string interactionMode: "generate"
    property var evaluationIssues: []
    property int currentIssueIndex: 0
    property var evaluationRange: null
    property int compositionStartTick: -1
    property var lastCommittedSolution: null
    property int lastCommittedTick: -1
    property bool currentSectionEdited: false

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10

        Label {
            text: interactionMode === "evaluate"
                ? "Evaluation Mode: Review one issue at a time."
                : (currentSection === "INITIAL" ? "Highlight your subject." : "Current Structure: " + currentSection)
            wrapMode: Text.WordWrap
            font.bold: true
        }

        RowLayout {
            visible: interactionMode === "generate" && currentSection === "INITIAL"
            Button {
                text: btnState === "solving" ? "..." : "Generate Real Answer"
                Layout.fillWidth: true
                onClicked: triggerGeneration("real_answer") 
            }
            Button {
                text: btnState === "solving" ? "..." : "Generate Tonal Answer"
                Layout.fillWidth: true
                onClicked: triggerGeneration("tonal_answer") 
            }
        }

        RowLayout {
            visible: interactionMode === "generate" && currentSection === "EXPO_2"
            Button {
                text: btnState === "solving" ? "Solving..." : "Generate Subject (Entry 3)"
                Layout.fillWidth: true
                onClicked: triggerGeneration("auto")
            }
        }

        RowLayout {
            visible: interactionMode === "generate" && (currentSection === "EXPO_3" || currentSection === "EPISODE" || currentSection === "MIDDLE_ENTRY")
            Button {
                text: btnState === "solving" ? "..." : "Generate Full Episode"
                Layout.fillWidth: true
                onClicked: triggerGeneration("episode")
            }
            Button {
                text: btnState === "solving" ? "..." : "Generate Middle Entry"
                Layout.fillWidth: true
                onClicked: triggerGeneration("middle_entry")
            }
        }

        RowLayout {
            Button {
                text: "◄ Prev"
                Layout.fillWidth: true
                onClicked: {
                    if (interactionMode === "evaluate") {
                        if (evaluationIssues.length === 0) return;
                        currentIssueIndex = (currentIssueIndex <= 0) ? evaluationIssues.length - 1 : currentIssueIndex - 1;
                        showCurrentIssue();
                        return;
                    }
                    if (generatedSolutions.length === 0) return;
                    currentSolutionIndex = (currentSolutionIndex <= 0) ? generatedSolutions.length - 1 : currentSolutionIndex - 1;
                    pasteCurrentSolution();
                }
            }
            Label {
                text: interactionMode === "evaluate"
                    ? (evaluationIssues.length > 0 ? (currentIssueIndex + 1) + " of " + evaluationIssues.length : "0 of 0")
                    : (generatedSolutions.length > 0 ? (currentSolutionIndex + 1) + " of " + generatedSolutions.length : "0 of 0")
                horizontalAlignment: Text.AlignHCenter
                Layout.minimumWidth: 50
            }
            Button {
                id: nextSolBtn
                text: "Next ►"
                Layout.fillWidth: true
                onClicked: {
                    if (interactionMode === "evaluate") {
                        if (evaluationIssues.length === 0) return;
                        currentIssueIndex = (currentIssueIndex >= evaluationIssues.length - 1) ? 0 : currentIssueIndex + 1;
                        showCurrentIssue();
                        return;
                    }
                    if (generatedSolutions.length === 0) return;
                    if (currentSolutionIndex >= generatedSolutions.length - 1) {
                        nextSolBtn.text = "..."
                        Bridge.nextMeasure([], "auto", "next", currentSolutionIndex, null, null, null, false, function(solution, nextState, durationMultiplier, errorMsg) {
                            if (solution) {
                                var temp = generatedSolutions.slice();
                                temp.push(solution);
                                generatedSolutions = temp;
                                currentSolutionIndex++;
                                pasteCurrentSolution();
                            } else {
                                evaluationFeedback = errorMsg ? "Next Alternative Failed: " + errorMsg : "Failed to find alternative.";
                            }
                            nextSolBtn.text = "Next ►"
                        });
                    } else {
                        currentSolutionIndex++;
                        pasteCurrentSolution();
                    }
                }
            }
        }

        RowLayout {
            Button {
                text: interactionMode === "evaluate" ? "Re-run Evaluate" : "Evaluate"
                Layout.fillWidth: true
                onClicked: {
                    evaluationFeedback = "Evaluating...";
                    var selectionRange = interactionMode === "evaluate"
                        ? evaluationRange
                        : Bridge.getSelectionRange(curScore);
                    if (!selectionRange) {
                        evaluationFeedback = "Highlight a range before evaluating.";
                        return;
                    }

                    Bridge.evaluateFugue(curScore, selectionRange, function(issues, mistakes, errorMsg, returnedRange) {
                        if (errorMsg) {
                            resetEvaluationMode(errorMsg);
                            return;
                        }

                        if (issues && issues.length > 0) {
                            enterEvaluationMode(issues, returnedRange);
                        } else if (issues && issues.length === 0) {
                            resetEvaluationMode("Perfect! No counterpoint errors detected.", returnedRange);
                        } else if (mistakes && mistakes.length > 0) {
                            resetEvaluationMode(mistakes.join("\n"), returnedRange);
                        } else {
                            evaluationFeedback = "Error: Did not receive a response from the solver.";
                        }
                    });
                }
            }
            Button {
                text: "Reset"
                Layout.minimumWidth: 80
                onClicked: {
                    Bridge.nextMeasure([], "reset", "new", 0, null, null, null, false, function() {
                        currentSection = "INITIAL";
                        generatedSolutions = [];
                        currentSolutionIndex = 0;
                        targetPasteTick = 0;
                        currentDurationMultiplier = 1;
                        compositionStartTick = -1;
                        lastCommittedSolution = null;
                        lastCommittedTick = -1;
                        currentSectionEdited = false;
                        resetEvaluationMode("Generator reset successfully. Highlight your subject and start again.");
                    });
                }
            }
        }

        RowLayout {
            visible: interactionMode === "generate" && lastCommittedSolution !== null && lastCommittedTick >= 0
            Button {
                text: "Restore Last Section"
                Layout.fillWidth: true
                onClicked: restoreLastCommittedSection()
            }
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            TextArea {
                text: evaluationFeedback
                wrapMode: Text.WordWrap
                readOnly: true
                background: Rectangle {
                    color: "#f5f5f5"
                    border.color: "#dddddd"
                }
            }
        }
    }

    // Request the next generated section from the Python server.
    function requestGeneration(subjectData, decision, meterInfo, keyInfo, historyExcerpt, historyValidated, oldPasteTick) {
        Bridge.nextMeasure(subjectData, decision, "new", currentSolutionIndex, meterInfo, keyInfo, historyExcerpt, historyValidated, function(solution, nextState, durationMultiplier, errorMsg) {
            if (!solution) {
                targetPasteTick = oldPasteTick;
                evaluationFeedback = errorMsg ? "Error: " + errorMsg : "Error: Generation failed.";
                btnState = "ready";
                return; 
            }
            generatedSolutions = [solution]; 
            currentSolutionIndex = 0;
            currentSection = nextState; 
            currentDurationMultiplier = durationMultiplier !== undefined ? durationMultiplier : 1;
            pasteCurrentSolution();
            evaluationFeedback = "Generation complete.";
            btnState = "ready"; 
        });
    }

    // Format a short multi-line generation error block for the plugin textbox.
    function formatGenerationError(title, details) {
        if (!details || details.length === 0) {
            return title;
        }

        var lines = [title];
        for (var i = 0; i < details.length; i++) {
            lines.push("- " + details[i]);
        }
        return lines.join("\n");
    }

    // Validate the edited current section with the same evaluator flow before continuing generation.
    function validateEditedHistoryAndGenerate(decision, subjectData, meterInfo, keyInfo, historyExcerpt, oldPasteTick) {
        var sectionStartTick = lastCommittedTick >= 0 ? lastCommittedTick : compositionStartTick;
        var historyRange = {
            "startTick": sectionStartTick,
            "endTick": targetPasteTick,
            "startStaff": 0,
            "endStaff": curScore.nstaves - 1
        };

        Bridge.evaluateFugue(curScore, historyRange, function(issues, mistakes, errorMsg) {
            if (errorMsg) {
                targetPasteTick = oldPasteTick;
                evaluationFeedback = errorMsg;
                btnState = "ready";
                return;
            }

            if (issues && issues.length > 0) {
                targetPasteTick = oldPasteTick;
                var lines = [];
                for (var i = 0; i < issues.length && i < 3; i++) {
                    lines.push(issues[i].location + ": " + issues[i].summary);
                }
                evaluationFeedback = formatGenerationError(
                    "Edited fugue introduces counterpoint issues. Use Evaluate or Restore Last Section before continuing:",
                    lines
                );
                btnState = "ready";
                return;
            }

            requestGeneration(subjectData, decision, meterInfo, keyInfo, historyExcerpt, true, oldPasteTick);
        });
    }

    // Trigger generation for the current fugue section and paste the best result.
    function triggerGeneration(decision) {
        if (btnState === "solving") return; 
        resetEvaluationMode(undefined, undefined, currentSection !== "INITIAL");
        
        var subjectData = [];
        var meterInfo = null;
        var keyInfo = null;
        var historyExcerpt = null;
        var oldPasteTick = targetPasteTick;
        
        if (currentSection === "INITIAL") {
            var selectionRange = Bridge.getSelectionRange(curScore);
            if (!selectionRange) {
                evaluationFeedback = "Highlight your subject before generating.";
                return;
            }
            lastCommittedSolution = null;
            lastCommittedTick = -1;
            currentSectionEdited = false;
            var cursor = curScore.newCursor();
            targetPasteTick = selectionRange.endTick;
            subjectDurationTicks = selectionRange.endTick - selectionRange.startTick;
            compositionStartTick = selectionRange.startTick;
            cursor.rewind(1);
            subjectData = Bridge.extractSubject(cursor);
            meterInfo = Bridge.getSelectionMeterInfo(curScore);
            keyInfo = Bridge.getSelectionKeyInfo(curScore);
            currentDurationMultiplier = 1; 
        } else {
            if (generatedSolutions.length > 0) {
                lastCommittedSolution = generatedSolutions[currentSolutionIndex];
                lastCommittedTick = targetPasteTick;
                currentSectionEdited = !Bridge.scoreRangeMatchesSolution(
                    curScore,
                    targetPasteTick,
                    lastCommittedSolution
                );
            } else {
                currentSectionEdited = false;
            }
            if (currentSectionEdited) {
                historyExcerpt = Bridge.buildGenerationHistoryPayload(
                    curScore,
                    compositionStartTick,
                    targetPasteTick + (subjectDurationTicks * currentDurationMultiplier)
                );
            }
            targetPasteTick += (subjectDurationTicks * currentDurationMultiplier); 
        }
        
        btnState = "solving"; 
        evaluationFeedback = "Generating...";

        if (currentSectionEdited && historyExcerpt) {
            validateEditedHistoryAndGenerate(decision, subjectData, meterInfo, keyInfo, historyExcerpt, oldPasteTick);
            return;
        }

        requestGeneration(subjectData, decision, meterInfo, keyInfo, historyExcerpt, false, oldPasteTick);
    }

    // Paste the currently selected generated solution into the score.
    function pasteCurrentSolution() {
        if (generatedSolutions.length > 0) {
            var selectedSolution = generatedSolutions[currentSolutionIndex];
            Bridge.writeNotesToScore(
                selectedSolution.voice_0, 
                selectedSolution.voice_1, 
                selectedSolution.voice_2,
                curScore, 
                targetPasteTick
            );
            currentSectionEdited = false;
        }
    }

    // Restore the most recently committed generated section after a failed edit.
    function restoreLastCommittedSection() {
        if (!lastCommittedSolution || lastCommittedTick < 0) {
            return;
        }

        Bridge.writeNotesToScore(
            lastCommittedSolution.voice_0,
            lastCommittedSolution.voice_1,
            lastCommittedSolution.voice_2,
            curScore,
            lastCommittedTick
        );
        currentSectionEdited = false;
        evaluationFeedback = "Restored the last generated section.";
    }

    // Describe the currently selected evaluation issue.
    function showCurrentIssue() {
        if (evaluationIssues.length === 0) {
            resetEvaluationMode("Perfect! No counterpoint errors detected.", evaluationRange);
            return;
        }

        var issue = evaluationIssues[currentIssueIndex];
        evaluationFeedback =
            "Issue " + (currentIssueIndex + 1) + " of " + evaluationIssues.length + "\n" +
            issue.location + "\n" +
            issue.summary;
    }

    // Enter evaluation mode with a fresh issue list and selected range.
    function enterEvaluationMode(issues, range) {
        interactionMode = "evaluate";
        evaluationIssues = issues || [];
        currentIssueIndex = 0;
        evaluationRange = range || null;
        showCurrentIssue();
    }

    // Exit evaluation mode and optionally show a status message.
    function resetEvaluationMode(message, range, clearSelection) {
        interactionMode = "generate";
        evaluationIssues = [];
        currentIssueIndex = 0;
        evaluationRange = range === undefined ? null : range;
        if (message !== undefined) {
            evaluationFeedback = message;
        }
    }
}
