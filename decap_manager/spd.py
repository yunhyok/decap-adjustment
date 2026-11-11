"""Read SPD placement and pin distances, and safely write selected component changes."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, DecimalException
import hashlib
import json
import math
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


_QUOTED = rb"""(?:"(?:[^"\\\r\n]|\\.)*"|'(?:[^'\\\r\n]|\\.)*')"""
_CONNECT_FIELDS = re.compile(
    _QUOTED + rb"""|(?P<unclosed>['"])|(?P<usage>(?:(?<!\S)|(?<=^\+))Usage\s*=)""", re.I
)
_USAGE_VALUE = re.compile(rb"\s*(" + _QUOTED + rb"""|[^\s'"=]+)(?=\s|$)""")
_STATUS_TO_USAGE = {"Automatic": None, "Enabled": b"0b1000", "Disabled": b"0b111000"}
_USAGE_TO_STATUS = {token: status for status, token in _STATUS_TO_USAGE.items()}
# Statuses save_spd can write to any component; other source values stay on their own line.
WRITABLE_STATUSES = tuple(_STATUS_TO_USAGE)
_ATTRIBUTE = re.compile(rb"^[a-z][a-z0-9_]*\s*=", re.I)
_CONNECT = re.compile(rb"^\.Connect[ \t]+\S+[ \t]+(\S+)", re.I)
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?"
_LENGTH = rf"({_NUMBER})\s*([a-zµμ]+)(?=\s|$)"
_COORDINATE = re.compile(r"\b([XY])\s*=\s*" + _LENGTH, re.I)
_OUTLINE = re.compile(r"\bOutline\s*=\s*" + _LENGTH + r"\s+" + _LENGTH, re.I)
_LAYER = re.compile(rb"\bLayer\s*=\s*(\S+)", re.I)
_EXT_NODES = re.compile(rb"\bExtNode\s*=\s*(.*)", re.I)
_DESCRIPTION = re.compile(r"\*\s*Description\s*[:=]\s*(.+)", re.I)
_SIZE_LABEL = re.compile(r"\*\s*(?:Case\s+Size|Package(?:\s+Size)?|Size(?:\s+Code)?)\s*[:=]\s*(.+)", re.I)
_CAP_LABEL = re.compile(r"\*\s*(?:(?:Nominal\s+)?Capacitance\s*[:=]|(?:Property\s*[:=]\s*)?C\s*=)\s*(.+)", re.I)
_SIZE_CODE = re.compile(r"([0-9]{4}m?)(?:\s*\(\s*([0-9]{4}m?)\s*\))?", re.I)
_CAP_VALUE = re.compile(rf"({_NUMBER})\s*(?:\[\s*([fpnumµμ]?f)\s*\]|([fpnumµμ]?f))", re.I)
_PF = {"ff": "0.001", "pf": "1", "nf": "1000", "uf": "1000000", "mf": "1000000000", "f": "1000000000000"}
_MANUAL_COMMENT = b"* DecapAdjustment TypeMetadata "
_MAX_MANUAL_COMMENT = 4096
_AXES = re.compile(r"\b[XY]\s*=", re.I)
_UM = {"m": "1000000", "cm": "10000", "mm": "1000", "u": "1", "um": "1",
       "µm": "1", "μm": "1", "mil": "25.4", "mils": "25.4", "in": "25400", "inch": "25400"}
_RETURN = re.compile(r"(?:[ADP]?GND|VSS)(?:[/_]\d+)?", re.I)
_SITE_NET = re.compile(r"U[0-9]+_(.+)")
_MAX_LINE = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Pin:
    name: str
    node: bytes
    annotation: bytes
    net: str


@dataclass(slots=True)
class _Component:
    row: dict
    pins: list[_Pin]
    connect_line: int
    usage_line: int | None


@dataclass(frozen=True, slots=True)
class _Node:
    x: float | None
    y: float | None
    net: str
    annotation: bytes
    side: str


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _normalized(raw, number):
    return (raw.removeprefix(b"\xef\xbb\xbf") if number == 1 else raw).lstrip()


