# U6C PC Webcam Scanner

## Quick PowerShell Run

PC webcam as the camera, hosted to LAN so you can view it on mobile:

```powershell
py -3 "C:\Users\br\Documents\Codex\2026-06-05\https-github-com-kruix-art-minos\outputs\minos_pc_webcam\u6c_pc_webcam.py" --fps 30 --continuous-yolo --preload-yolo --lan-stream --lan-fps 30
```

iPhone/phone camera as the camera, with the processed U6C view shown on the phone:

```powershell
py -3 "C:\Users\br\Documents\Codex\2026-06-05\https-github-com-kruix-art-minos\outputs\minos_pc_webcam\u6c_pc_webcam.py" --phone-camera --phone-https --continuous-yolo --preload-yolo --phone-fps 30 --phone-processed-fps 30
```

For the phone-camera HTTPS version, install/trust the U6C phone certificate once
with `make_u6c_phone_https_cert.py`, then use the printed `https://...:8090/`
URL on the phone.

FPS options are capped at `60`. Use `20` for smoother CPU usage, `30` for a
better live view, or `60` if the PC and network can keep up.

## Build EXE

To build a Windows EXE release folder:

```powershell
cd "C:\Users\br\Documents\Codex\2026-06-05\https-github-com-kruix-art-minos\outputs\minos_pc_webcam"
.\build_u6c_exe.ps1
```

The finished release appears in:

```text
dist\U6C
```

That folder contains `U6C.exe`, `U6C_Cert_Helper.exe`, launch `.bat` files,
`models`, `certs`, and docs. Share the `dist\U6C` folder, not the readable
source folder.

This is a desktop recreation of the U6C SIGINT / UFO scanner concept for a PC
with an attached webcam. It does not need Android, Pydroid, or Kivy.

## What It Does

- Opens a PC webcam with OpenCV.
- Optionally uses a phone/browser camera on the LAN as the input camera.
- Detects pixel-level micro-motion from the live camera feed.
- Turns micro-motion hits into persistent tracked dots with IDs.
- Adds fading motion-dot particles across moving regions.
- Locks the strongest motion near the center reticle, or near a mouse click.
- Smooths target movement with a Kalman filter.
- Uses optical flow to help recover a target after brief motion loss.
- Shows a radar-style motion panel and enlarged target inspector.
- Optionally tags the locked crop once with a YOLOv8 ONNX model.
- Optionally runs live full-frame YOLO boxes for faces, people, or objects.

## Install

Install Python 3.10 or newer, then from this folder run:

```powershell
py -3 -m pip install -r requirements.txt
```

The scanner works without the YOLO model. To enable one-shot object tagging with
the default people/object model:

```powershell
py -3 download_model.py --model people
```

That default model is a COCO YOLO model. It can tag full human bodies as
`PERSON`, plus common objects such as birds, airplanes, cars, and boats.

For face tagging, download a face-specific YOLO model:

```powershell
py -3 download_model.py --model face
```

Or download all built-in model profiles:

```powershell
py -3 download_model.py --all
```

For a broad general-purpose ensemble, download the COCO YOLOv8n/s/m/l/x set:

```powershell
py -3 download_model.py --general
```

## Run

```powershell
py -3 u6c_pc_webcam.py
```

If your webcam is not camera 0:

```powershell
py -3 u6c_pc_webcam.py --camera 1
```

To use the face model instead of the default people/object model:

```powershell
py -3 u6c_pc_webcam.py --model models\yolov8n-face-lindevs.onnx --model-label FACE
```

To start with live face scanning already turned on:

```powershell
py -3 u6c_pc_webcam.py --model models\yolov8n-face-lindevs.onnx --model-label FACE --continuous-yolo
```

To preload available YOLO profiles so in-app model switching is faster:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --preload-yolo
```

To keep model switching fast while leaving ensemble off at startup:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --preload-yolo
```

Turn ensemble on manually with the `ENS` button or `E` key. When enabled, all
downloaded general COCO models run together and overlapping boxes keep the
higher-confidence detection.

You can also use:

```powershell
run_windows.bat
```

## LAN Phone Feed

Start the app with LAN streaming enabled:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --preload-yolo --lan-stream
```

The terminal prints a URL like:

```text
LAN stream online: http://192.168.1.23:8080/
```

Open that URL from a phone browser on the same Wi-Fi network. If Windows asks
about firewall access for Python, allow access on private networks.

If the phone feed is choppy, lower the stream load:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --lan-stream --lan-fps 8 --lan-scale 0.5 --lan-quality 60
```

## LAN Phone Camera Input

This mode hosts a small website on the PC. Your phone opens that website, uses
the phone camera, and sends frames back to the PC. The YOLO models still run on
the PC.

Plain HTTP may work on some Android browsers, but iPhones require HTTPS for
browser camera access. For iPhone, first make the local HTTPS certificate files:

```powershell
py -3 -m pip install -r requirements.txt
py -3 make_u6c_phone_https_cert.py
```

If the app prints a different PC IP later, rerun that command with
`--host YOUR-PC-IP`.

