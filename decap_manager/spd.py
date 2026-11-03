"""Read component inventory and package pin distances without retaining board meshes."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re


_USAGE = re.compile(rb"\bUsage\s*=\s*(\S+)", re.I)
_USAGE_ASSIGNMENT = re.compile(rb"\bUsage\s*=", re.I)
_COORDINATE = re.compile(r"\b([XY])\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*([a-zµμ]+)(?=\s|$)", re.I)
_AXES = re.compile(r"\b[XY]\s*=", re.I)
_UM = {"m": "1000000", "cm": "10000", "mm": "1000", "u": "1", "um": "1",
       "µm": "1", "μm": "1", "mil": "25.4", "mils": "25.4", "in": "25400", "inch": "25400"}
_RETURN = re.compile(r"(?:[ADP]?GND|VSS)(?:[/_]\d+)?", re.I)
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


@dataclass(frozen=True, slots=True)
class _Node:
    x: float | None
    y: float | None
    net: str
    annotation: bytes


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _lines(path, digest=None):
    with path.open("rb", buffering=1024 * 1024) as stream:
        number = 0
        while raw := stream.readline(_MAX_LINE + 1):
            number += 1
            if len(raw) > _MAX_LINE:
                raise ValueError(f"SPD {number}행: 한 줄이 8 MiB 제한을 초과했습니다.")
            if digest is not None:
                digest.update(raw)
            yield number, (raw.removeprefix(b"\xef\xbb\xbf") if number == 1 else raw).lstrip()


def _text(raw, number):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"SPD {number}행: 부품 정보는 UTF-8 텍스트여야 합니다.") from exc


def _status_attribute(line, number):
    assignments = _USAGE_ASSIGNMENT.findall(line)
    if not assignments:
        return None
    if len(assignments) > 1:
        raise ValueError(f"SPD {number}행: Usage 지정이 중복되었습니다.")
    usage = _USAGE.search(line)
    if not usage or b"=" in usage[1] or line[usage.end():].lstrip().startswith(b"="):
        raise ValueError(f"SPD {number}행: Usage 값이 없거나 지정 형식이 잘못되었습니다.")
    return {b"0b1000": "Enabled", b"0b111000": "Disabled"}.get(usage[1], "Unknown")


def _geometry(raw, number):
    coordinates = {}
    text = _text(raw, number)
    for match in _COORDINATE.finditer(text):
        axis, value, unit = match.groups()
        if unit.lower() not in _UM:
            raise ValueError(f"SPD {number}행: 좌표 또는 길이 단위가 올바르지 않습니다.")
        try:
            value = float(Decimal(value) * Decimal(_UM[unit.lower()]))
        except (InvalidOperation, OverflowError) as exc:
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

    def __init__(self, components, nodes, ground_nets, source_hash):
        self._components = components
        self._nodes = nodes
        self._ground_nets = ground_nets
        self.inventory = [dict(component.row) for component in components]
        self.components = dict(Counter(row["component"] for row in self.inventory))
        self.source_hash = source_hash

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
        records = []
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
            signals = {net for net in pins if net not in self._ground_nets and not _RETURN.fullmatch(net)}
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
            distance = min(math.hypot(a.x - b.x, a.y - b.y)
                           for a in pins[net] for b in references[site][net])
            if not math.isfinite(distance):
                raise ValueError(f"SPD RefDes {refdes}: 거리가 유한한 숫자가 아닙니다.")
            records.append(dict(component.row, net=net, distance=distance))
        if not records:
            raise ValueError("SPD: 선택한 대상 부품에 NET으로 연결된 물리 핀이 없어 거리를 계산할 수 없습니다. 미연결 부품은 전체 목록에 보존됩니다.")
        return records


def read_spd(path) -> SpdLayout:
    """Two bounded binary passes; memory scales with component pins, not file size."""
    path = Path(path)
    before = _signature(path)
    digest = hashlib.sha256()
    components, seen_refs, ground_nets = [], set(), set()
    active = None
    pin_names = set()
    netlist = False
    for number, line in _lines(path, digest):
        prefix = line[:12].lower()
        if active is None and not netlist and not prefix.startswith((b".connect", b".endc", b".netlist")):
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
            status = _status_attribute(line, number) or "Automatic"
            active = _Component(dict(row=len(components) + 2, refdes=refdes, component=part,
                                     status=status, net="", distance=None), [])
            components.append(active)
            pin_names = set()
        elif directive == b".endc":
            if active is None:
                raise ValueError(f"SPD {number}행: 대응하는 .Connect 없이 .EndC가 나타났습니다.")
            active = None
        elif active is not None:
            if directive.startswith(b"."):
                raise ValueError(f"SPD {number}행: 다른 구역 앞에 .EndC가 없습니다.")
            if line.startswith(b"+") or directive.startswith(b"usage=") or (len(fields) > 1 and fields[1] == b"="):
                status = _status_attribute(line, number)
                if status is not None:
                    if active.row["status"] != "Automatic":
                        raise ValueError(f"SPD {number}행: Usage 지정이 중복되었습니다.")
                    active.row["status"] = status
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
    if active is not None or netlist:
        raise ValueError("SPD: .Connect/.NetList 구역의 종료 표시가 없습니다.")
    if not components:
        raise ValueError("SPD: .Connect 부품 목록이 없습니다.")
    wanted = {pin.node for component in components for pin in component.pins}
    nodes = {}
    package = None
    for number, line in _lines(path):
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
        x, y = _geometry(line, number) if net else (None, None)
        node = _Node(x, y, net, annotation)
        if key in nodes and nodes[key] != node:
            raise ValueError(f"SPD {number}행: 물리 노드 {key.decode('utf-8', 'replace')} 정의가 충돌합니다.")
        nodes[key] = node
    if package is not None:
        raise ValueError("SPD: .Package 구역의 종료 표시가 없습니다.")
    if before != _signature(path):
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
    return SpdLayout(components, nodes, ground_nets, digest.hexdigest())
