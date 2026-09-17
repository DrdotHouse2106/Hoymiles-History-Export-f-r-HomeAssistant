"""Minimal hand-rolled protobuf wire-format decoder for the Hoymiles S-Miles
Cloud "LineChart" response (used by the count_by_day endpoints).

Message schema (LineChart / LineSeries) reverse-engineered by the
ioBroker.hoymiles project (https://github.com/Eistee82/ioBroker.hoymiles,
MIT License, Copyright (c) 2026 Eistee82) — see its src/lib/proto/Chart.proto.
This module only implements a from-scratch wire-format reader against that
already-published schema; no code was copied from that project.

message LineSeries {
    string type = 1;
    repeated float data = 2 [packed = true];
    int32 did = 3;
    int32 port = 4;
}
message LineChart {
    repeated string x_axis = 1;
    repeated LineSeries series = 2;
    string type = 3;
}
"""
import struct


def read_varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def parse_fields(buf):
    pos = 0
    out = []
    while pos < len(buf):
        tag, pos = read_varint(buf, pos)
        field_no = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            val, pos = read_varint(buf, pos)
        elif wire_type == 2:
            length, pos = read_varint(buf, pos)
            val = buf[pos:pos + length]
            pos += length
        elif wire_type == 5:
            val = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"Unsupported wire type {wire_type} at pos {pos}")
        out.append((field_no, wire_type, val))
    return out


def parse_packed_floats(b):
    n = len(b) // 4
    return list(struct.unpack(f"<{n}f", b))


def parse_line_series(b):
    fields = parse_fields(b)
    series = {"type": None, "data": [], "did": None, "port": None}
    for fno, wt, val in fields:
        if fno == 1:
            series["type"] = val.decode("utf-8")
        elif fno == 2:
            series["data"] = parse_packed_floats(val)
        elif fno == 3:
            series["did"] = val
        elif fno == 4:
            series["port"] = val
    return series


def parse_line_chart(b):
    fields = parse_fields(b)
    chart = {"x_axis": [], "series": [], "type": None}
    for fno, wt, val in fields:
        if fno == 1:
            chart["x_axis"].append(val.decode("utf-8"))
        elif fno == 2:
            chart["series"].append(parse_line_series(val))
        elif fno == 3:
            chart["type"] = val.decode("utf-8")
    return chart
