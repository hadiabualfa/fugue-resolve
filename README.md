# FugueResolve

FugueResolve is a MuseScore plugin for assisted fugue writing. It uses:

- `z3-solver` to generate contrapuntal continuations
- `music21` to represent and transform musical material
- `Flask` to run a small local server that connects MuseScore to Python
- QML/JavaScript to provide the MuseScore plugin UI

The plugin supports two workflows:

- `Generation mode`: generate fugues measure-by-measure based on a given subject
- `Evaluation mode`: highlight issues in a selected passage and step through them one at a time

## Requirements

- MuseScore `3` or later
- Python `3.10` recommended
- A local Python environment with the packages in `requirements.txt`

Tested Python package versions in this repository:

- `Flask==3.1.3`
- `music21==9.9.1`
- `z3-solver==4.16.0.0`

## Repository Layout

- `FugueResolve.qml`: MuseScore plugin UI
- `bridge.js`: MuseScore-to-Python bridge
- `main.py`: local Flask server with generation/evaluation endpoints
- `fugue_solver.py`: Z3 solver and counterpoint evaluator
- `fugue_state.py`: state machine

## 1. Download the Project

Clone or download this repository to a local folder:

```bash
git clone <your-repo-url> fugue-resolve
cd fugue-resolve
```

If you downloaded a ZIP file instead, extract it and open the extracted folder in a terminal.

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

The simplest setup is to place the whole project folder inside a MuseScore plugin directory, or point MuseScore to this folder as a custom plugin location.

Important:

- `FugueResolve.qml` and `bridge.js` must stay in the same folder
- `main.py`, `fugue_solver.py`, and `fugue_state.py` must stay together wherever you launch the Python server

Typical workflow:

1. Copy the repository folder into a MuseScore plugins folder, or add the folder to MuseScore's plugin search path.
2. Restart MuseScore.
3. Open `Plugins > Plugin Manager`.
4. Enable `FugueResolve`.

After enabling it, the plugin appears in:

```text
Plugins > FugueResolve
```

## 5. Start the Python Server

From the project root, activate the environment if needed and run:

```bash
source .venv/bin/activate
python main.py
```

This starts the local Flask server on:

```text
http://127.0.0.1:5000
```

Keep this terminal open while using the plugin. The MuseScore bridge sends requests to:

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

- The plugin writes generated material directly into the score.
- The server keeps an internal fugue state, so generation is intended to proceed section by section.
- `Reset` clears the server state and returns the UI to the initial generation state.

### Evaluation Mode

1. Highlight the passage you want to inspect.
2. Click `Evaluate`.
3. The plugin switches into evaluation mode and:
   - highlights one issue in the score
   - describes that issue in the plugin window
4. Use `Next` and `Prev` to move through the issues one at a time.
5. Edit the score in MuseScore.
6. Click `Re-run Evaluate` to refresh the issue list.
7. Click `Reset` to leave evaluation mode and return to normal generation mode.

## Recommended Workflow

1. Start the Python server.
2. Open MuseScore and enable the plugin.
3. Highlight a subject and generate the exposition.
4. Continue with episodes and middle entries.
5. Highlight the full passage and run `Evaluate`.
6. Step through issues with `Next` and `Prev`.
7. Re-run evaluation after edits.

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

## Development Notes

- The server is started with `debug=True` in `main.py`.
- The current bridge expects the local server to stay on `127.0.0.1:5000`.
- MuseScore-side generation and evaluation both depend on the Python server being available.
