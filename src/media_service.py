"""The ONVIF media service.

Frigate reads one thing from here that matters: a media profile that carries a
PTZConfiguration. Frigate walks the profile list and takes the first profile
that has both a VideoEncoderConfiguration and a PTZConfiguration with a default
continuous velocity space. Without that, it decides the camera is not a PTZ
camera and shows no arrows.

**The PTZConfiguration names one space and only one.** Frigate turns each
default space in this block into a feature in its UI:

  DefaultContinuousPanTiltVelocitySpace  -> "pt", the arrow buttons
  DefaultContinuousZoomVelocitySpace     -> "zoom", zoom buttons
  DefaultRelativePanTiltTranslationSpace -> "pt-r"
  DefaultRelativeZoomTranslationSpace    -> "zoom-r"
  DefaultAbsoluteZoomPositionSpace       -> "zoom-a"

This camera pans and tilts and does nothing else, so this block names the
continuous pan and tilt space alone. That is why the UI shows four arrows and no
zoom. Do not add a space here to look complete: the camera answers OK to zoom
and focus commands and then does not move, so any extra space here becomes a
button that lies.

Frigate never plays video through this service. It reads the camera through
go2rtc, so the video numbers below describe the stream and nothing depends on
them.
"""

import xml.etree.ElementTree as ElementTree

from soap import TRT, TT, qname, sub

PROFILE_TOKEN = "bedroom_main"
PROFILE_NAME = "bedroom_main"
VIDEO_SOURCE_TOKEN = "video_source"
VIDEO_ENCODER_TOKEN = "video_encoder"
PTZ_CONFIGURATION_TOKEN = "ptz_configuration"
PTZ_NODE_TOKEN = "ptz_node"

CONTINUOUS_PAN_TILT_SPACE = (
    "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
)


def _multicast(parent) -> None:
    """An empty multicast block. The schema requires it and nothing reads it."""
    multicast = sub(parent, TT, "Multicast")
    address = sub(multicast, TT, "Address")
    sub(address, TT, "Type", "IPv4")
    sub(address, TT, "IPv4Address", "0.0.0.0")
    sub(multicast, TT, "Port", 0)
    sub(multicast, TT, "TTL", 1)
    sub(multicast, TT, "AutoStart", "false")


def _video_encoder_configuration(parent, width: int, height: int) -> None:
    encoder = sub(parent, TT, "VideoEncoderConfiguration", token=VIDEO_ENCODER_TOKEN)
    sub(encoder, TT, "Name", "main")
    sub(encoder, TT, "UseCount", 1)
    sub(encoder, TT, "Encoding", "H264")
    resolution = sub(encoder, TT, "Resolution")
    sub(resolution, TT, "Width", width)
    sub(resolution, TT, "Height", height)
    sub(encoder, TT, "Quality", 4)
    rate_control = sub(encoder, TT, "RateControl")
    sub(rate_control, TT, "FrameRateLimit", 10)
    sub(rate_control, TT, "EncodingInterval", 1)
    sub(rate_control, TT, "BitrateLimit", 1024)
    _multicast(encoder)
    sub(encoder, TT, "SessionTimeout", "PT60S")


def ptz_configuration(parent) -> None:
    configuration = sub(
        parent, TT, "PTZConfiguration", token=PTZ_CONFIGURATION_TOKEN
    )
    sub(configuration, TT, "Name", "pan and tilt")
    sub(configuration, TT, "UseCount", 1)
    sub(configuration, TT, "NodeToken", PTZ_NODE_TOKEN)
    # The one capability this camera has. Read the note at the top of this file
    # before you add a second space here.
    sub(
        configuration,
        TT,
        "DefaultContinuousPanTiltVelocitySpace",
        CONTINUOUS_PAN_TILT_SPACE,
    )
    speed = sub(configuration, TT, "DefaultPTZSpeed")
    pan_tilt = sub(speed, TT, "PanTilt")
    pan_tilt.set("x", "0.5")
    pan_tilt.set("y", "0.5")
    pan_tilt.set("space", CONTINUOUS_PAN_TILT_SPACE)
    # The shim's own watchdog is the real limit on a move. This value tells a
    # client what to expect.
    sub(configuration, TT, "DefaultPTZTimeout", "PT8S")


def _profile(parent, width: int, height: int) -> None:
    profile = sub(parent, TRT, "Profiles", token=PROFILE_TOKEN, fixed="true")
    sub(profile, TT, "Name", PROFILE_NAME)
    _video_encoder_configuration(profile, width, height)
    ptz_configuration(profile)


def get_profiles(width: int, height: int):
    response = ElementTree.Element(qname(TRT, "GetProfilesResponse"))
    _profile(response, width, height)

    return response


def get_video_sources(width: int, height: int):
    response = ElementTree.Element(qname(TRT, "GetVideoSourcesResponse"))
    source = sub(response, TRT, "VideoSources", token=VIDEO_SOURCE_TOKEN)
    sub(source, TT, "Framerate", 10)
    resolution = sub(source, TT, "Resolution")
    sub(resolution, TT, "Width", width)
    sub(resolution, TT, "Height", height)

    return response
