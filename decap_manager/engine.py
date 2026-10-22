"""Pure, transactional decap adjustment and placement calculations."""

from collections import Counter, defaultdict
import math
import re


_CAPACITANCE = re.compile(
    r"(?<![A-Z0-9.])([0-9]+(?:\.[0-9]+)?)\s*([PNUM]?)([0-9]*)\s*F(?![A-Z0-9])"
)
_ACTIVE_STATUSES = {"Automatic", "Disabled"}


def parse_capacitance(name):
    """Return explicit capacitance in pF, or None for unknown/ambiguous names."""
    text = str(name).upper().replace("Μ", "U").replace("µ", "U")
    matches = list(_CAPACITANCE.finditer(text))
    if len(matches) != 1:
        return None
    number, unit, fraction = matches[0].groups()
    if fraction and (not unit or "." in number):
        return None
    value = float(number + ("." + fraction if fraction else ""))
    value *= {"P": 1, "N": 1_000, "U": 1_000_000, "M": 1_000_000_000, "": 1e12}[unit]
    return value if math.isfinite(value) and value > 0 else None


def parse_expression(text):
    """Parse A+2,D-1; each comma-separated operation uses the previous state."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("명령이 비어 있습니다. A+1, D-2처럼 입력하세요.")
    result = []
    for part in text.split(","):
        match = re.fullmatch(r"\s*([ADad])\s*([+-])\s*([0-9]+)\s*", part)
        if not match or int(match[3]) == 0:
            raise ValueError(f"잘못된 명령 {part!r}: A+1, D-2 형식과 1 이상의 수량을 사용하세요.")
        result.append((match[1].upper(), int(match[3]) * (1 if match[2] == "+" else -1)))
    return result


class _CalculationError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _counts(records):
    totals, active = Counter(), Counter()
    for record in records:
        key = (record.get("net", ""), record.get("component", ""))
        if not all(isinstance(value, str) for value in key):
            continue
        totals[key] += 1
        active[key] += record.get("status") == "Automatic"
    return totals, active


def _invalid_distance(record):
    distance = record.get("distance")
    return type(distance) not in (int, float) or not math.isfinite(distance) or distance < 0


def compute(records, commands, *, priority=None, policies=None, rearrange=False,
            selected_nets=None, aliases=None):
    """Calculate a preview without mutating input; any error rolls back everything.

    Policies and aliases are explicit exact-name mappings. No historical conversion
    or inferred component-name matching is applied. Enabled rows are preserved when
    outside the requested Automatic/Disabled groups.
    """
    original = [dict(record) for record in records]
    state = [dict(record) for record in original]
    issues, actions = [], []
    before_total, before_active = _counts(original)
    expected_active = before_active.copy()
    expected_total = before_total.copy()
    reasons = defaultdict(list)
    authorized = defaultdict(set)

    def issue(code, message, level="error"):
        issues.append({"level": level, "code": code, "message": message})

    def finish(ok):
        final = state if ok else original
        after_total, after_active = _counts(final)
        keys = set(before_total) | set(expected_active) | set(after_total)
        summary = [dict(net=net, component=component,
                        before_total=before_total[(net, component)],
                        after_total=after_total[(net, component)],
                        before_active=before_active[(net, component)],
                        expected_active=expected_active[(net, component)],
                        after_active=after_active[(net, component)])
                   for net, component in sorted(keys)]
        changes = []
        if ok:
            for old, new in zip(original, final):
                if (old["component"], old["status"]) != (new["component"], new["status"]):
                    changes.append(dict(refdes=old["refdes"], net=old["net"], row=old["row"],
                                        old_component=old["component"], new_component=new["component"],
                                        old_status=old["status"], new_status=new["status"],
                                        distance=old.get("distance"),
                                        reason="; ".join(dict.fromkeys(reasons[old["row"]]))))
        for action in actions:
            action["applied"] = ok
        return dict(ok=ok, records=[dict(r) for r in final], changes=changes,
                    actions=actions, summary=summary, issues=issues)

    seen_refs, seen_rows = set(), set()
    for record in state:
        label = f"RefDes {record.get('refdes', '?')}"
        for field in ("refdes", "component", "status"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                issue("invalid_record", f"{label}: {field} 값이 비어 있거나 문자열이 아닙니다.")
        if not isinstance(record.get("net"), str):
            issue("invalid_record", f"{label}: NET은 문자열이어야 합니다 (빈 NET은 그대로 보존 가능).")
        row = record.get("row")
        if type(row) is not int or row < 1:
            issue("invalid_record", f"{label}: Excel 행 번호가 올바르지 않습니다.")
        elif row in seen_rows:
            issue("duplicate_row", f"Excel {row}행이 중복되었습니다.")
        if type(row) is int:
            seen_rows.add(row)
        refdes = record.get("refdes")
        if isinstance(refdes, str) and refdes in seen_refs:
            issue("duplicate_refdes", f"RefDes {refdes}가 중복되었습니다. 정확한 자리를 선택할 수 없습니다.")
        if isinstance(refdes, str):
            seen_refs.add(refdes)
    if issues:
        return finish(False)
    missing_distance = [f"{r['refdes']} ({r['net']})" for r in state
                        if r["status"] in _ACTIVE_STATUSES and _invalid_distance(r)]
    if missing_distance:
        issue("unavailable_distance", f"Automatic/Disabled {len(missing_distance)}개 자리의 distance가 없거나 잘못되었습니다. "
              "선택 후보에 포함되면 계산을 중단합니다: " + ", ".join(missing_distance), "warning")

    aliases = aliases or {}
    if not isinstance(aliases, dict) or any(
            not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
            for k, v in aliases.items()):
        issue("invalid_alias", "별칭은 명령 부품명 → 정확한 부품명의 문자열 매핑이어야 합니다.")
        return finish(False)
    component_names = {r["component"] for r in state}
    net_names = {r["net"] for r in state}
    policy_by_target = {}
    for policy in policies or []:
        if not isinstance(policy, dict) or any(
            not isinstance(policy.get(key), str) or not policy[key].strip()
            for key in ("target", "donor")
        ) or policy.get("direction") not in ("A", "D") or policy.get("donor_status") not in (
            "Disabled", "any"
        ) or type(policy.get("compensate")) is not bool:
            issue("invalid_policy", "변환 규칙의 target/donor, A/D, Disabled/any, compensate 값을 확인하세요.")
            continue
        if policy["target"] == policy["donor"] or policy["target"] in policy_by_target:
            issue("conflicting_policy", f"{policy['target']}: 자기 자신 또는 중복된 변환 규칙입니다.")
        else:
            policy_by_target[policy["target"]] = dict(policy)

    parsed_commands, seen_commands = [], set()
    for command in commands:
        if not isinstance(command, dict):
            issue("invalid_command", "명령 행이 올바르지 않습니다.")
            continue
        net, component = command.get("net"), command.get("component")
        source = str(command.get("source", "직접 입력"))
        if not isinstance(net, str) or not net.strip() or not isinstance(component, str) or not component.strip():
            issue("invalid_command", f"{source}: NET과 부품명을 모두 지정하세요.")
            continue
        component = aliases.get(component, component)
        key = (net, component)
        if key in seen_commands:
            issue("conflicting_commands", f"{source}: {net} / {component} 명령 행이 중복됩니다. 순차 명령은 한 셀에 콤마로 연결하세요.")
        seen_commands.add(key)
        if net not in net_names:
            issue("unknown_net", f"{source}: NET {net}가 RefDes 파일에 없습니다.")
        if component not in component_names and component not in policy_by_target:
            issue("unknown_component", f"{source}: 정확히 일치하는 부품 {component}가 없습니다. 명시적 별칭 또는 변환 규칙을 지정하세요.")
        try:
            operations = parse_expression(command.get("expression"))
        except ValueError as exc:
            issue("invalid_expression", f"{source}: {exc}")
            continue
        parsed_commands.append(dict(net=net, component=component, expression=command["expression"],
                                    source=source, operations=operations))
        expected_active[key] += sum(delta for _, delta in operations)
        authorized[net].add(component)

    if rearrange:
        if not priority or isinstance(priority, str) or any(not isinstance(c, str) or not c.strip() for c in priority):
            issue("invalid_priority", "재배치할 부품을 가까운 곳 → 먼 곳 순서로 명시하세요.")
        elif len(priority) != len(set(priority)):
            issue("invalid_priority", "재배치 우선순위에 같은 부품이 중복되었습니다.")
        elif set(priority) - (component_names | set(policy_by_target)):
            issue("unknown_component", "재배치 목록에 알 수 없는 부품이 있습니다: " + ", ".join(sorted(set(priority) - component_names - set(policy_by_target))))
        if not selected_nets or isinstance(selected_nets, str) or any(not isinstance(n, str) or not n.strip() for n in selected_nets):
            issue("invalid_scope", "재배치할 NET을 명시적으로 선택하세요.")
        elif set(selected_nets) - net_names:
            issue("unknown_net", "재배치 NET이 RefDes 파일에 없습니다: " + ", ".join(sorted(set(selected_nets) - net_names)))
    if any(i["level"] == "error" for i in issues):
        return finish(False)

    def group(net, component):
        return [r for r in state if r["net"] == net and r["component"] == component]

    def validate_statuses(rows, context):
        unsupported = [f"{r['refdes']}={r['status']}" for r in rows if r["status"] not in _ACTIVE_STATUSES]
        if unsupported:
            raise _CalculationError("unsupported_status", f"{context}: Automatic/Disabled 이외의 상태가 있습니다: {', '.join(unsupported)}")

    def ordered(rows, direction, context, limit=None):
        missing = [r["refdes"] for r in rows if _invalid_distance(r)]
        if missing:
            raise _CalculationError("missing_distance", f"{context}: 선택 후보의 distance가 없거나 잘못되었습니다: {', '.join(missing)}")
        rows = sorted(rows, key=lambda r: (r["distance"] if direction == "A" else -r["distance"], r["row"]))
        if limit and len(rows) > limit and rows[limit - 1]["distance"] == rows[limit]["distance"]:
            issue("distance_tie", f"{context}: 선택 경계의 distance {rows[limit]['distance']:g}가 같습니다. Excel 행 번호가 작은 자리를 먼저 선택했습니다.", "warning")
        return rows

    def change(record, component, status, kind, reason, command=None, **metadata):
        action = dict(kind=kind, refdes=record["refdes"], net=record["net"], row=record["row"],
                      distance=record["distance"], old_component=record["component"],
                      new_component=component, old_status=record["status"], new_status=status,
                      reason=reason, **metadata)
        if command:
            action.update(source=command["source"], expression=command["expression"],
                          target_component=command["component"])
        actions.append(action)
        reasons[record["row"]].append(reason)
        record["component"], record["status"] = component, status

    try:
        for command in parsed_commands:
            net, component = command["net"], command["component"]
            context = f"{command['source']}: {net} / {component}"
            validate_statuses(group(net, component), context)
            for step, (direction, delta) in enumerate(command["operations"], 1):
                count = abs(delta)
                old_status, new_status = ("Disabled", "Automatic") if delta > 0 else ("Automatic", "Disabled")
                eligible = ordered([r for r in group(net, component) if r["status"] == old_status],
                                   direction, context, count)
                chosen = eligible[:count]
                for record in chosen:
                    reason = f"명령 {direction}{delta:+d} ({step}단계): {old_status} → {new_status}, 거리 {record['distance']:g}"
                    change(record, component, new_status, "command", reason, command,
                           step=step, direction=direction, delta=delta)
                missing = count - len(chosen)
                if not missing:
                    continue
                policy = policy_by_target.get(component) if delta > 0 else None
                if policy is None:
                    raise _CalculationError("shortage", f"{context}: {direction}{delta:+d}에 필요한 {old_status} {count}개 중 {len(chosen)}개만 있습니다 (부족 {missing}개).")
                donor = policy["donor"]
                donor_rows = group(net, donor)
                validate_statuses(donor_rows, context + f" / 변환 원본 {donor}")
                candidates = [r for r in donor_rows if policy["donor_status"] == "any" or r["status"] == "Disabled"]
                donor_rows = ordered(candidates, policy["direction"], context + f" / 변환 원본 {donor}", missing)[:missing]
                if len(donor_rows) < missing:
                    raise _CalculationError("shortage", f"{context}: 기존 {old_status} {len(chosen)}개, 변환 가능한 {donor} {len(donor_rows)}개로 {count}개를 채울 수 없습니다 (부족 {missing - len(donor_rows)}개).")
                authorized[net].add(donor)
                active_donors = sum(r["status"] == "Automatic" for r in donor_rows)
                for record in donor_rows:
                    reason = f"부족분 변환: {donor} → {component}/Automatic, {policy['direction']}순 거리 {record['distance']:g}"
                    change(record, component, "Automatic", "conversion", reason, command,
                           step=step, direction=policy["direction"], compensate=policy["compensate"])
                expected_total[(net, donor)] -= missing
                expected_total[(net, component)] += missing
                if active_donors and policy["compensate"]:
                    compensation = ordered([r for r in group(net, donor) if r["status"] == "Disabled"],
                                           "A", context + f" / {donor} 활성 수량 보정", active_donors)[:active_donors]
                    if len(compensation) < active_donors:
                        raise _CalculationError("compensation_shortage", f"{context}: 변환한 Automatic {donor} {active_donors}개를 보정할 남은 Disabled가 {len(compensation)}개뿐입니다.")
                    for record in compensation:
                        reason = f"활성 수량 보정: {donor} 변환으로 줄어든 Automatic을 가장 가까운 Disabled에서 복구, 거리 {record['distance']:g}"
                        change(record, donor, "Automatic", "compensation", reason, command, step=step, direction="A")
                elif active_donors:
                    expected_active[(net, donor)] -= active_donors
                    issue("donor_active_reduced", f"{context}: 보정하지 않는 명시적 규칙에 따라 {donor}의 Automatic이 {active_donors}개 감소합니다.", "warning")

        if rearrange:
            ranks = {component: index for index, component in enumerate(priority)}
            for net in sorted(set(selected_nets)):
                authorized[net].update(priority)
                rows = [r for r in state if r["net"] == net and r["component"] in ranks]
                validate_statuses(rows, f"{net} 재배치")
                rows = ordered(rows, "A", f"{net} 재배치")
                payloads = sorted([dict(r) for r in rows], key=lambda r: ranks[r["component"]])
                for record, payload in zip(rows, payloads):
                    if (record["component"], record["status"]) == (payload["component"], payload["status"]):
                        continue
                    reason = (f"우선순위 재배치 {ranks[payload['component']] + 1}순위: "
                              f"{payload['refdes']} 거리 {payload['distance']:g}의 부품/상태 → 거리 {record['distance']:g}")
                    change(record, payload["component"], payload["status"], "rearrange", reason,
                           origin_refdes=payload["refdes"], origin_distance=payload["distance"],
                           priority=ranks[payload["component"]] + 1)

        # Independent group recount and before-image comparison guard the transaction.
        actual_total, actual_active = _counts(state)
        for key in set(before_total) | set(expected_active) | set(actual_total):
            if actual_active[key] != expected_active[key] or actual_total[key] != expected_total[key]:
                raise _CalculationError("count_mismatch", f"{key[0]} / {key[1]}: 수량 검산 실패 (Automatic 예상 {expected_active[key]}, 결과 {actual_active[key]}; 전체 예상 {expected_total[key]}, 결과 {actual_total[key]}).")
        accounted_rows = {a["row"] for a in actions}
        for old, new in zip(original, state):
            immutable_old = {k: v for k, v in old.items() if k not in ("component", "status")}
            immutable_new = {k: v for k, v in new.items() if k not in ("component", "status")}
            if immutable_old != immutable_new:
                raise _CalculationError("invariant_failed", f"{old['refdes']}: RefDes, NET 또는 물리 자리 정보가 변경되었습니다.")
            if old != new and (old["row"] not in accounted_rows or old["component"] not in authorized[old["net"]]
                               or new["component"] not in authorized[old["net"]]):
                raise _CalculationError("unauthorized_change", f"{old['refdes']}: 지정되지 않은 NET/부품/자리 변경이 발견되었습니다.")
    except _CalculationError as exc:
        issue(exc.code, str(exc))
        issue("atomic_rollback", "오류가 있어 모든 변경을 취소했습니다. 원본 상태를 유지하며 내보낼 수 없습니다.", "info")
        return finish(False)
    issue("verified", f"{len(state)}개 물리 자리, {len(set(before_total) | set(expected_active))}개 NET/부품 조합 수량 및 지정 범위 검산을 통과했습니다.", "info")
    return finish(True)
