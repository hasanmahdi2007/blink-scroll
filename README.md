# Blink Scroll

Hands-free "lazy scrolling" for Windows. Blink Scroll watches you through the laptop webcam and
scrolls the window under your mouse cursor when you make a **deliberate** eye gesture, by default
a fast double blink. Natural blinking (15-20 times a minute, often in pairs) is filtered out.

* Runs 100% locally with Google's pretrained MediaPipe FaceLandmarker. No training, no cloud.
* **Privacy:** frames are never stored or sent anywhere. The only network access is the one-time
  model download. `--record` saves numbers only (scores and head angles), never images.
* **Low power:** about 1-2% of total CPU while active. Drops further when nobody is in front of
  the camera, and to zero with the camera released when paused or when Windows is locked.

## Setup

Requires Windows 10/11 and a webcam.

1. Install Python 3.12 (MediaPipe supports 3.9-3.14; 3.12 has the most mature wheels):
   `winget install Python.Python.3.12`
2. In the project folder:
   ```
   %LOCALAPPDATA%\Programs\Python\Python312\python.exe -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   .venv\Scripts\python.exe -m blinkscroll.download_model
   ```
   The model (`models/face_landmarker.task`, ~3.7 MB) is downloaded once.

## Running

| How | What |
|---|---|
| `run.bat` | Normal use: no console, tray icon plus floating circle. |
| `debug.bat` | Debug window and console log of every blink and trigger. |
| `debug.bat --record` | The same, and saves the score time series to `recordings\*.csv`. |
| `.venv\Scripts\python.exe -m blinkscroll --autostart on` | Start with Windows (`off` to undo; also in the menus). |

Other flags: `--paused` (start with the camera off), `--stats` (log CPU and FPS every 10 s),
`--config FILE`.

### First start: calibration (about 6 s)

1. Blue circle, label "look at the screen with eyes open": keep your eyes open for 3 s.
2. **One beep:** close both eyes and keep them closed.
3. **Two beeps:** open them again. Done.

The result is saved, so later starts skip this. Recalibrate from the tray or circle menu, or with
**Ctrl+Alt+C**, when you put on or take off glasses or the lighting changes a lot.

### Controls

* **Floating circle** on the screen edge. It hides itself and leaves a small sliver; move the
  mouse onto the sliver and it slides out.
  * **Click:** on/off. Off releases the camera, so the camera light goes out.
  * **Drag:** move it along the edge, or drop it on the other half of the screen to switch sides.
  * **Right-click:** menu.
  * It flashes white on every scroll.
* **Tray icon:** the same menu. Double-click to pause or resume.
* **Hotkeys:** **Ctrl+Alt+B** pause/resume, **Ctrl+Alt+C** recalibrate, **Ctrl+Alt+Q** quit.

| Colour | Meaning |
|---|---|
| green | active, watching for gestures |
| teal | face seen, but you are not facing the screen (gestures ignored) |
| amber | no face; after 10 s it drops to low-power 4 fps |
| blue | calibrating |
| grey | paused, camera off |
| red | camera error (busy in another app, or unplugged); retries every 3 s |

### Gestures

| Gesture | Default | Action |
|---|---|---|
| Fast double blink: two full blinks, the 2nd ending within 0.7 s of the 1st starting | **on** | scroll down (5 notches over 150 ms) |
| Long blink: both eyes closed for 0.6 s or more | off | scroll down **while held** |
| Wink: one eye closed for 0.25 s or more, the other clearly open | off | **while held**: left = down, right = up (configurable) |

**Hold to scroll** (`scroll.hold_to_scroll`, on by default): once a wink or long blink is
recognised, the page keeps scrolling for as long as you hold it, so how long you hold decides
how far it goes. The speed starts at 4 notches/s and ramps to 12 notches/s over 2 s. It stops
when you open the eye, and after 20 s at the latest. After every gesture there is a 1 s
cooldown.

Winks use their own lower closed line (40% of the way from open to closed), because a wink
closes the eye less than a full blink. That is safe because the other eye must stay open at the
same time. If left and right come out reversed, set `detection.swap_eyes = true`.

**Which gesture?** It depends on how you blink. Run `tune.bat` (a guided 3-minute recording) and
compare the options with the replay tool. For one user with glasses, the recordings gave:
- long blink: 12 of 12 detected
- left wink: every clean wink detected
- fast double blink: only 2 of 11, because their deliberate double blinks often merged into one
  closure, and natural blink pairs while reading looked the same
- false scrolls across 200 s of reading, keyboard use and head turning: 0-1

## How it works

```
camera thread --newest frame only--> processing thread (capped 20 fps, 4 fps in low power)
  |                                    |
  | cv2 DirectShow 640x480             +-> downscale to 320 px -> MediaPipe FaceLandmarker (VIDEO mode)
  |                                    |     blendshapes: eyeBlinkLeft/Right, eyeLookDownLeft/Right
  |                                    |     4x4 transform matrix -> yaw / pitch / roll
  |                                    +-> EyeTracker   (eyes.py, pure)
  |                                    |     EMA smoothing -> calibrated per-eye thresholds
  |                                    |     -> hysteresis -> Blink events
  |                                    +-> GestureEngine (gestures.py, pure)
  |                                    |     facing-screen gate, double/long/wink, cooldown,
  |                                    |     slow baseline adaptation
  |                                    +-> Scroller (actions.py): smooth wheel steps
main thread: Tk event loop (floating circle) <- queue <- tray, hotkeys, pipeline state
```

