"""SOAP 1.2 envelope handling for the ONVIF services.

The ONVIF WSDLs that Frigate's client loads are document/literal over SOAP 1.2,
so every response carries the SOAP 1.2 envelope namespace and the
application/soap+xml content type.

This module only builds and parses envelopes. The service modules build the
bodies.
"""

import xml.etree.ElementTree as ElementTree

SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
WSSE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
WSU = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-utility-1.0.xsd"
)

# The ONVIF namespaces. tds, trt and tptz name the three services this shim
# answers. tt is the shared schema that carries every data type.
TDS = "http://www.onvif.org/ver10/device/wsdl"
TRT = "http://www.onvif.org/ver10/media/wsdl"
TPTZ = "http://www.onvif.org/ver20/ptz/wsdl"
TT = "http://www.onvif.org/ver10/schema"
TER = "http://www.onvif.org/ver10/error"

CONTENT_TYPE = "application/soap+xml; charset=utf-8"

# A SOAP request from Frigate is a few kilobytes. Anything much larger is not a
# request this shim serves, and reading it would only cost memory.
MAX_REQUEST_BYTES = 256 * 1024

for prefix, namespace in (
    ("SOAP-ENV", SOAP_ENV),
    ("wsse", WSSE),
    ("wsu", WSU),
    ("tds", TDS),
    ("trt", TRT),
    ("tptz", TPTZ),
    ("tt", TT),
    ("ter", TER),
):
    ElementTree.register_namespace(prefix, namespace)


class MalformedRequest(Exception):
    """The request body is not a SOAP envelope this shim will parse."""


def qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def sub(parent, namespace: str, name: str, text=None, **attrib):
    """Append one child element. Attribute names may carry a namespace."""
    element = ElementTree.SubElement(parent, qname(namespace, name), attrib)
    if text is not None:
        element.text = str(text)

    return element


def parse(body: bytes):
    """Return (action, body_element, header_element) for a SOAP request.

    `action` is the local name of the first child of the SOAP Body, which is
    the ONVIF operation name.
    """
    if len(body) > MAX_REQUEST_BYTES:
        raise MalformedRequest("the request body is too large")

    # ElementTree expands internal entities, so a document type declaration is
    # the way in for an entity expansion attack. This shim never needs one.
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise MalformedRequest("a document type declaration is not accepted")

    try:
        envelope = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise MalformedRequest(f"the body is not XML: {error}") from error

    if envelope.tag != qname(SOAP_ENV, "Envelope"):
        raise MalformedRequest(f"the root element is not a SOAP envelope: {envelope.tag}")

    soap_body = envelope.find(qname(SOAP_ENV, "Body"))
    if soap_body is None or len(soap_body) == 0:
        raise MalformedRequest("the envelope holds no body element")

    operation = soap_body[0]
    action = operation.tag.rsplit("}", 1)[-1]

    return action, operation, envelope.find(qname(SOAP_ENV, "Header"))


def envelope(*body_children) -> bytes:
    """Wrap response elements in a SOAP 1.2 envelope."""
    root = ElementTree.Element(qname(SOAP_ENV, "Envelope"))
    body = ElementTree.SubElement(root, qname(SOAP_ENV, "Body"))
    for child in body_children:
        body.append(child)

    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def fault(reason: str, subcode: str = "ter:ActionNotSupported", sender: bool = True) -> bytes:
    """Build a SOAP 1.2 fault. The ONVIF client raises it as an exception."""
    root = ElementTree.Element(qname(SOAP_ENV, "Envelope"))
    # The subcode is a QName carried as element text. ElementTree only declares
    # a prefix it sees in a tag or an attribute name, never one inside text, so
    # bind ter here by hand. Without this the client fails to parse the fault
    # and raises a parse error instead of the fault itself.
    root.set("xmlns:ter", TER)
    body = ElementTree.SubElement(root, qname(SOAP_ENV, "Body"))
    fault_element = ElementTree.SubElement(body, qname(SOAP_ENV, "Fault"))

    code = ElementTree.SubElement(fault_element, qname(SOAP_ENV, "Code"))
    role = "SOAP-ENV:Receiver"
    if sender:
        role = "SOAP-ENV:Sender"
    sub(code, SOAP_ENV, "Value", role)
    subcode_element = ElementTree.SubElement(code, qname(SOAP_ENV, "Subcode"))
    sub(subcode_element, SOAP_ENV, "Value", subcode)

    reason_element = ElementTree.SubElement(fault_element, qname(SOAP_ENV, "Reason"))
    text = sub(reason_element, SOAP_ENV, "Text", reason)
    text.set("{http://www.w3.org/XML/1998/namespace}lang", "en")

    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def find_text(parent, namespace: str, name: str) -> str:
    """Read one child's text, or an empty string when it is absent."""
    if parent is None:
        return ""

    element = parent.find(qname(namespace, name))
    if element is None or element.text is None:
        return ""

    return element.text
