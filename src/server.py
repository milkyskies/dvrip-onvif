"""An ONVIF PTZ shim for a camera that speaks dvrip and nothing else.

The bedroom camera is an iCSee CACAGOO S2 Pro. It has no ONVIF, no web UI and
no RTSP of its own. Frigate can only drive a camera over ONVIF, so this service
presents itself as an ONVIF camera, accepts the pan and tilt commands Frigate
sends, and turns each one into a dvrip command.

Frigate points at this service instead of at the camera. Video does not come
through here at all: go2rtc holds the one stream and Frigate reads that.

Read ~/desk/docs/20-services/bedroom-camera-ptz.md before you change this.
"""

import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import device_service
import media_service
import ptz_service
import settings as settings_module
from auth import is_authorised
from motion import MotionController
from soap import (
    CONTENT_TYPE,
    MAX_REQUEST_BYTES,
    TPTZ,
    TT,
    MalformedRequest,
    envelope,
    fault,
    find_text,
    parse,
    qname,
)

logger = logging.getLogger("dvrip-onvif")

# Every operation that could move the camera on one command, without a press and
# a release. Each one is refused. Read the note in ptz_service.py: GotoPreset
# made this camera move and not stop.
REFUSED_PTZ_ACTIONS = {
    "GotoPreset": "this camera does not stop reliably after GotoPreset, so it is refused",
    "SetPreset": "this shim stores no presets",
    "RemovePreset": "this shim stores no presets",
    "SetHomePosition": "this shim stores no home position",
    "GotoHomePosition": "this shim stores no home position",
    "AbsoluteMove": "the camera reports no position, so it cannot move to one",
    "RelativeMove": "the camera reports no position, so it cannot move by one",
    "GetStatus": "the camera reports no pan or tilt position",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "dvrip-onvif"
    sys_version = ""

    # ------------------------------------------------------------------ HTTP

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/healthz":
            self._send(404, b"not found", "text/plain")

            return

        status = self.server.motion.status()
        body = json.dumps(
            {
                "moving": status.moving,
                "direction": status.direction,
                "movesStarted": status.moves_started,
                "stopsCompleted": status.stops_completed,
                "watchdogStops": status.watchdog_stops,
                "stopFailures": status.stop_failures,
                "lastError": status.last_error,
            }
        ).encode("utf-8")
        self._send(200, body, "application/json")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST_BYTES:
            self._send(413, b"too large", "text/plain")

            return

        body = self.rfile.read(length)

        try:
            action, request, header = parse(body)
        except MalformedRequest as error:
            logger.warning("rejected a request on %s: %s", path, error)
            self._send(400, fault(str(error), "ter:WellFormed"), CONTENT_TYPE)

            return

        # GetSystemDateAndTime is unauthenticated by the ONVIF specification, so
        # that a client can align its clock before it signs anything.
        config = self.server.settings
        if action != "GetSystemDateAndTime" and not is_authorised(
            header, config.username, config.password
        ):
            logger.warning("rejected an unauthorised %s on %s", action, path)
            self._send(
                401,
                fault("the sender is not authorised", "ter:NotAuthorized"),
                CONTENT_TYPE,
            )

            return

        self._dispatch(path, action, request)

    # -------------------------------------------------------------- dispatch

    def _dispatch(self, path: str, action: str, request) -> None:
        if path == device_service.DEVICE_PATH:
            self._device(action)

            return

        if path == device_service.MEDIA_PATH:
            self._media(action)

            return

        if path == device_service.PTZ_PATH:
            self._ptz(action, request)

            return

        self._fault(f"no service at {path}")

    def _device(self, action: str) -> None:
        base = self._base_url()

        if action == "GetCapabilities":
            self._ok(device_service.get_capabilities(base))

            return

        if action == "GetServices":
            self._ok(device_service.get_services(base))

            return

        if action == "GetDeviceInformation":
            self._ok(device_service.get_device_information())

            return

        if action == "GetSystemDateAndTime":
            self._ok(device_service.get_system_date_and_time())

            return

        self._unsupported(action)

    def _media(self, action: str) -> None:
        config = self.server.settings

        if action == "GetProfiles":
            self._ok(media_service.get_profiles(config.video_width, config.video_height))

            return

        if action == "GetVideoSources":
            self._ok(
                media_service.get_video_sources(config.video_width, config.video_height)
            )

            return

        self._unsupported(action)

    def _ptz(self, action: str, request) -> None:
        if action in REFUSED_PTZ_ACTIONS:
            logger.info("refused %s", action)
            self._fault(REFUSED_PTZ_ACTIONS[action])

            return

        if action == "ContinuousMove":
            self._continuous_move(request)

            return

        if action == "Stop":
            self.server.motion.request_stop()
            self._ok(ptz_service.stop_response())

            return

        if action == "GetPresets":
            self._ok(ptz_service.get_presets())

            return

        if action == "GetServiceCapabilities":
            self._ok(ptz_service.get_service_capabilities())

            return

        if action == "GetConfigurations":
            self._ok(ptz_service.get_configurations())

            return

        if action == "GetConfigurationOptions":
            self._ok(ptz_service.get_configuration_options())

            return

        if action == "GetNodes":
            self._ok(ptz_service.get_nodes())

            return

        if action == "GetNode":
            self._ok(ptz_service.get_node())

            return

        self._unsupported(action)

    def _continuous_move(self, request) -> None:
        velocity = request.find(qname(TPTZ, "Velocity"))
        pan_tilt = None
        if velocity is not None:
            pan_tilt = velocity.find(qname(TT, "PanTilt"))

        if pan_tilt is None:
            # A zoom only move. This camera has no zoom, so there is nothing to
            # start and nothing to stop.
            logger.info("ignored a ContinuousMove that carried no pan or tilt")
            self._ok(ptz_service.continuous_move_response())

            return

        pan = _as_float(pan_tilt.get("x"))
        tilt = _as_float(pan_tilt.get("y"))
        direction = ptz_service.direction_for(pan, tilt)

        if not direction:
            # A velocity of zero means stop, by the ONVIF specification.
            self.server.motion.request_stop()
            self._ok(ptz_service.continuous_move_response())

            return

        timeout = _duration_seconds(find_text(request, TPTZ, "Timeout"))
        self.server.motion.request_move(
            direction, ptz_service.step_for(pan, tilt), timeout
        )
        self._ok(ptz_service.continuous_move_response())

    # --------------------------------------------------------------- replies

    def _ok(self, body_element) -> None:
        self._send(200, envelope(body_element), CONTENT_TYPE)

    def _fault(self, reason: str, subcode: str = "ter:ActionNotSupported") -> None:
        """Answer with a SOAP fault.

        A SOAP 1.2 fault travels on HTTP 500. The ONVIF client only reads the
        fault out of the body when the status is not 200, so a fault sent with
        200 would be parsed as a reply and confuse the caller.
        """
        self._send(500, fault(reason, subcode), CONTENT_TYPE)

    def _unsupported(self, action: str) -> None:
        logger.info("no handler for %s", action)
        self._fault(f"{action} is not implemented")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _base_url(self) -> str:
        """Build the address to advertise from the address the client used.

        Reporting the client's own Host back means the service addresses in
        GetCapabilities are always reachable by whoever asked, with nothing to
        configure.
        """
        host = self.headers.get("Host")
        if not host:
            host = f"{self.server.server_address[0]}:{self.server.server_address[1]}"

        return f"http://{host}"

    def log_message(self, format: str, *args) -> None:
        logger.debug("%s %s", self.address_string(), format % args)


def _as_float(raw) -> float:
    if raw is None:
        return 0.0

    try:
        return float(raw)
    except ValueError:
        return 0.0


def _duration_seconds(raw: str) -> float:
    """Read an xs:duration such as PT5S into seconds.

    Only the second and minute forms that a PTZ client sends are read. Anything
    else falls back to the shim's own limit, which the caller clamps anyway.
    """
    text = raw.strip().upper()
    if not text.startswith("PT"):
        return float("inf")

    total = 0.0
    number = ""
    for character in text[2:]:
        if character.isdigit() or character == ".":
            number += character
            continue

        if not number:
            return float("inf")

        if character == "M":
            total += float(number) * 60
        elif character == "S":
            total += float(number)
        else:
            return float("inf")

        number = ""

    if total <= 0:
        return float("inf")

    return total


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config, motion) -> None:
        self.settings = config
        self.motion = motion
        super().__init__(address, Handler)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )

    try:
        config = settings_module.load()
    except settings_module.SettingsError as error:
        logger.error("configuration is not usable: %s", error)

        return 2

    motion = MotionController(config.max_move_seconds)
    motion.start()

    server = Server((config.listen_host, config.listen_port), config, motion)
    logger.info(
        "listening on %s:%d, a move stops after %.1fs at the latest",
        config.listen_host,
        config.listen_port,
        config.max_move_seconds,
    )

    def shut_down(signum, frame) -> None:
        logger.info("signal %d received, stopping the camera", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shut_down)
    signal.signal(signal.SIGINT, shut_down)

    try:
        server.serve_forever()
    finally:
        # Stop the camera before this process goes away. A restart must never
        # leave a motor running.
        motion.shutdown()
        server.server_close()
        logger.info("stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