* **Latest frame only.** The capture thread overwrites one slot, so stale frames are dropped and
  there is never a backlog. `read()` blocks until the next frame arrives, which keeps it cheap.
* **Smoothing.** An exponential moving average with a 40 ms time constant, so it behaves the same
  at any frame rate. It removes jitter while adding less than a frame of lag.
* **Calibration** measures both your open level *and* your closed level, per eye. With glasses a
  fully closed eye may only score about 0.45-0.55 instead of about 1.0, so fixed thresholds fail.
  For each eye, the "closed" threshold sits 60% of the way from open to closed and the "open"
  threshold sits at 30%.
* **Hysteresis.** An eye counts as closed above the closed threshold and as open again only below
  the open threshold, so a score hovering near one line can't flicker. A blink means both eyes
  closed and then both open again. Closures shorter than 2 frames above the threshold are noise.
* **False-trigger defences.** A double blink needs two *full* closures (at least 70% of your
  calibrated closed level, while many natural blinks are partial), each under 0.45 s, inside the
  0.7 s window. Then the 1 s cooldown applies.
* **Facing-screen gate.** Gestures are ignored when your head is turned (yaw over 20°), tilted
  (pitch over 12°, roll over 20°) or your **eyes look down** (`eyeLookDown` over 0.45). The last
  case catches glancing at the keyboard without moving your head, which also raises the blink
  score. Angles are measured relative to your neutral posture recorded during calibration. The
  gate is read while your eyes are open and held while they are closed, because pose and gaze
  estimates are unreliable with the lids shut.
* **Adaptive baseline.** While you face the screen with your eyes open, the open level drifts
  slowly toward the current value (time constant about 25 s, limited to ±0.1), so lighting
  changes don't break detection.
* **Power.**
  * After 10 s with no face, the app processes only 4 fps.
  * Pausing, or Windows being locked, releases the camera and blocks the processing thread
    entirely.
  * Tk, tray and hotkeys are event-driven; the UI queue is checked 6-7 times a second.

## Tuning guide

Settings live in `config.toml`, created with defaults on first run. Edit it, then choose
**Reload config** from either menu; no restart is needed. Delete the file to restore defaults.

1. Run `debug.bat --record`. The debug window shows raw (dim) and smoothed (bright) scores, the
   red (closed) and green (open) threshold lines, head angles relative to neutral, gaze-down, the
   gate, and the blink and trigger counters.
2. Read normally for a few minutes, then do some deliberate double blinks.
3. Replay the recording with any config change, without the camera:
   `.venv\Scripts\python.exe -m blinkscroll.replay recordings\scores-....csv`

| Symptom | Try |
|---|---|
| Natural blink pairs scroll | Lower `gestures.double_blink.window_s` (e.g. 0.55); raise `min_closure` (0.8). |
| Deliberate double blinks missed | Blink a bit more firmly. Raise `window_s` (0.8); lower `min_closure` (0.6); raise `processing.fps` (25). |
| Blinks not counted at all | Recalibrate. Lower `calibration.closed_fraction` (0.5). |
| Triggers while looking at the keyboard | Lower `head_pose.max_look_down` (0.35) or `max_pitch_deg` (8). |
| Nothing works while you face the screen | The gate is too strict (teal circle): check the debug window's gate line, raise the limit it names. |
| Scroll too far or too short | `scroll.amount` (notches; 1 notch is about 3 lines). |
| Scroll should feel smoother | `scroll.steps = 10` gives half-notch steps (most modern apps support it). |

## Measured performance

Laptop: Windows 11, built-in webcam at 640x480, inference on 320-px frames (about 3.3 ms per
frame). "% of total" covers all 8 logical CPUs.

| Mode | CPU (one core) | CPU (total) | Processing FPS |
|---|---|---|---|
| Active, 15 fps (no debug window) | ~11% | ~1.4% | 15.1-15.5 |
| Active, 15 fps, with debug window | ~20% | ~2.5% | 14.8 |
| Low-power, no face (4 fps) | 0.3-1.1% | 0.04-0.14% | <=4 |
| Paused / Windows locked (camera released) | 0.0% | 0.0% | 0 |

> Note: the low-power figure was taken with the session locked, when the camera delivered black
> frames at ~1 fps, so it is a lower bound. Active mode at the current 20 fps default is
> re-measured in the next update.

## Development

```
.venv\Scripts\python.exe -m pytest              # 42 unit tests (gesture state machine etc.)
.venv\Scripts\python.exe -m tests.manual_scroll_check   # real wheel events into a test window
```

Modules: `camera.py` (capture thread), `landmarks.py` (MediaPipe to `Sample`), `pose.py`,
`eyes.py` and `gestures.py` (pure, unit-tested logic), `actions.py` (scrolling), `pipeline.py`
(processing loop, low-power, pause), `controls/` (hotkeys, tray, floating circle), `app.py`
(wiring), `config.py`, `recorder.py` and `replay.py`, `system.py` (lock detection, single instance,
autostart).
