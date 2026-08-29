"""Object identifiers used by the presentation and ACSE layers.

Values are BER *content* octets -- what follows tag 0x06 and its length.
"""

ACSE_AS = bytes([0x52, 0x01, 0x00, 0x01])        # 2.2.1.0.1     ACSE abstract syntax
MMS_AS = bytes([0x28, 0xCA, 0x22, 0x02, 0x01])   # 1.0.9506.2.1  MMS abstract syntax
MMS_ACSE = bytes([0x28, 0xCA, 0x22, 0x02, 0x03]) # 1.0.9506.2.3  MMS application context
BER = bytes([0x51, 0x01])                        # 2.1.1         Basic Encoding Rules


def oid_tlv(value: bytes) -> bytes:
    from ..core import ber
    return ber.tlv(0x06, value)