def _lines(path, digest=None, *, original=False):
    with path.open("rb", buffering=1024 * 1024) as stream:
        number = 0
        while raw := stream.readline(_MAX_LINE + 1):
            number += 1
            if len(raw) > _MAX_LINE:
                raise ValueError(f"SPD {number}행: 한 줄이 8 MiB 제한을 초과했습니다.")
            if digest is not None:
                digest.update(raw)
            yield number, raw if original else _normalized(raw, number)


def _text(raw, number):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"SPD {number}행: 부품 정보는 UTF-8 텍스트여야 합니다.") from exc


def _usage_span(line, number):
    span = None
    for field in _CONNECT_FIELDS.finditer(line):
        if field["unclosed"]:
            raise ValueError(f"SPD {number}행: .Connect 속성의 따옴표가 닫히지 않았습니다.")
        if not field["usage"]:
            continue
        if span is not None:
            raise ValueError(f"SPD {number}행: Usage 지정이 중복되었습니다.")
        value = _USAGE_VALUE.match(line, field.end())
        if not value or line[:field.start()].rstrip().endswith(b"=") or line[value.end():].lstrip().startswith(b"="):
            raise ValueError(f"SPD {number}행: Usage 값이 없거나 지정 형식이 잘못되었습니다.")
        span = field.start(), value.start(1), value.end()
    return span


def _status_attribute(line, number):
    if span := _usage_span(line, number):
        _, start, end = span
        return _USAGE_TO_STATUS.get(line[start:end], "Unknown")
    return None


def _length(value, unit):
    return float(Decimal(value) * Decimal(_UM[unit.lower()]))


def _size(raw):
    matches = _OUTLINE.findall(raw.decode("utf-8", "replace"))
    if len(matches) == 1:
        x, xunit, y, yunit = matches[0]
        try:
            width, height = _length(x, xunit), _length(y, yunit)
        except (KeyError, DecimalException, OverflowError):
            return None
        if all(math.isfinite(v) and v > 0 for v in (width, height)):
            return width, height
    return None


def _side(raw):
    layers = _LAYER.findall(raw)
    if len(layers) == 1:
        return {b"top": "Top", b"signal$top": "Top", b"plane$top": "Top",
                b"bottom": "Bottom", b"signal$bottom": "Bottom", b"plane$bottom": "Bottom"}.get(layers[0].lower(), "Unknown")
    return "Unknown"


def _comment_metadata(raw, sizes, capacitances):
    line = raw.decode("utf-8", "replace").strip()
    if match := _DESCRIPTION.fullmatch(line):
        size, *cap_fields = match[1].split("/")
    elif match := _SIZE_LABEL.fullmatch(line):
        size, cap_fields = match[1], ()
    elif match := _CAP_LABEL.fullmatch(line):
        size, cap_fields = "", (match[1],)
    else:
        return
    if len(sizes) < 2 and (match := _SIZE_CODE.fullmatch(size.strip())):
        codes = [value.upper() for value in match.groups() if value]
        sizes.add(next((value for value in codes if value.endswith("M")), codes[0]))
    for field in cap_fields:
        if len(capacitances) == 2:
            break
        if not (match := _CAP_VALUE.fullmatch(field.strip())):
            continue
        unit = (match[2] or match[3]).lower().replace("µ", "u").replace("μ", "u")
        try:
            value = float(Decimal(match[1]) * Decimal(_PF[unit]))
        except (DecimalException, OverflowError):
            continue
        if math.isfinite(value) and value > 0:
            capacitances.add(value)


def validate_component_metadata(value):
    if not isinstance(value, dict) or value.keys() != {"size", "capacitance_pf"}:
        raise ValueError("SPD: 수동 부품 정보는 크기와 pF 단위 용량이 필요합니다.")
    size, capacitance = value["size"], value["capacitance_pf"]
    if size is not None and (not isinstance(size, str) or not size.strip() or len(size) > 32 or not size.isprintable()):
        raise ValueError("SPD: 부품 크기는 표시 가능한 32자 이하의 문자열이어야 합니다.")
    if capacitance is not None and (type(capacitance) not in (float, int) or not 0 < capacitance < math.inf):
        raise ValueError("SPD: 정전용량은 0보다 큰 유한한 pF 값이어야 합니다.")
    return dict(size=size.strip() if size else None, capacitance_pf=float(capacitance) if capacitance else None)


