from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from .models import Rule, FIELDS

def validate_rule(rule: Rule, source_ids: set[str]):
    if rule.op in {"ALL", "ANY", "NOT"}:
        if not rule.children or (rule.op == "NOT" and len(rule.children) != 1):
            raise ValueError("Empty or malformed logical group")
        for child in rule.children:
            validate_rule(child, source_ids)
        return
    if rule.field not in FIELDS or rule.source_id not in source_ids or not rule.source_section:
        raise ValueError(f"Rule {rule.id} requires a known field and a valid source section")
    if rule.op in {"in", "between"} and not isinstance(rule.value, list):
        raise ValueError("Set and range operators require a list")
    if rule.op == "between" and len(rule.value) != 2:
        raise ValueError("Range requires two values")

def evaluate_rule(rule: Rule, facts: dict):
    if rule.op in {"ALL", "ANY", "NOT"}:
        children = [evaluate_rule(c, facts) for c in rule.children]
        states = [c["state"] for c in children]
        if rule.op == "ALL":
            state = "FAIL" if "FAIL" in states else "PASS" if states and all(s == "PASS" for s in states) else "UNKNOWN"
        elif rule.op == "ANY":
            state = "PASS" if "PASS" in states else "FAIL" if states and all(s == "FAIL" for s in states) else "UNKNOWN"
        else:
            state = {"PASS": "FAIL", "FAIL": "PASS", "UNKNOWN": "UNKNOWN"}[states[0]] if len(states) == 1 else "UNKNOWN"
        return {"id": rule.id, "label": rule.label, "label_ta": rule.label_ta, "state": state, "children": children}
    value = facts.get(rule.field)
    result = {"id": rule.id, "label": rule.label, "label_ta": rule.label_ta, "field": rule.field, "state": "UNKNOWN", "source_id": rule.source_id, "source_section": rule.source_section}
    if value is None or value == "":
        return result
    try:
        op = rule.op
        if op == "eq":
            passed = type(value) is type(rule.value) and value == rule.value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                passed = Decimal(str(value)) == Decimal(str(rule.value))
        elif op == "in":
            passed = value in rule.value
        else:
            a = Decimal(str(value))
            b = Decimal(str(rule.value)) if op != "between" else None
            if not a.is_finite() or (b is not None and not b.is_finite()):
                return result
            passed = {"lte": lambda: a <= b, "gte": lambda: a >= b, "lt": lambda: a < b, "gt": lambda: a > b, "between": lambda: Decimal(str(rule.value[0])) <= a <= Decimal(str(rule.value[1]))}[op]()
        result["state"] = "PASS" if passed else "FAIL"
    except (ValueError, TypeError, InvalidOperation):
        pass
    return result

def leaves(result):
    if "children" not in result:
        return [result]
    return [item for child in result["children"] for item in leaves(child)]

def window_status(scheme, today=None):
    today = today or date.today()
    if scheme.get("window_status") == "CLOSED":
        return "CLOSED"
    start, end = scheme.get("window_start"), scheme.get("window_end")
    if not start or not end:
        return "UNKNOWN"
    return "OPEN" if date.fromisoformat(start) <= today <= date.fromisoformat(end) else "CLOSED"

def application_readiness(result, facts):
    """Preparation can proceed with provisional rules; final submission cannot."""
    preparation = []
    if result["preliminary_state"] == "FAIL":
        preparation.append("A checked condition does not match your profile. Open Rules & sources to see it.")
    elif result["preliminary_state"] != "PASS":
        preparation.append("Complete the missing profile details shown for this scheme.")
    if not str(facts.get("full_name", "")).strip():
        preparation.append("Save your full name in My profile.")
    if result["document_status"] != "READY":
        preparation.append("Upload the missing document types listed for this scheme.")
    if result["window_status"] == "CLOSED":
        preparation.append("The recorded application window is closed. Check the official dates.")
    submission = list(preparation)
    if result["review_reasons"] or result["status"] == "REVIEW_REQUIRED":
        submission.append("Complete scheme policy verification in Sources & review before submission.")
    if result["window_status"] == "UNKNOWN":
        submission.append("Verify the scheme's current application dates before submission.")
    return {
        "can_prepare": not preparation,
        "can_submit": not submission and result["status"] == "MATCH",
        "preparation_blockers": preparation,
        "submission_blockers": submission,
        "policy_status": "REVIEW_REQUIRED" if result["review_reasons"] or result["status"] == "REVIEW_REQUIRED" else "REVIEWED",
    }

def assess(scheme, facts, held_documents, today=None):
    today = today or date.today()
    rule = Rule.model_validate(scheme["rule"])
    tree = evaluate_rule(rule, facts)
    flat = leaves(tree)
    review_reasons = []
    if not scheme.get("coverage_complete"):
        review_reasons.append("The published summary does not establish complete policy coverage.")
    if scheme.get("review_status") != "REVIEWED":
        review_reasons.append("This rule version requires reviewer sign-off.")
    if scheme.get("policy_changed"):
        review_reasons.append("An official source changed after this version was reviewed.")
    expiry = scheme.get("review_valid_until")
    if not expiry or date.fromisoformat(expiry) < today:
        review_reasons.append("The source review has expired or has no validity date.")
    if tree["state"] == "FAIL":
        status = "NO_MATCH"
    elif tree["state"] == "UNKNOWN":
        status = "NEEDS_INFORMATION"
    elif review_reasons:
        status = "REVIEW_REQUIRED"
    else:
        status = "MATCH"
    # Only request unknown facts in unresolved branches, avoiding irrelevant OR branches.
    def unresolved(node):
        if node["state"] != "UNKNOWN":
            return []
        if "children" not in node:
            return [node["field"]]
        return [field for child in node["children"] for field in unresolved(child)]
    missing_docs = [x for x in scheme["documents"] if x["id"] not in held_documents]
    result = {"scheme_id": scheme["id"], "name": scheme["name"], "name_ta": scheme["name_ta"], "status": status, "preliminary_state": tree["state"], "criteria": flat, "tree": tree, "missing_fields": sorted(set(unresolved(tree))), "review_reasons": review_reasons, "window_status": window_status(scheme, today), "document_status": "READY" if not missing_docs else "INCOMPLETE", "missing_documents": missing_docs, "rule_version": scheme["rule_version"], "source_ids": sorted(set(x["source_id"] for x in flat if x.get("source_id"))), "benefit": scheme["benefit"], "portal_url": scheme["portal_url"], "is_demo": scheme.get("is_demo", False)}
    return {**result, **application_readiness(result, facts)}
