"""Validated, preview-only command alternatives for insufficient physical slots."""

from copy import deepcopy

from .engine import compute, parse_expression


def find_shortages(records, commands, aliases=None):
    """Find NETs whose requested Automatic total exceeds physical decap slots."""
    aliases = aliases or {}
    groups = {}
    for record in records:
        if record["status"] not in ("Automatic", "Disabled"):
            continue
        components = groups.setdefault(record["net"], {})
        item = components.setdefault(record["component"], dict(
            component=record["component"], total=0, active=0, requested_active=0))
        item["total"] += 1
        item["active"] += record["status"] == "Automatic"
        item["requested_active"] = item["active"]
    net_order = dict.fromkeys(command["net"] for command in commands)
    for command in commands:
        component = aliases.get(command["component"], command["component"])
        item = groups.setdefault(command["net"], {}).setdefault(component, dict(
            component=component, total=0, active=0, requested_active=0))
        item["requested_active"] += sum(delta for _, delta in parse_expression(command["expression"]))
    shortages = []
    for net in net_order:
        components = list(groups[net].values())
        total = sum(item["total"] for item in components)
        active = sum(item["active"] for item in components)
        requested = sum(item["requested_active"] for item in components)
        if requested > total:
            shortages.append(dict(net=net, total=total, active=active,
                                  requested_active=requested, deficit=requested - total,
                                  components=components))
    return shortages


def shortage_choices(records, commands, shortage, *, policies, aliases=None):
    """Return only alternatives that pass the engine for the chosen NET.

    Other NET commands remain in each deep-copied command list for the caller's
    complete preview; their execution and optional rearrangement are not tested here.
    """
    aliases = aliases or {}
    net, deficit = shortage["net"], shortage["deficit"]
    if deficit <= 0:
        return []
    indexed = [(index, command, aliases.get(command["component"], command["component"]),
                parse_expression(command["expression"]))
               for index, command in enumerate(commands) if command["net"] == net]
    if not indexed:
        return []
    choices = []

    def offer(candidate, component, source, before, after, strategy, label, detail):
        if any(choice["commands"] == candidate for choice in choices):
            return
        result = compute(records, [c for c in candidate if c["net"] == net],
                         policies=policies, aliases=aliases)
        if not result["ok"]:
            return
        effect = (f"{net} 요청 Automatic {shortage['requested_active']}개 → "
                  f"{shortage['requested_active'] - deficit}개 (물리 {shortage['total']}자리)")
        message = (f"이번 계산 수량 조정: {net} / {component}, {source}: "
                   f"{before or '명령 없음'} → {after or '명령 삭제'}. {detail} {effect}.")
        adjustment = dict(level="info", code="quantity_adjustment", message=message,
                          net=net, component=component, source=source,
                          original_expression=before, adjusted_expression=after,
                          strategy=strategy, delta=-deficit,
                          requested_active_before=shortage["requested_active"],
                          requested_active_after=shortage["requested_active"] - deficit)
        choices.append(dict(label=label, description=message + " 원본 명령 파일은 변경하지 않습니다.",
                            commands=candidate, adjustment=adjustment,
                            summary=[s for s in result["summary"] if s["net"] == net]))

    for index, command, component, operations in indexed:
        for step, (direction, delta) in enumerate(operations):
            if delta < deficit:
                continue
            adjusted = list(operations)
            adjusted[step] = (direction, delta - deficit)
            expression = ",".join(f"{order}{amount:+d}" for order, amount in adjusted if amount)
            candidate = deepcopy(commands)
            source = str(command.get("source", "직접 입력"))
            if expression:
                candidate[index]["expression"] = expression
                candidate[index]["source"] = source + " [이번계산조정]"
            else:
                candidate.pop(index)
            offer(candidate, component, source, command["expression"], expression, "reduce_addition",
                  f"{component} {step + 1}단계 추가 {delta}개 → {delta - deficit}개",
                  f"{direction}+{delta}의 추가 수량만 {deficit}개 줄이고 나머지 순차 명령을 유지합니다.")

    positive_components = {component for _, _, component, ops in indexed if any(delta > 0 for _, delta in ops)}
    for item in shortage["components"]:
        component = item["component"]
        if component in positive_components or item["active"] < deficit or item["requested_active"] < deficit:
            continue
        existing = next((entry for entry in indexed if entry[2] == component), None)
        candidate = deepcopy(commands)
        if existing:
            index, command, _, _ = existing
            source = str(command.get("source", "직접 입력"))
            before = command["expression"]
            expression = f"A-{deficit},{before}"
            candidate[index]["expression"] = expression
            candidate[index]["source"] = source + " [이번계산조정]"
        else:
            # An exact name redirected by an alias cannot name this donor in a new command.
            if aliases.get(component, component) != component:
                continue
            source, before, expression = "수량조정팝업", "", f"A-{deficit}"
            candidate.insert(indexed[0][0], dict(net=net, component=component,
                                               expression=expression, source=source))
        offer(candidate, component, source, before, expression, "reduce_other",
              f"{component} {deficit}개 감소 (A · 가까운 자리부터)",
              f"기존 추가 명령을 유지하고 {component}의 가까운 Automatic부터 {deficit}개를 Disabled로 바꿉니다.")
    return choices