def _read_manual_comment(line):
    if len(line) > _MAX_MANUAL_COMMENT:
        return None
    try:
        fields = json.loads(line[len(_MANUAL_COMMENT):])
        if isinstance(fields, dict) and isinstance(component := fields.pop("component", None), str):
            return component, validate_component_metadata(fields)
    except (ValueError, RecursionError):
        pass
    return None


def _geometry(raw, number):
    coordinates = {}
    text = _text(raw, number)
    for match in _COORDINATE.finditer(text):
        axis, value, unit = match.groups()
        if unit.lower() not in _UM:
            raise ValueError(f"SPD {number}행: 좌표 또는 길이 단위가 올바르지 않습니다.")
        try:
            value = _length(value, unit)
        except (DecimalException, OverflowError) as exc:
            raise ValueError(f"SPD {number}행: 좌표가 올바르지 않습니다.") from exc
        axis = axis.upper()
        if axis in coordinates or not math.isfinite(value):
            raise ValueError(f"SPD {number}행: {axis} 좌표가 중복되었거나 유한한 숫자가 아닙니다.")
        coordinates[axis] = value
    if set(coordinates) != {"X", "Y"} or len(_AXES.findall(text)) != 2:
        raise ValueError(f"SPD {number}행: 물리 노드의 X/Y 좌표와 명시적 길이 단위가 필요합니다.")
    return coordinates["X"], coordinates["Y"]


