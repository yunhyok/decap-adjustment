"""Excel command discovery, validation, and source fingerprinting."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re

from openpyxl import load_workbook


_ALIASES = {
    "net": {"net", "netname", "네트", "넷"},
    "component": {"component", "componenttype", "componentname", "part", "partname", "부품", "부품명"},
    "channel": {"channel", "채널"},
    "site": {"site", "사이트"},
    "expression": {"expression", "command", "adjustment", "change", "명령", "증감", "증감명령"},
}
_EXPRESSION = re.compile(r"[AD]\s*[+-]\s*[1-9]\d*(?:\s*,\s*[AD]\s*[+-]\s*[1-9]\d*)*", re.I)


def _blank(value):
    return value is None or isinstance(value, str) and not value.strip()


def _header(value):
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if re.match(r"^channel\s*(/|\[)", text):
        return "channel"
    text = re.sub(r"[\s_\-\[\]()/]", "", text)
    return next((name for name, aliases in _ALIASES.items() if text in aliases), None)


def _component_header(value):
    return isinstance(value, str) and bool(re.search(r"(^cap(?:_|$)|(?:\d[punmµμ](?:\d+)?f)(?:_|$))", value.strip(), re.I))


def inspect_workbook(path, kind="commands"):
    """Return candidate command mappings; callers choose when sheets are ambiguous."""
    if kind != "commands":
        raise ValueError(f"지원하지 않는 파일 종류: {kind}. 명령 파일만 지원합니다.")
    candidates = []
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        for sheet in workbook:
            for row_number, row in enumerate(sheet.iter_rows(), 1):
                columns, duplicates, components = {}, [], {}
                for cell in row:
                    name = _header(cell.value)
                    if name:
                        if name in columns:
                            duplicates.append(name)
                        columns[name] = cell.column
                    elif _component_header(cell.value):
                        if cell.value in components:
                            duplicates.append(str(cell.value))
                        components[cell.value] = cell.column
                tall = {"component", "expression"}.issubset(columns)
                identity = "net" in columns or {"channel", "site"}.issubset(columns)
                if not identity or not (tall or components):
                    continue
                if duplicates:
                    raise ValueError(f"{sheet.title} {row_number}행: 중복/모호한 헤더 {', '.join(duplicates)}. 열을 직접 지정하세요.")
                mapping = {"sheet": sheet.title, "header_row": row_number, "columns": columns}
                if not tall:
                    mapping["component_columns"] = components
                candidates.append(mapping)
    finally:
        workbook.close()
    return sorted(candidates, key=lambda item: (item["header_row"], -len(item["columns"])))


def _validate_mapping(workbook, mapping):
    if mapping.get("sheet") not in workbook.sheetnames:
        raise ValueError("선택한 시트를 찾을 수 없습니다.")
    sheet = workbook[mapping["sheet"]]
    if sheet.max_row is None or sheet.max_column is None:
        sheet.calculate_dimension(force=True)
    header = mapping.get("header_row")
    columns = mapping.get("columns", {})
    if isinstance(header, bool) or not isinstance(header, int) or not 1 <= header <= sheet.max_row:
        raise ValueError("헤더 행은 시트 안의 1 이상 정수여야 합니다.")
    if set(columns) - set(_ALIASES):
        raise ValueError("지원하지 않는 열 이름이 있습니다.")
    component_columns = mapping.get("component_columns", {})
    values = list(columns.values()) + list(component_columns.values())
    if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= sheet.max_column for value in values):
        raise ValueError("열 번호는 시트 안의 1 이상 정수여야 합니다.")
    if len(values) != len(set(values)):
        raise ValueError("하나의 열을 여러 항목에 중복 지정할 수 없습니다.")
    if not ("net" in columns or {"channel", "site"}.issubset(columns)):
        raise ValueError("NET 또는 Channel/SITE 열을 지정하세요.")
    tall = {"component", "expression"}.issubset(columns)
    if tall == bool(component_columns):
        raise ValueError("부품/명령 열 또는 부품별 명령 열 중 한 가지 형식을 지정하세요.")
    for name in component_columns:
        _text(name, "부품 열 이름")
    return sheet


def _text(value, context, allow_empty=False):
    if _blank(value):
        if allow_empty:
            return ""
        raise ValueError(f"{context}: 값이 비어 있습니다.")
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"{context}: 앞뒤 공백 없는 텍스트가 필요합니다.")
    return value


def _value(row, columns, key):
    index = columns.get(key)
    return row[index - 1].value if index else None


def _reject_formulas(row, indexes, context):
    if any(row[index - 1].data_type == "f" for index in indexes):
        raise ValueError(f"{context}: 입력 열에 수식이 있습니다. 계산된 값을 별도 파일에 저장하여 사용하세요.")


def _command_net(row, mapping, context):
    columns = mapping["columns"]
    if "net" in columns:
        return _text(_value(row, columns, "net"), f"{context} NET")
    channel = _text(_value(row, columns, "channel"), f"{context} Channel")
    site = _value(row, columns, "site")
    if isinstance(site, (int, float)) and not isinstance(site, bool) and math.isfinite(site) and site >= 0 and int(site) == site:
        site = str(int(site))
    else:
        site = _text(site, f"{context} SITE")
    template = mapping.get("net_template", "U{site}_{channel}")
    if not isinstance(template, str) or "{site}" not in template or "{channel}" not in template:
        raise ValueError("NET 템플릿에는 {site}와 {channel}을 모두 포함해야 합니다.")
    remaining = template.replace("{site}", "").replace("{channel}", "")
    if "{" in remaining or "}" in remaining:
        raise ValueError("NET 템플릿에는 {site}, {channel}만 사용할 수 있습니다.")
    return _text(template.replace("{site}", site).replace("{channel}", channel), f"{context} NET")


def read_commands(path, mapping):
    commands = []
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        sheet = _validate_mapping(workbook, mapping)
        columns = mapping["columns"]
        for row_number, row in enumerate(sheet.iter_rows(min_row=mapping["header_row"] + 1), mapping["header_row"] + 1):
            context = f"{sheet.title} {row_number}행"
            if mapping.get("component_columns"):
                cells = [(component, row[index - 1]) for component, index in sorted(mapping["component_columns"].items(), key=lambda item: item[1])]
            else:
                cells = [(_value(row, columns, "component"), row[columns["expression"] - 1])]
            pending = [(component, cell) for component, cell in cells if not _blank(cell.value)]
            if not pending:
                continue
            _reject_formulas(row, list(columns.values()) + list(mapping.get("component_columns", {}).values()), context)
            net = _command_net(row, mapping, context)
            for component, cell in pending:
                component = _text(component, f"{context} component")
                expression = cell.value
                if not isinstance(expression, str) or not _EXPRESSION.fullmatch(expression.strip()):
                    raise ValueError(f"{sheet.title}!{cell.coordinate}: 잘못된 명령 {expression!r}; A-2,D+1 형식을 사용하세요.")
                commands.append({"net": net, "component": component, "expression": expression.strip().upper(),
                                 "source": f"{sheet.title}!{cell.coordinate}"})
    finally:
        workbook.close()
    return commands


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
