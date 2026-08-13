"""The ONVIF device service.

This is the entry point. Frigate's client calls GetCapabilities first and reads
the address of every other service out of the answer.

**What this answer leaves out is the point of it.** It names the device, media
and PTZ services and nothing else. There is no imaging service, so Frigate finds
no focus control and shows no focus buttons. The camera has no focus, no zoom
and no iris: it answers OK to all three and does nothing, so advertising them
would put buttons in the UI that quietly do nothing.
"""

import datetime
import xml.etree.ElementTree as ElementTree

from soap import TDS, TT, qname, sub

MANUFACTURER = "milkyskies"
MODEL = "dvrip-onvif"
FIRMWARE = "1.0"
SERIAL = "bedroom"
HARDWARE = "iCSee CACAGOO S2 Pro over dvrip"

DEVICE_PATH = "/onvif/device_service"
MEDIA_PATH = "/onvif/media_service"
PTZ_PATH = "/onvif/ptz_service"


def get_capabilities(base_url: str):
    response = ElementTree.Element(qname(TDS, "GetCapabilitiesResponse"))
    capabilities = sub(response, TDS, "Capabilities")

    device = sub(capabilities, TT, "Device")
    sub(device, TT, "XAddr", base_url + DEVICE_PATH)
    network = sub(device, TT, "Network")
    sub(network, TT, "IPFilter", "false")
    sub(network, TT, "ZeroConfiguration", "false")
    sub(network, TT, "IPVersion6", "false")
    sub(network, TT, "DynDNS", "false")
    system = sub(device, TT, "System")
    sub(system, TT, "DiscoveryResolve", "false")
    sub(system, TT, "DiscoveryBye", "false")
    sub(system, TT, "RemoteDiscovery", "false")
    sub(system, TT, "SystemBackup", "false")
    sub(system, TT, "SystemLogging", "false")
    sub(system, TT, "FirmwareUpgrade", "false")
    version = sub(system, TT, "SupportedVersions")
    sub(version, TT, "Major", "2")
    sub(version, TT, "Minor", "40")

    media = sub(capabilities, TT, "Media")
    sub(media, TT, "XAddr", base_url + MEDIA_PATH)
    streaming = sub(media, TT, "StreamingCapabilities")
    sub(streaming, TT, "RTPMulticast", "false")
    sub(streaming, TT, "RTP_TCP", "false")
    sub(streaming, TT, "RTP_RTSP_TCP", "false")

    ptz = sub(capabilities, TT, "PTZ")
    sub(ptz, TT, "XAddr", base_url + PTZ_PATH)

    return response


def get_device_information():
    response = ElementTree.Element(qname(TDS, "GetDeviceInformationResponse"))
    sub(response, TDS, "Manufacturer", MANUFACTURER)
    sub(response, TDS, "Model", MODEL)
    sub(response, TDS, "FirmwareVersion", FIRMWARE)
    sub(response, TDS, "SerialNumber", SERIAL)
    sub(response, TDS, "HardwareId", HARDWARE)

    return response


def get_services(base_url: str):
    response = ElementTree.Element(qname(TDS, "GetServicesResponse"))
    for namespace, path in (
        ("http://www.onvif.org/ver10/device/wsdl", DEVICE_PATH),
        ("http://www.onvif.org/ver10/media/wsdl", MEDIA_PATH),
        ("http://www.onvif.org/ver20/ptz/wsdl", PTZ_PATH),
    ):
        service = sub(response, TDS, "Service")
        sub(service, TDS, "Namespace", namespace)
        sub(service, TDS, "XAddr", base_url + path)
        version = sub(service, TDS, "Version")
        sub(version, TT, "Major", "2")
        sub(version, TT, "Minor", "40")

    return response


def get_system_date_and_time():
    """Report the clock in UTC.

    A client that signs with a password digest compares its own clock with this
    one, so an honest answer keeps a digest from being read as stale.
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    response = ElementTree.Element(qname(TDS, "GetSystemDateAndTimeResponse"))
    system_date = sub(response, TDS, "SystemDateAndTime")
    sub(system_date, TT, "DateTimeType", "NTP")
    sub(system_date, TT, "DaylightSavings", "false")
    time_zone = sub(system_date, TT, "TimeZone")
    sub(time_zone, TT, "TZ", "UTC0")

    utc = sub(system_date, TT, "UTCDateTime")
    time_element = sub(utc, TT, "Time")
    sub(time_element, TT, "Hour", now.hour)
    sub(time_element, TT, "Minute", now.minute)
    sub(time_element, TT, "Second", now.second)
    date_element = sub(utc, TT, "Date")
    sub(date_element, TT, "Year", now.year)
    sub(date_element, TT, "Month", now.month)
    sub(date_element, TT, "Day", now.day)

    return response
