# FugueResolve

FugueResolve is a MuseScore plugin for assisted fugue writing. It uses:

- `z3-solver` to generate contrapuntal continuations
- `music21` to represent and transform musical material
- `Flask` to run a local server that allows for data exchange between MuseScore and Python
- QML and JavaScript to provide the MuseScore plugin UI and communicate with the Python backend

The plugin supports two workflows:

- `Generation mode`: generate fugues measure-by-measure based on a given subject
- `Evaluation mode`: highlight issues in a selected passage and step through them one at a time

## Requirements

- MuseScore `3` or later
- Python `3.10` recommended
- A local Python environment with the packages in `requirements.txt`

## Repository Layout

- `FugueResolve.qml`: MuseScore plugin UI
- `bridge.js`: MuseScore-to-Python bridge
- `main.py`: local Flask server with generation/evaluation endpoints
- `fugue_solver.py`: Z3 solver and counterpoint evaluator
- `fugue_state.py`: state machine

## 1. Download the Project

Clone or download this repository to a local folder:

```bash
git clone https://github.com/hadiabualfa/fugue-resolve
```

Open the repository in a code editor.

## 2. Create a Python Environment

Create and activate a virtual environment from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Install Python Dependencies

Install the required packages:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Install the Plugin in MuseScore

Copy just the `FugueResolve.qml` and `bridge.js` files to a separate folder inside the MuseScore Plugins folder. Alternatively, place the whole repository inside the MuseScore Plugins folder and proceed from there.

Important:

- `FugueResolve.qml` and `bridge.js` must stay in the same folder
- `main.py`, `fugue_solver.py`, and `fugue_state.py` must stay together wherever you launch the Python server

Once the above is complete:

1. Restart MuseScore.
2. Open `Plugins > Plugin Manager`.
3. Enable `FugueResolve`.

After enabling it, the plugin appears in:

```text
Plugins > FugueResolve
```

## 5. Start the Python Server

From the project root, activate the environment if needed and run `main.py`:

```bash
source .venv/bin/activate
python3 main.py
```

This starts the local Flask server on:

```text
http://127.0.0.1:5000
```

Keep this terminal open while using the plugin. The MuseScore-to-Python bridge sends requests to:

- `POST /generate`
- `POST /evaluate`

If you change the server host or port, update the URLs in `bridge.js`.

## 6. Using the Plugin in MuseScore

### Generation Mode

1. Open a score in MuseScore.
2. Write a subject.
3. Highlight the subject range in the score.
4. Open `Plugins > FugueResolve`.
5. Choose:
   - `Generate Real Answer`, or
   - `Generate Tonal Answer`
6. Continue generation with the available buttons:
   - `Generate Subject (Entry 3)`
   - `Generate Full Episode`
   - `Generate Middle Entry`
7. Use `Next` and `Prev` to browse alternative generated solutions for the current section.

Notes:

- Writing a highly complicated subject or breaking voice-leading rules may cause the generation constraints to become unsatisfiable.
- The plugin writes generated material directly into the score.
- The server keeps track of an internal state, so generation is intended to proceed section by section. Thus, the subject does not need to remain highlighted so that edits can be made along the way.
- `Reset` clears the internal state and returns the UI to its initial state.

### Evaluation Mode

1. Highlight the entire fugue that you want to inspect.
2. Click `Evaluate`.
3. The plugin switches to evaluation mode:
   - It highlights issues one-by-one in the score
   - It describes each issue in the plugin window
4. Use `Next` and `Prev` to move through these issues and edit the score to address each of them.
6. Click `Re-run Evaluate` to refresh the issue list.
7. Repeat this process until no issues are reported.
8. Click `Reset` at any time to leave evaluation mode and return to generation mode.

## Troubleshooting

### The plugin appears in MuseScore, but generation fails immediately

Make sure the Python server is running:

```bash
python3 main.py
```

### MuseScore cannot connect to the server

Check that:

- The server is running locally
- Nothing else is already using port `5000`
- `bridge.js` still points to `http://127.0.0.1:5000`

### The plugin does not appear in MuseScore

Check that:

- The folder containing `FugueResolve.qml` and `bridge.js` is inside a plugin search path
- The plugin is enabled in `Plugins > Plugin Manager`
- MuseScore was restarted after copying the files

### Python reports missing modules

Re-activate the virtual environment and reinstall dependencies:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### The solver fails to generate an instance for the next measure

- Ensure that any edits made to the piece satisfy the rules of counterpoint
- Click `Reset` and re-try the generation, selecting different instances for previous measures