Then start the PC in HTTPS phone-camera mode:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --preload-yolo
```

The terminal prints two URLs like:

```text
Phone HTTPS cert helper: http://192.168.1.23:8091/
Phone camera input online: https://192.168.1.23:8090/
```

On the iPhone:

1. Open the cert helper URL first, such as `http://192.168.1.23:8091/`.
2. Download the U6C certificate.
3. Open iOS Settings and install the downloaded profile.
4. Go to Settings > General > About > Certificate Trust Settings.
5. Enable full trust for `U6C Phone Camera Local CA`.
6. Open the HTTPS camera URL, such as `https://192.168.1.23:8090/`.
7. Tap `Start Camera` and allow camera access.

Port `8090` is HTTPS in this mode. Opening `http://192.168.1.23:8090/` or just
typing `192.168.1.23:8090` may fail because Safari can choose plain HTTP.

For non-iPhone browsers that allow LAN camera access over HTTP, this simpler
mode is still available:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --continuous-yolo --preload-yolo
```

That terminal prints a URL like:

```text
Phone camera input online: http://192.168.1.23:8090/
```

The PC window will switch from the waiting screen to the phone camera feed once
frames arrive.

Phone-camera mode now requests a 16:9 stream and fits phone frames into a 16:9
scanner view. This keeps an iPhone 15 feed from being stretched in the desktop
window. Hold the phone sideways for the cleanest full-frame view.

The same phone page also shows the PC-processed view. Use the `Processed` button
to see YOLO boxes, motion dots, HUD, and focus panels from the PC, or `Camera`
to see the raw phone preview. The models still run on the PC.

If you want the phone feed to fill the scanner view even if it has to crop the
edges:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --phone-aspect-mode crop --continuous-yolo --preload-yolo
```

To let another device watch the processed U6C view too, also enable LAN stream:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --preload-yolo --lan-stream
```

In that mode:

- Phone camera and same-phone processed page: `https://YOUR-PC-IP:8090/`
- Phone certificate helper page: `http://YOUR-PC-IP:8091/`
- Extra processed U6C viewing page: `http://YOUR-PC-IP:8080/`

If the phone camera page is choppy, lower the upload load:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --phone-fps 8 --phone-width 960 --phone-quality 60
```

If the processed view on the phone is choppy, lower the return stream:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --phone-processed-fps 5 --phone-processed-scale 0.5 --phone-processed-quality 58
```

## Discord Person Alerts

Set your Discord webhook URL in PowerShell, then start person alerts:

```powershell
$env:U6C_DISCORD_WEBHOOK = "PASTE_YOUR_DISCORD_WEBHOOK_URL_HERE"
py -3 u6c_pc_webcam.py --continuous-yolo --preload-yolo --discord-person-alerts
```

The app waits `0.5` seconds after `PERSON` is detected, then sends a processed
snapshot. That short delay helps avoid blurry first-frame captures. It also
remembers recent person boxes so it does not spam the same person every frame.

Useful alert tuning:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --discord-person-alerts --discord-person-cooldown 180 --discord-person-confidence 0.55 --discord-snapshot-delay 0.8
```

## Controls

| Control | Action |
| --- | --- |
| Space or C | Capture target near the center reticle |
| Left mouse click | Capture target near the clicked point |
| Right mouse click or U | Unlock target |
| F | Toggle optical flow |
| R | Toggle radar |
| M | Cycle render mode |
| Y | Toggle YOLO snap tagging |
| G | Toggle live full-frame YOLO scanning |
| E | Toggle general-model ensemble mode |
| O | Cycle YOLO model profile inside the app |
| B | Toggle the clickable control menu |
| H | Toggle HUD |
| V | Mirror camera image |
| + / - | Digital zoom in / out |
| X | Reset zoom |
| [ / ] | Lower / raise motion threshold |
| , / . | Shrink / grow lock radius |
| Q or Esc | Quit |

## Mouse Controls

- Click a control-menu button to change scanner settings without closing the app.
- Drag the `SENS`, `MIN SIZE`, and `BODY MERGE` sliders to tune motion detection live.
- Click inside a live YOLO box to open a corner focus preview, zoom toward that target, and request a lock.
- Click a green tracked motion dot to lock onto that same motion track.
- Left-click outside the menu, YOLO boxes, and tracked dots to capture near the click.
- Right-click to unlock.

## Practical Settings

For sky tracking, a stable tripod helps more than any software setting. Start
with:

```powershell
py -3 u6c_pc_webcam.py --threshold 14 --min-area 24 --motion-merge-radius 18 --lock-radius 90 --zoom 1.5
```

If the scene is noisy, raise `--threshold`. If tiny objects are missed, lower it.
For random speckles, raise `MIN SIZE` or lower `SENS`. For a moving person
turning into too many dots, raise `BODY MERGE`.

Motion dots fade after a few seconds. To change that from startup:

```powershell
py -3 u6c_pc_webcam.py --motion-dot-life 3.5 --motion-dot-density 45
```

## Notes

This app only analyzes webcam pixels locally. It does not emit radar, transmit
signals, interfere with aircraft, or interact physically with anything in view.
