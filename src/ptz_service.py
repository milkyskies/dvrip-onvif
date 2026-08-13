"""The ONVIF PTZ service.

Frigate's arrow buttons produce exactly two calls. A press sends ContinuousMove
with a pan and tilt velocity, and a release sends Stop. Everything else in this
module exists so that a client can ask what the camera can do and get a true
answer.

**Every operation that could move the camera without a press is refused here.**
GotoPreset, SetPreset, GotoHomePosition, AbsoluteMove and RelativeMove all
return a SOAP fault. GotoPreset is not merely unsupported: it made this camera
move and not stop when it was tested on 2026-08-13, and only an explicit
direction stop halted it. A fault is the guarantee that no client can reach it
through this shim.

GetStatus is refused for a different reason. The camera reports no pan or tilt
position at all, so any position this shim returned would be invented. Frigate's
autotracking needs GetStatus, and autotracking on this camera cannot work.
Failing loudly is the honest answer.
"""

import xml.etree.ElementTree as ElementTree

from media_service import (
    CONTINUOUS_PAN_TILT_SPACE,
    PTZ_CONFIGURATION_TOKEN,
    PTZ_NODE_TOKEN,
    ptz_configuration,
)
from ptzcore import MAX_STEP
from soap import TPTZ, TT, qname, sub

# ONVIF velocity runs from -1 to 1 on each axis. Below this the request is a
# stop dressed as a move, so treat it as one.
MINIMUM_VELOCITY = 0.05


def direction_for(pan: float, tilt: float) -> str:
    """Turn an ONVIF pan and tilt velocity into a dvrip direction name.

    Returns an empty string when neither axis carries enough velocity to move.
    """
    horizontal = ""
    if pan >= MINIMUM_VELOCITY:
        horizontal = "right"
    elif pan <= -MINIMUM_VELOCITY:
        horizontal = "left"

    vertical = ""
    if tilt >= MINIMUM_VELOCITY:
        vertical = "up"
    elif tilt <= -MINIMUM_VELOCITY:
        vertical = "down"

    return vertical + horizontal


def step_for(pan: float, tilt: float) -> int:
    """Turn an ONVIF velocity into a dvrip motor speed of 1 to 8.

    The fastest axis sets the speed, so a diagonal moves at the speed the
    client asked for rather than at the slower of the two.
    """
    magnitude = max(abs(pan), abs(tilt))

    return max(1, min(MAX_STEP, round(magnitude * MAX_STEP)))


def continuous_move_response():
    return ElementTree.Element(qname(TPTZ, "ContinuousMoveResponse"))


def stop_response():
    return ElementTree.Element(qname(TPTZ, "StopResponse"))


def get_presets():
    """An empty preset list.

    This shim stores no presets and refuses GotoPreset, so reporting none is
    the truth. Read the note at the top of this file.
    """
    return ElementTree.Element(qname(TPTZ, "GetPresetsResponse"))


def get_service_capabilities():
    response = ElementTree.Element(qname(TPTZ, "GetServiceCapabilitiesResponse"))
    capabilities = sub(response, TPTZ, "Capabilities")
    capabilities.set("EFlip", "false")
    capabilities.set("Reverse", "false")
    capabilities.set("GetCompatibleConfigurations", "false")
    # The camera reports no position and no move state. Frigate reads
    # MoveStatus to decide whether autotracking can work, so this must say no.
    capabilities.set("MoveStatus", "false")
    capabilities.set("StatusPosition", "false")

    return response


def get_configurations():
    response = ElementTree.Element(qname(TPTZ, "GetConfigurationsResponse"))
    ptz_configuration(response)

    return response


def get_configuration_options():
    """Report the one motion space this camera has.

    The spaces list holds continuous pan and tilt and nothing else, for the
    same reason the media profile does. See media_service.py.
    """
    response = ElementTree.Element(qname(TPTZ, "GetConfigurationOptionsResponse"))
    options = sub(response, TPTZ, "PTZConfigurationOptions")

    spaces = sub(options, TT, "Spaces")
    velocity = sub(spaces, TT, "ContinuousPanTiltVelocitySpace")
    sub(velocity, TT, "URI", CONTINUOUS_PAN_TILT_SPACE)
    x_range = sub(velocity, TT, "XRange")
    sub(x_range, TT, "Min", "-1.0")
    sub(x_range, TT, "Max", "1.0")
    y_range = sub(velocity, TT, "YRange")
    sub(y_range, TT, "Min", "-1.0")
    sub(y_range, TT, "Max", "1.0")

    timeout = sub(options, TT, "PTZTimeout")
    sub(timeout, TT, "Min", "PT0S")
    sub(timeout, TT, "Max", "PT8S")

    return response


def get_nodes():
    response = ElementTree.Element(qname(TPTZ, "GetNodesResponse"))
    _node(response)

    return response


def get_node():
    response = ElementTree.Element(qname(TPTZ, "GetNodeResponse"))
    _node(response)

    return response


def _node(parent) -> None:
    node = sub(parent, TPTZ, "PTZNode", token=PTZ_NODE_TOKEN)
    node.set("FixedHomePosition", "false")
    sub(node, TT, "Name", PTZ_CONFIGURATION_TOKEN)

    spaces = sub(node, TT, "SupportedPTZSpaces")
    velocity = sub(spaces, TT, "ContinuousPanTiltVelocitySpace")
    sub(velocity, TT, "URI", CONTINUOUS_PAN_TILT_SPACE)
    x_range = sub(velocity, TT, "XRange")
    sub(x_range, TT, "Min", "-1.0")
    sub(x_range, TT, "Max", "1.0")
    y_range = sub(velocity, TT, "YRange")
    sub(y_range, TT, "Min", "-1.0")
    sub(y_range, TT, "Max", "1.0")

    # No presets, and no home position. Both would move the camera on a single
    # command, and this camera does not stop reliably when it is told to.
    sub(node, TT, "MaximumNumberOfPresets", 0)
    sub(node, TT, "HomeSupported", "false")
