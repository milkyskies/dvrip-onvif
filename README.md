# dvrip-onvif

An ONVIF PTZ shim for a camera that speaks **dvrip and nothing else**.

The bedroom camera on ceres is an iCSee CACAGOO S2 Pro. It has no ONVIF, no web UI and no RTSP of
its own. Frigate can drive a camera over ONVIF and over nothing else. This service stands between
them: it presents itself as an ONVIF camera, accepts the two commands Frigate's arrow buttons
produce, and turns each one into a dvrip command.

**Video does not pass through here.** go2rtc holds the one stream and Frigate reads that.

**The full reference is [`~/desk/docs/20-services/bedroom-camera-ptz.md`](../../desk/docs/20-services/bedroom-camera-ptz.md).**
Read it before changing anything. This file is the short version.

## What consumes it

Frigate, at `http://dvrip-onvif:8000` over `dokploy-network`. Nothing else, and there is no
ingress: no Traefik label, no domain and no published port.

## How it is deployed

**Dokploy**, compose mode, `Home` project, `production` environment, compose name `dvrip-onvif`.

`docker-compose.yml` in this repo is the commented source. Dokploy strips every comment when it
re-serialises the file, so paste this one into the Dokploy editor when it changes.

There is no build step. Everything here is standard library Python, so the service runs a pinned
`python:3.12-slim` with the source bind mounted read only.

## Layout

| Path | What |
|---|---|
| `src/server.py` | The HTTP server, the routing and the request handlers |
| `src/motion.py` | **The only thing that talks to the camera.** One worker thread, and the four safety rules |
| `src/motion_test.py` | Proves those rules against a fake camera. No test framework needed |
| `src/soap.py` | SOAP 1.2 envelopes, faults and parsing |
| `src/auth.py` | WS-Security UsernameToken checking |
| `src/device_service.py` | ONVIF device service. Decides which services Frigate believes exist |
| `src/media_service.py` | ONVIF media service. **The PTZConfiguration here decides which buttons appear** |
| `src/ptz_service.py` | ONVIF PTZ service, and every operation it refuses |
| `src/settings.py` | The environment, read once |

## Depends on

`~/src/python-dvr`, mounted read only. It carries this house's single byte JSON terminator patch,
without which no login to this camera succeeds, and `ptzcore.py` in it is the single source of
truth for the dvrip motion commands. `ptz.py` there is the command line over the same module.

## Develop

```sh
# Run the safety tests. They touch no hardware.
PYTHONPATH=src:/home/onesc/src/python-dvr python3 src/motion_test.py

# Deploy a source change.
docker restart dvrip-onvif
```

## The rule that matters

**A camera that keeps moving after the button is released is worse than no buttons.** Every design
choice in `src/motion.py` follows from that. Read the docstring at the top of it before you change
the worker loop.