class SpdLayout:
    """Inventory rows retain the source NET; calculation rows select one signal NET.

    Distances are closest XY pin distances in µm, never component centroids.
    Multiple reference instances require site-exclusive NET evidence; physical
    proximity cannot resolve a site. Unknown return names remain signal NETs.
    """

    def __init__(self, components, nodes, ground_nets, parts, models, model_metadata, manual_metadata, source_hash):
        self._components = components
        self._nodes = nodes
        self._ground_nets = ground_nets
        self._parts = parts
        self._models = models
        self._calculation_keys = set()
        self.inventory = [dict(component.row) for component in components]
        self.components = dict(Counter(row["component"] for row in self.inventory))
        self._manual_metadata = manual_metadata
        self.component_metadata = {
            name: dict(manual_metadata[name][1], source="Manual") if name in manual_metadata else
            model_metadata.get(name, dict(size=None, capacitance_pf=None, source=None))
            for name in self.components.keys() | parts.keys() | models.keys()
        }
        self.source_hash = source_hash
        # shortcut: draw the mapped physical pins; parse board outlines when an explicit format is verified.
        self.board_outline = ()

    def geometry(self):
        """Return physical XY (µm), side, pins and optional footprint size/rotation by RefDes."""
        result = {}
        for component in self._components:
            nodes = [self._nodes[pin.node] for pin in component.pins]
            pins = [(node.x, node.y) for node in nodes if node.x is not None]
            sides = {node.side for node in nodes}
            part = self._parts.get(component.row["component"])
            item = dict(x=None, y=None, side=next(iter(sides)) if len(sides) == 1 else "Unknown",
                        pins=pins, size=part[1] if part else None)
            if pins:
                xs, ys = zip(*pins)
                item.update(x=min(xs) / 2 + max(xs) / 2, y=min(ys) / 2 + max(ys) / 2)
            if item["size"] and len(pins) == 2 and pins[0] != pins[1]:
                # The longer body axis is aligned with the two pads.
                (x1, y1), (x2, y2) = pins
                item["angle"] = math.degrees(math.atan2(y2 - y1, x2 - x1)) - (90 if item["size"][1] > item["size"][0] else 0)
            result[component.row["refdes"]] = item
        return result

    def _pins(self, component):
        pins = defaultdict(list)
        for pin in component.pins:
            node = self._nodes.get(pin.node)
            if node is None:
                raise ValueError(f"SPD RefDes {component.row['refdes']} 핀 {pin.name}: 물리 노드 {pin.node.decode('utf-8', 'replace')}가 없습니다.")
            if node.net:
                pins[node.net].append(node)
        return pins

    def records(self, reference_component: str, target_components):
        records = []
        for component, _, net, pins, reference_pins in self._scope(reference_component, target_components):
            distance = min(math.hypot(a.x - b.x, a.y - b.y) for a in pins for b in reference_pins)
            if not math.isfinite(distance):
                raise ValueError(f"SPD RefDes {component.row['refdes']}: 거리가 유한한 숫자가 아닙니다.")
            records.append(dict(component.row, net=net, distance=distance))
        self._calculation_keys.update((row["row"], row["net"], row["distance"]) for row in records)
        return records

    def scope_metadata(self, reference_component: str, target_components):
        """Map targets to the reference RefDes and channel shown alongside the original NET."""
        result = {}
        for component, site, net, _, _ in self._scope(reference_component, target_components):
            match = _SITE_NET.fullmatch(net)
            result[component.row["refdes"]] = dict(site=site, channel=match[1] if match else net)
        return result

    def validate_records(self, records):
        """Reject records whose changes cannot be written to this SPD."""
        _record_edits(self, records)

    def _signals(self, pins):
        return {net for net in pins if net not in self._ground_nets and not _RETURN.fullmatch(net)}

    def _scope(self, reference_component, target_components):
        if not isinstance(reference_component, str) or reference_component not in self.components:
            raise ValueError("SPD: 존재하는 기준 부품의 정확한 이름을 선택하세요.")
        if isinstance(target_components, str):
            raise ValueError("SPD: 대상 부품은 정확한 부품명 목록이어야 합니다.")
        targets = set(target_components)
        if not targets or targets - self.components.keys():
            raise ValueError("SPD: 존재하는 대상 부품 이름을 선택하세요.")
        if reference_component in targets:
            raise ValueError("SPD: 기준 부품과 대상 부품은 달라야 합니다.")
        references = {}
        owners = defaultdict(set)
        for component in self._components:
            if component.row["component"] == reference_component:
                refdes = component.row["refdes"]
                references[refdes] = self._pins(component)
                for net in references[refdes]:
                    owners[net].add(refdes)
        found = False
        for component in self._components:
            if component.row["component"] not in targets:
                continue
            refdes = component.row["refdes"]
            pins = self._pins(component)
            if not pins:
                continue
            exclusive_sites = {next(iter(owners[net])) for net in pins if len(owners.get(net, ())) == 1}
            if len(references) == 1:
                site = next(iter(references))
            elif len(exclusive_sites) != 1:
                raise ValueError(f"SPD RefDes {refdes}: 기준 사이트가 모호합니다. 한 사이트의 전용 NET이 필요합니다. "
                                 f"대상 NET: {', '.join(sorted(pins))}; 기준 후보: {', '.join(sorted(exclusive_sites or references))}")
            else:
                site = next(iter(exclusive_sites))
            common = pins.keys() & references[site].keys()
            signals = self._signals(pins)
            if len(signals) > 1:
                raise ValueError(f"SPD RefDes {refdes}: 신호 NET이 모호하여 거리 행을 지정할 수 없습니다. "
                                 f"NET 후보: {', '.join(sorted(signals))}")
            if signals:
                net = next(iter(signals))
            elif component.row["net"] in common:
                net = component.row["net"]
            elif len(common) == 1:
                net = next(iter(common))
            else:
                raise ValueError(f"SPD RefDes {refdes}: 같은 NET의 기준 핀이 없거나 모호합니다. "
                                 f"기준 RefDes {site}; 대상 NET: {', '.join(sorted(pins))}")
            if net not in common:
                raise ValueError(f"SPD RefDes {refdes}: 기준 RefDes {site}에 대상 NET {net}의 핀이 없어 거리를 계산할 수 없습니다.")
            found = True
            yield component, site, net, pins[net], references[site][net]
        if not found:
            raise ValueError("SPD: 선택한 대상 부품에 NET으로 연결된 물리 핀이 없어 거리를 계산할 수 없습니다. 미연결 부품은 전체 목록에 보존됩니다.")


