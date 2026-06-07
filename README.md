# u6c pc webcam scanner

it does not need android, pydroid, or kivy.

---

## quick powershell run

from the project folder:

```powershell
cd .\u6cgeo
```

pc webcam as the camera, with the processed view hosted on the lan so it can be opened on a phone:

```powershell
py -3 .\u6c_pc_webcam.py --fps 30 --continuous-yolo --preload-yolo --lan-stream --lan-fps 30
```

phone camera as the camera, with the processed u6c view shown back on the phone:

```powershell
py -3 .\u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --preload-yolo --phone-fps 30 --phone-processed-fps 30
```

for the phone-camera https version, install and trust the u6c phone certificate one time:

```powershell
py -3 .\make_u6c_phone_https_cert.py
```

then open the printed `https://...:8090/` url on the phone.

fps is capped at 60. use 20 for lighter cpu usage, 30 for a better live view, or 60 if the pc and network can keep up.

---

## build exe

to build a windows exe release folder:

```powershell
cd .\u6cgeo
.\build_u6c_exe.ps1
```

the release will be created here:

```text
dist\u6c
```

that folder includes:

```text
u6c.exe
u6c_cert_helper.exe
launch .bat files
models
certs
docs
```

share the `dist\u6c` folder, not the source folder.

---

## what it does

- opens a pc webcam with opencv.
- can use a phone/browser camera on the lan as the input camera.
- detects pixel-level micro-motion from the live camera feed.
- turns motion hits into persistent tracked dots with ids.
- adds fading motion particles across moving areas.
- locks onto the strongest motion near the center reticle or near a mouse click.
- smooths target movement with a kalman filter.
- uses optical flow to help recover a target after brief motion loss.
- shows a radar-style motion panel and enlarged target inspector.
- can tag the locked crop once with a yolov8 onnx model.
- can run live full-frame yolo boxes for faces, people, and objects.

---

## install

install python 3.10 or newer.

from the project folder:

```powershell
py -3 -m pip install -r requirements.txt
```

the scanner works without a yolo model.

to enable the default people/object model:

```powershell
py -3 download_model.py --model people
```

that model is a coco yolo model. it can tag full human bodies as `person`, plus common objects like birds, airplanes, cars, and boats.

for face tagging:

```powershell
py -3 download_model.py --model face
```

to download all built-in model profiles:

```powershell
py -3 download_model.py --all
```

for the general coco yolov8n/s/m/l/x set:

```powershell
py -3 download_model.py --general
```

---

## run

basic start:

```powershell
py -3 u6c_pc_webcam.py
```

use a different webcam:

```powershell
py -3 u6c_pc_webcam.py --camera 1
```

use the face model:

```powershell
py -3 u6c_pc_webcam.py --model models\yolov8n-face-lindevs.onnx --model-label face
```

start with live face scanning already on:

```powershell
py -3 u6c_pc_webcam.py --model models\yolov8n-face-lindevs.onnx --model-label face --continuous-yolo
```

preload yolo profiles so switching models in the app is faster:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --preload-yolo
```

the general ensemble can be turned on in the app with the `ens` button or the `e` key. when it is enabled, all downloaded general coco models run together and overlapping boxes keep the higher-confidence detection.

you can also run:

```powershell
run_windows.bat
```

---

## lan phone feed

start the app with lan streaming:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --preload-yolo --lan-stream
```

the terminal will print something like:

```text
lan stream online: http://192.168.1.23:8080/
```

open that url from a phone browser on the same wi-fi network.

if windows asks about firewall access for python, allow access on private networks.

if the phone feed is choppy, lower the stream load:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --lan-stream --lan-fps 8 --lan-scale 0.5 --lan-quality 60
```

---

## lan phone camera input

this mode hosts a small page on the pc. the phone opens the page, uses the phone camera, and sends frames back to the pc. the yolo models still run on the pc.

plain http may work on some android browsers, but iphones need https for browser camera access.

for iphone, first make the local https certificate files:

```powershell
py -3 -m pip install -r requirements.txt
py -3 make_u6c_phone_https_cert.py
```

if the app prints a different pc ip later, rerun it with:

```powershell
py -3 make_u6c_phone_https_cert.py --host your-pc-ip
```

then start https phone-camera mode:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --preload-yolo
```

the terminal prints two urls like:

```text
phone https cert helper: http://192.168.1.23:8091/
phone camera input online: https://192.168.1.23:8090/
```

on the iphone:

1. open the cert helper url, like `http://192.168.1.23:8091/`.
2. download the u6c certificate.
3. open ios settings and install the downloaded profile.
4. go to settings > general > about > certificate trust settings.
5. enable full trust for `u6c phone camera local ca`.
6. open the https camera url, like `https://192.168.1.23:8090/`.
7. tap start camera and allow camera access.

port `8090` is https in this mode. opening `http://192.168.1.23:8090/` may fail because safari may choose plain http.

for non-iphone browsers that allow lan camera access over http:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --continuous-yolo --preload-yolo
```

the terminal prints a url like:

```text
phone camera input online: http://192.168.1.23:8090/
```

the pc window switches from the waiting screen to the phone camera feed once frames arrive.

phone-camera mode requests a 16:9 stream and fits phone frames into a 16:9 scanner view. this keeps an iphone 15 feed from stretching in the desktop window. holding the phone sideways gives the cleanest full-frame view.

the same phone page can also show the processed pc view. use the `processed` button to see yolo boxes, motion dots, hud, and focus panels from the pc. use `camera` to see the raw phone preview.

to make the phone feed fill the scanner view, even if it crops the edges:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --phone-aspect-mode crop --continuous-yolo --preload-yolo
```

to let another device watch the processed u6c view too:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --preload-yolo --lan-stream
```

in that mode:

```text
phone camera and same-phone processed page: https://your-pc-ip:8090/
phone certificate helper page: http://your-pc-ip:8091/
extra processed u6c viewing page: http://your-pc-ip:8080/
```

if the phone camera page is choppy, lower the upload load:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --phone-fps 8 --phone-width 960 --phone-quality 60
```

if the processed view on the phone is choppy, lower the return stream:

```powershell
py -3 u6c_pc_webcam.py --phone-camera --phone-https --continuous-yolo --phone-processed-fps 5 --phone-processed-scale 0.5 --phone-processed-quality 58
```

---

## discord person alerts

set the discord webhook url in powershell:

```powershell
$env:u6c_discord_webhook = "paste_your_discord_webhook_url_here"
```

then start person alerts:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --preload-yolo --discord-person-alerts
```

the app waits 0.5 seconds after `person` is detected, then sends a processed snapshot. that delay helps avoid blurry first-frame captures. it also remembers recent person boxes so it does not spam the same person every frame.

useful alert tuning:

```powershell
py -3 u6c_pc_webcam.py --continuous-yolo --discord-person-alerts --discord-person-cooldown 180 --discord-person-confidence 0.55 --discord-snapshot-delay 0.8
```

---

## controls

| control | action |
|---|---|
| space or c | capture target near the center reticle |
| left mouse click | capture target near the clicked point |
| right mouse click or u | unlock target |
| f | toggle optical flow |
| r | toggle radar |
| m | cycle render mode |
| y | toggle yolo snap tagging |
| g | toggle live full-frame yolo scanning |
| e | toggle general-model ensemble mode |
| o | cycle yolo model profile inside the app |
| b | toggle the clickable control menu |
| h | toggle hud |
| v | mirror camera image |
| + / - | digital zoom in / out |
| x | reset zoom |
| [ / ] | lower / raise motion threshold |
| , / . | shrink / grow lock radius |
| q or esc | quit |

---

## mouse controls

- click a control-menu button to change scanner settings without closing the app.
- drag the `sens`, `min size`, and `body merge` sliders to tune motion detection live.
- click inside a live yolo box to open a corner focus preview, zoom toward that target, and request a lock.
- click a green tracked motion dot to lock onto that motion track.
- left-click outside the menu, yolo boxes, and tracked dots to capture near the click.
- right-click to unlock.

---

## practical settings

for sky tracking, a stable tripod helps more than any software setting.

good starting point:

```powershell
py -3 u6c_pc_webcam.py --threshold 14 --min-area 24 --motion-merge-radius 18 --lock-radius 90 --zoom 1.5
```

if the scene is noisy, raise `--threshold`.

if tiny objects are missed, lower it.

for random speckles, raise `min size` or lower `sens`.

for a moving person turning into too many dots, raise `body merge`.

motion dots fade after a few seconds. to change that from startup:

```powershell
py -3 u6c_pc_webcam.py --motion-dot-life 3.5 --motion-dot-density 45
```

---

## notes

this app only analyzes webcam pixels locally.

it does not emit radar, transmit signals, interfere with aircraft, or physically interact with anything in view.
