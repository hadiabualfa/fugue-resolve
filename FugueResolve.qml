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
    property int nextMeasureToGenerate: 2
    property int subjectDurationTicks: 0
    property string btnState: "ready"
    property string evaluationFeedback: "Highlight a measure and click Evaluate"
    property string currentSection: "INITIAL"
    property int currentDurationMultiplier: 1
    property string interactionMode: "generate"
    property var evaluationIssues: []
    property int currentIssueIndex: 0
    property var evaluationRange: null

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
                        Bridge.nextMeasure([], "auto", "next", currentSolutionIndex, null, function(solution, nextState, durationMultiplier, errorMsg) {
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
                    var selectionRange = interactionMode === "evaluate" ? evaluationRange : null;
                    Bridge.evaluateFugue(curScore, selectionRange, function(issues, mistakes, errorMsg, returnedRange) {
                        if (errorMsg) {
                            interactionMode = "generate";
                            evaluationIssues = [];
                            currentIssueIndex = 0;
                            evaluationRange = null;
                            Bridge.clearEvaluationSelection(curScore);
                            evaluationFeedback = errorMsg;
                            return;
                        }

                        if (issues && issues.length > 0) {
                            interactionMode = "evaluate";
                            evaluationIssues = issues;
                            currentIssueIndex = 0;
                            evaluationRange = returnedRange;
                            showCurrentIssue();
                        } else if (issues && issues.length === 0) {
                            interactionMode = "generate";
                            evaluationIssues = [];
                            currentIssueIndex = 0;
                            evaluationRange = returnedRange;
                            Bridge.clearEvaluationSelection(curScore);
                            evaluationFeedback = "Perfect! No counterpoint errors detected.";
                        } else if (mistakes && mistakes.length > 0) {
                            interactionMode = "generate";
                            evaluationIssues = [];
                            currentIssueIndex = 0;
                            evaluationRange = returnedRange;
                            evaluationFeedback = mistakes.join("\n");
                            Bridge.clearEvaluationSelection(curScore);
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
                    Bridge.clearEvaluationSelection(curScore);
                    Bridge.nextMeasure([], "reset", "new", 0, null, function() {
                        currentSection = "INITIAL";
                        interactionMode = "generate";
                        evaluationIssues = [];
                        currentIssueIndex = 0;
                        evaluationRange = null;
                        generatedSolutions = [];
                        currentSolutionIndex = 0;
                        targetPasteTick = 0;
                        currentDurationMultiplier = 1;
                        evaluationFeedback = "Generator reset successfully. Highlight your subject and start again.";
                    });
                }
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

    function triggerGeneration(decision) {
        if (btnState === "solving") return; 
        interactionMode = "generate";
        evaluationIssues = [];
        currentIssueIndex = 0;
        evaluationRange = null;
        
        var subjectData = [];
        var meterInfo = null;
        var oldPasteTick = targetPasteTick;
        
        if (currentSection === "INITIAL") {
            var cursor = curScore.newCursor();
            cursor.rewind(2);
            targetPasteTick = cursor.tick;
            cursor.rewind(1);
            subjectDurationTicks = targetPasteTick - cursor.tick;
            subjectData = Bridge.extractSubject(cursor);
            meterInfo = Bridge.getSelectionMeterInfo(curScore);
            currentDurationMultiplier = 1; 
        } else {
            targetPasteTick += (subjectDurationTicks * currentDurationMultiplier); 
            Bridge.clearEvaluationSelection(curScore);
        }
        
        btnState = "solving"; 
        evaluationFeedback = "Generating...";
        
        Bridge.nextMeasure(subjectData, decision, "new", currentSolutionIndex, meterInfo, function(solution, nextState, durationMultiplier, errorMsg) {
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
        }
    }

    function showCurrentIssue() {
        if (evaluationIssues.length === 0) {
            interactionMode = "generate";
            Bridge.clearEvaluationSelection(curScore);
            evaluationFeedback = "Perfect! No counterpoint errors detected.";
            return;
        }

        var issue = evaluationIssues[currentIssueIndex];
        Bridge.focusEvaluationIssue(curScore, issue);
        evaluationFeedback =
            "Issue " + (currentIssueIndex + 1) + " of " + evaluationIssues.length + "\n" +
            issue.location + "\n" +
            issue.summary;
    }
}