def read_spd(path) -> SpdLayout:
    """Two bounded binary passes; memory scales with component pins, not file size."""
    path = Path(path)
    before = _signature(path)
    digest = hashlib.sha256()
    components, seen_refs, ground_nets = [], set(), set()
    parts, models = {}, {}
    model_metadata, manual_metadata = {}, {}
    model_notes = None
    definition = None
    active = None
    pin_names = set()
    netlist = False
    for number, line in _lines(path, digest):
        if definition and line.startswith(b"+"):
            kind, name = definition
            if kind == b".part" and (part := parts[name]) is not None:
                attrs = line[1:]
                part[0].update(b"\n" + b" ".join(attrs.split()))
                if (size := _size(attrs)) is not None:
                    parts[name] = part[0], size if part[1] is None else None
            elif kind == b".partialckt" and model_notes is not None:
                attrs = line[1:].lstrip()
                if models[name] is not None:
                    models[name].extend(attrs.split())
                elif ext_nodes := _EXT_NODES.match(attrs):
                    models[name] = ext_nodes[1].split()
            continue
        definition = None
        prefix = line[:12].lower()
        if line.startswith(_MANUAL_COMMENT):
            if parsed := _read_manual_comment(line):
                name, metadata = parsed
                manual_metadata[name] = number, metadata
            continue
        if model_notes is not None:
            if line.startswith(b"*"):
                _comment_metadata(line, model_notes[1], model_notes[2])
                continue
            if prefix.startswith(b".endpartial") and line.split(None, 1)[0].lower() == b".endpartialckt":
                name, sizes, capacitances = model_notes
                size = next(iter(sizes)) if len(sizes) == 1 else None
                capacitance = next(iter(capacitances)) if len(capacitances) == 1 else None
                if size is not None or capacitance is not None:
                    model_metadata[name] = dict(size=size, capacitance_pf=capacitance, source="SPICE comment")
                model_notes = None
        if active is None and not netlist and not prefix.startswith((b".connect", b".endc", b".netlist", b".part")):
            continue
        fields = line.split()
        if not fields or line.startswith(b"*"):
            continue
        directive = fields[0].lower()
        if directive == b".connect":
            if active is not None or len(fields) < 3:
                raise ValueError(f"SPD {number}행: .Connect 형식이 잘못되었거나 .EndC가 없습니다.")
            refdes, part = (_text(field, number) for field in fields[1:3])
            if refdes in seen_refs:
                raise ValueError(f"SPD {number}행: RefDes {refdes}가 중복되었습니다.")
            seen_refs.add(refdes)
            status = _status_attribute(line.split(None, 3)[3], number) if len(fields) > 3 else None
            active = _Component(dict(row=len(components) + 2, refdes=refdes, component=part,
                                     status=status or "Automatic", net="", distance=None), [],
                                number, number if status else None)
            components.append(active)
            pin_names = set()
        elif directive == b".endc":
            if active is None:
                raise ValueError(f"SPD {number}행: 대응하는 .Connect 없이 .EndC가 나타났습니다.")
            active = None
        elif active is not None:
            if directive.startswith(b"."):
                raise ValueError(f"SPD {number}행: 다른 구역 앞에 .EndC가 없습니다.")
            if line.startswith(b"+") or _ATTRIBUTE.match(line) or (len(fields) > 1 and fields[1] == b"="):
                status = _status_attribute(line, number)
                if status is not None:
                    if active.usage_line is not None:
                        raise ValueError(f"SPD {number}행: Usage 지정이 중복되었습니다.")
                    active.row["status"] = status
                    active.usage_line = number
                continue
            if len(fields) != 2 or b".node" not in fields[1].lower():
                raise ValueError(f"SPD {number}행: 패키지 핀 매핑 형식이 잘못되었습니다.")
            pin = _text(fields[0], number)
            if pin in pin_names:
                raise ValueError(f"SPD {number}행: RefDes {active.row['refdes']}의 핀 {pin} 매핑이 중복되었습니다.")
            pin_names.add(pin)
            token, _, net_raw = fields[1].partition(b"::")
            node, _, annotation = token.partition(b"!!")
            net = _text(net_raw, number)
            active.pins.append(_Pin(pin, node, annotation, net))
            if net and not active.row["net"]:
                active.row["net"] = net
        elif directive == b".netlist":
            if netlist:
                raise ValueError(f"SPD {number}행: .NetList가 중첩되었습니다.")
            netlist = True
        elif directive == b".endnetlist":
            netlist = False
        elif netlist:
            # Only explicit membership is evidence; ordinary following entries may be ungrouped.
            if len(fields) >= 3 and fields[1] == b"->" and fields[2].split(b"::", 1)[0].split(b"||", 1)[0].lower() == b"groundnets":
                ground_nets.add(_text(fields[0].split(b"::", 1)[0].split(b"||", 1)[0], number))
        elif directive in (b".part", b".partialckt") and len(fields) >= 2:
            name = _text(fields[1], number)
            attrs = line.split(None, 2)[2] if len(fields) > 2 else b""
            if directive == b".part":
                parts[name] = (hashlib.sha256(b" ".join(attrs.split())), _size(attrs)) if name not in parts else None
            else:
                ext_nodes = _EXT_NODES.search(attrs)
                if model_notes is not None:
                    models[model_notes[0]] = None
                model_metadata.pop(name, None)
                model_notes = (name, set(), set()) if name not in models else None
                models[name] = ext_nodes[1].split() if ext_nodes and name not in models else None
            definition = directive, name
    if model_notes is not None:
        models[model_notes[0]] = None
    if active is not None or netlist:
        raise ValueError("SPD: .Connect/.NetList 구역의 종료 표시가 없습니다.")
    if not components:
        raise ValueError("SPD: .Connect 부품 목록이 없습니다.")
    wanted = {pin.node for component in components for pin in component.pins}
    nodes = {}
    package = None
    second_digest = hashlib.sha256()
    for number, line in _lines(path, second_digest):
        if line.startswith(b"."):
            fields = line.split()
            directive = fields[0].lower()
            if directive == b".package":
                if package is not None or len(fields) < 2:
                    raise ValueError(f"SPD {number}행: .Package 형식이 잘못되었거나 중첩되었습니다.")
                package = fields[1]
            elif directive == b".endpackage":
                if package is None:
                    raise ValueError(f"SPD {number}행: 대응하는 .Package 없이 .EndPackage가 나타났습니다.")
                package = None
            continue
        if package is None or line[:4].lower() != b"node":
            continue
        token = line.split(None, 1)[0]
        identity, _, raw_net = token.partition(b"::")
        local_id, _, annotation = identity.partition(b"!!")
        key = package + b"." + local_id
        if key not in wanted:
            continue
        net = _text(raw_net, number)
        try:
            x, y = _geometry(line, number)
        except ValueError:
            if net:
                raise
            x, y = None, None
        node = _Node(x, y, net, annotation, _side(line))
        if key in nodes and nodes[key] != node:
            raise ValueError(f"SPD {number}행: 물리 노드 {key.decode('utf-8', 'replace')} 정의가 충돌합니다.")
        nodes[key] = node
    if package is not None:
        raise ValueError("SPD: .Package 구역의 종료 표시가 없습니다.")
    if before != _signature(path) or digest.digest() != second_digest.digest():
        raise ValueError("SPD: 읽는 동안 파일이 변경되었습니다. 저장 후 다시 불러오세요.")
    for component in components:
        for pin in component.pins:
            node = nodes.get(pin.node)
            if node is not None and ((pin.net and pin.net != node.net) or
                                     (pin.annotation and node.annotation and pin.annotation != node.annotation)):
                raise ValueError(f"SPD RefDes {component.row['refdes']} 핀 {pin.name}: 노드 NET 또는 핀 주석이 충돌합니다. "
                                 f"매핑 NET {pin.net!r}; 물리 NET {node.net!r}")
            if node is None:
                raise ValueError(f"SPD RefDes {component.row['refdes']} 핀 {pin.name}: NET {pin.net!r}의 물리 노드 "
                                 f"{pin.node.decode('utf-8', 'replace')}가 없습니다.")
    return SpdLayout(components, nodes, ground_nets, parts, models, model_metadata, manual_metadata, digest.hexdigest())


