from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass


TYPE_A = 1
TYPE_CNAME = 5
TYPE_AAAA = 28
CLASS_IN = 1


@dataclass(slots=True)
class DNSQuery:
    message_id: int
    flags: int
    hostname: str
    qtype: int
    qclass: int
    question_bytes: bytes


def parse_query(payload: bytes) -> DNSQuery:
    if len(payload) < 12:
        raise ValueError("payload too small for DNS header")

    message_id, flags, question_count, _, _, _ = struct.unpack("!HHHHHH", payload[:12])
    if question_count < 1:
        raise ValueError("DNS payload does not contain a question")

    offset = 12
    labels: list[str] = []
    while True:
        length = payload[offset]
        offset += 1
        if length == 0:
            break
        label = payload[offset : offset + length].decode("idna")
        labels.append(label)
        offset += length

    qtype, qclass = struct.unpack("!HH", payload[offset : offset + 4])
    question_end = offset + 4
    return DNSQuery(
        message_id=message_id,
        flags=flags,
        hostname=".".join(labels),
        qtype=qtype,
        qclass=qclass,
        question_bytes=payload[12:question_end],
    )


def build_address_response(query: DNSQuery, ip_text: str, ttl: int) -> bytes:
    ip_value = ipaddress.ip_address(ip_text)
    qtype = TYPE_AAAA if ip_value.version == 6 else TYPE_A
    if query.qtype != qtype:
        raise ValueError("query type does not match supplied IP version")

    flags = 0x8000 | 0x0400 | (query.flags & 0x0100) | 0x0080
    header = struct.pack("!HHHHHH", query.message_id, flags, 1, 1, 0, 0)
    answer_name = struct.pack("!H", 0xC00C)
    rdata = ip_value.packed
    answer = answer_name + struct.pack("!HHIH", qtype, CLASS_IN, ttl, len(rdata)) + rdata
    return header + query.question_bytes + answer


def build_nxdomain_response(query: DNSQuery) -> bytes:
    flags = 0x8000 | (query.flags & 0x0100) | 0x0080 | 0x0003
    header = struct.pack("!HHHHHH", query.message_id, flags, 1, 0, 0, 0)
    return header + query.question_bytes