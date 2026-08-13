"""The runtime configuration for the shim.

Every value is read once, at startup, into one frozen object. Nothing below
this module reads the environment.
"""

import os
from dataclasses import dataclass

# The camera sends 1080p on its main stream. Frigate never plays video through
# this shim, it plays go2rtc, so these numbers only describe the profile that
# the ONVIF media service advertises.
DEFAULT_VIDEO_WIDTH = 1920
DEFAULT_VIDEO_HEIGHT = 1080

# How long one press may run the motors when no Stop arrives. This is the
# watchdog that stops a runaway, so it is a safety limit and not a preference.
DEFAULT_MAX_MOVE_SECONDS = 8.0


class SettingsError(Exception):
    """The environment does not hold a value the shim needs."""


@dataclass(frozen=True)
class Settings:
    listen_host: str
    listen_port: int
    username: str
    password: str
    max_move_seconds: float
    video_width: int
    video_height: int


def _required(name: str) -> str:
    """Read one value from the environment, or from the file that names it.

    ONVIF_PASSWORD_FILE wins over ONVIF_PASSWORD. A file keeps the credential
    out of the container environment, where `docker inspect` and the Dokploy
    API both print it in plain text.
    """
    path = os.environ.get(f"{name}_FILE", "")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError as error:
            raise SettingsError(f"{name}_FILE cannot be read: {error}") from error

        if not value:
            raise SettingsError(f"{name}_FILE is empty: {path}")

        return value

    value = os.environ.get(name, "")
    if not value:
        raise SettingsError(f"{name} is not set")

    return value


def _number(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default

    try:
        return float(raw)
    except ValueError as error:
        raise SettingsError(f"{name} is not a number: {raw}") from error


def load() -> Settings:
    """Read the environment into one Settings object.

    ONVIF_USERNAME and ONVIF_PASSWORD guard this shim. They are not the camera
    login. The camera login is read from the go2rtc config by camenv.py, which
    is the single source of truth for it.
    """
    max_move_seconds = _number("PTZ_MAX_MOVE_SECONDS", DEFAULT_MAX_MOVE_SECONDS)
    if max_move_seconds <= 0:
        raise SettingsError("PTZ_MAX_MOVE_SECONDS must be more than 0")

    return Settings(
        listen_host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        listen_port=int(_number("LISTEN_PORT", 8000)),
        username=_required("ONVIF_USERNAME"),
        password=_required("ONVIF_PASSWORD"),
        max_move_seconds=max_move_seconds,
        video_width=int(_number("VIDEO_WIDTH", DEFAULT_VIDEO_WIDTH)),
        video_height=int(_number("VIDEO_HEIGHT", DEFAULT_VIDEO_HEIGHT)),
    )