def _replace_connect_fields(raw, number, part, status):
    line = _normalized(raw, number)
    header = _CONNECT.match(line)
    offset = len(raw) - len(line)
    if header:
        offset += header.end()
    head, attrs = raw[:offset], raw[offset:]
    if part is not None:
        if not header:
            raise ValueError(f"SPD {number}행: 저장할 .Connect 부품 필드가 없습니다.")
        head = head[:-len(header[1])] + part.encode("utf-8")
    if status is not None:
        usage = _usage_span(attrs, number)
        token = _STATUS_TO_USAGE[status]
        if usage:
            start, value, end = usage
            attrs = attrs[:start] + attrs[end:] if token is None else attrs[:value] + token + attrs[end:]
            if token is None and not header and attrs.strip() == b"+":
                attrs = attrs.replace(b"+", b"", 1)
        elif header and token is not None:
            end = len(attrs.rstrip(b" \t\r\n"))
            attrs = attrs[:end] + b" Usage = " + token + attrs[end:]
        else:
            raise ValueError(f"SPD {number}행: 저장할 Usage 필드를 찾을 수 없습니다.")
    return head + attrs


def _component_compatible(layout, component, part, mappings):
    old = component.row["component"]
    old_part, new_part = layout._parts.get(old), layout._parts.get(part)
    if not (old_part and new_part and old_part[1] and old_part[0].digest() == new_part[0].digest()):
        return False
    pins = frozenset(pin.name for pin in component.pins)
    if old in layout._models or part in layout._models:
        old_nodes, new_nodes = layout._models.get(old), layout._models.get(part)
        return bool(old_nodes and old_nodes == new_nodes and len(old_nodes) == len(pins) and
                    set(old_nodes) == {pin.encode("utf-8") for pin in pins})
    return bool(pins and mappings[part] == {pins})


def _record_edits(layout, records):
    by_ref = {component.row["refdes"]: component for component in layout._components}
    mappings = defaultdict(set)
    for component in layout._components:
        name = component.row["component"]
        if name not in layout._models:
            mappings[name].add(frozenset(pin.name for pin in component.pins))
    seen, updates, edits = set(), {}, {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("SPD: 부품 변경은 RefDes별 행으로 입력해야 합니다.")
        refdes = record.get("refdes")
        component = by_ref.get(refdes) if isinstance(refdes, str) else None
        if component is None or refdes in seen:
            raise ValueError(f"SPD: RefDes {refdes!r}가 원본에 없거나 중복되었습니다.")
        seen.add(refdes)
        original = component.row
        row, net, distance = record.get("row"), record.get("net"), record.get("distance")
        valid_net = isinstance(net, str) and (net == original["net"] if distance is None else
                    type(distance) in (int, float) and (original["row"], net, distance) in layout._calculation_keys)
        if type(row) is not int or row != original["row"] or not valid_net:
            raise ValueError(f"SPD RefDes {refdes}: 원본 행 또는 NET이 일치하지 않습니다.")
        part, status = record.get("component"), record.get("status")
        if not isinstance(status, str) or (status != original["status"] and status not in _STATUS_TO_USAGE):
            raise ValueError(f"SPD RefDes {refdes}: 저장할 수 없는 상태 {status!r}입니다.")
        if not isinstance(part, str) or (part != original["component"] and not _component_compatible(layout, component, part, mappings)):
            raise ValueError(f"SPD RefDes {refdes}: 대체 부품 {part!r}의 .Part 형상 또는 모델 핀이 원본과 호환되지 않습니다.")
        new_part = part if part != original["component"] else None
        new_status = status if status != original["status"] else None
        if new_part is None and new_status is None:
            continue
        updates[refdes] = dict(original, component=part, status=status)
        usage_line = component.usage_line or component.connect_line
        if usage_line == component.connect_line:
            edits[component.connect_line] = (new_part, new_status)
        else:
            if new_part is not None:
                edits[component.connect_line] = (new_part, None)
            if new_status is not None:
                edits[usage_line] = (None, new_status)
    return updates, edits


def save_spd(source_path, layout: SpdLayout, records, destination, *, component_metadata=None) -> Path:
    """Publish a verified new SPD with selected .Connect changes and explicit metadata comments."""
    source, destination = Path(source_path), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("SPD: 원본 파일에는 저장할 수 없습니다. 새 파일을 선택하세요.")
    if os.path.lexists(destination):
        raise FileExistsError(f"SPD: 저장할 파일이 이미 있습니다. {destination}")
    updates, edits = _record_edits(layout, records)
    if component_metadata is not None and not isinstance(component_metadata, dict):
        raise ValueError("SPD: 수동 부품 정보는 부품 종류별로 입력해야 합니다.")
    manual, manual_edits, appended_comments = {}, {}, []
    for name, fields in (component_metadata or {}).items():
        if not isinstance(name, str) or name not in layout.component_metadata:
            raise ValueError(f"SPD: 수동 정보를 지정할 부품 종류 {name!r}가 원본에 없습니다.")
        fields = validate_component_metadata(fields)
        comment = _MANUAL_COMMENT + json.dumps(dict(component=name, **fields), separators=(",", ":")).encode("ascii")
        if len(comment) + 2 > _MAX_MANUAL_COMMENT:
            raise ValueError(f"SPD: {name}의 수동 부품 정보가 너무 깁니다.")
        manual[name] = dict(fields, source="Manual")
        if name in layout._manual_metadata:
            number, previous = layout._manual_metadata[name]
            if previous != fields:
                manual_edits[number] = comment
        else:
            appended_comments.append(comment)

    before = _signature(source)
    stale = "SPD: 불러온 뒤 원본 파일이 변경되었습니다. 다시 불러오세요."
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != layout.source_hash or before != _signature(source):
            raise ValueError(stale)

    temporary = None
    try:
        digest, output_digest = hashlib.sha256(), hashlib.sha256()
        with NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=".spd-save-", suffix=".tmp",
                                buffering=1024 * 1024, delete=False) as stream:
            temporary = Path(stream.name)
            newline = b"\n"
            for number, raw in _lines(source, digest, original=True):
                if number == 1 and raw.endswith(b"\r\n"):
                    newline = b"\r\n"
                if (edit := edits.get(number)) is not None:
                    raw = _replace_connect_fields(raw, number, *edit)
                elif (comment := manual_edits.get(number)) is not None:
                    raw = raw[:len(raw) - len(_normalized(raw, number))] + comment + raw[len(raw.rstrip(b"\r\n")):]
                output_digest.update(raw)
                stream.write(raw)
            if digest.hexdigest() != layout.source_hash or before != _signature(source):
                raise ValueError(stale)
            if appended_comments:
                comments = (b"" if raw.endswith(b"\n") else newline) + newline.join(appended_comments) + newline
                output_digest.update(comments)
                stream.write(comments)
            stream.flush()
            os.fsync(stream.fileno())
        verified = read_spd(temporary)
        expected = [updates.get(component.row["refdes"], component.row) for component in layout._components]
        if (verified.source_hash != output_digest.hexdigest() or verified.inventory != expected or
                any(verified.component_metadata.get(name) != fields for name, fields in manual.items())):
            raise ValueError("SPD: 저장 결과를 다시 읽어 검증하지 못했습니다.")
        if before != _signature(source):
            raise ValueError(stale)
        if os.name == "nt":
            temporary.rename(destination)
        else:
            os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
