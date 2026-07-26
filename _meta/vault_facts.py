"""Structured fact storage and retrieval."""

from __future__ import annotations

import datetime
import json
import re
import threading
import uuid

import yaml

from _meta import vault_runtime as rt


FACT_DOMAINS = ("identity", "pets", "work", "relationships", "preferences", "health")
FACT_TYPES = ("explicit", "inferred")
FACT_CONFIDENCES = ("high", "medium", "low")
FACT_PRIORITIES = ("pinned", "high", "normal", "temporary")
FACT_STATUSES = ("active", "superseded", "disputed", "archived")
FACT_BLOCK_RE = re.compile(
    r"<!-- fact:begin -->\s*```yaml\s*\r?\n(.*?)\r?\n```\s*<!-- fact:end -->",
    re.DOTALL,
)
FACT_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
FACT_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_fact_lock = threading.Lock()


def fact_domain_path(domain: str):
    return rt.VAULT / rt.FACTS_DIR / f"{domain}.md"


def _parse_fact_blocks(text: str) -> list[dict]:
    facts = []
    for match in FACT_BLOCK_RE.finditer(text):
        try:
            fact = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise ValueError(f"fact YAML 解析失败：{exc}") from exc
        if not isinstance(fact, dict):
            raise ValueError("fact block 必须是 YAML 对象。")
        facts.append(fact)
    return facts


def _normalize_fact(fact: dict) -> dict:
    normalized = dict(fact)
    for field in ("created", "updated", "valid_from", "valid_until"):
        value = normalized.get(field)
        if isinstance(value, (datetime.date, datetime.datetime)):
            normalized[field] = value.isoformat()
    refs = normalized.get("source_refs")
    if refs is None:
        normalized["source_refs"] = []
    elif not isinstance(refs, list):
        normalized["source_refs"] = [str(refs)]
    else:
        normalized["source_refs"] = [str(ref) for ref in refs]
    return normalized


def load_fact_domain(domain: str) -> tuple[dict, list[dict]]:
    filepath = fact_domain_path(domain)
    if not filepath.exists():
        return {}, []
    meta, body = rt.parse_frontmatter(
        filepath.read_text(encoding="utf-8", errors="strict")
    )
    return meta, [_normalize_fact(fact) for fact in _parse_fact_blocks(body)]


def _render_fact_domain(domain: str, meta: dict, facts: list[dict]) -> str:
    today = rt.today().isoformat()
    rendered_meta = dict(meta)
    rendered_meta.update(
        {
            "type": "fact-domain",
            "domain": domain,
            "updated": today,
            "tags": ["facts", domain],
        }
    )
    rendered_meta.setdefault("created", today)
    rendered_meta.setdefault("source", "memory-vault")
    parts = [
        f"# Fact domain: {domain}",
        "",
        "由 memory-vault MCP 的 `write_fact` 维护；事实值、状态和版本链不要直接手改。",
        "`note` 可在 Obsidian 中补充叙事背景。",
    ]
    for fact in facts:
        payload = yaml.safe_dump(
            fact,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        parts.extend(
            [
                "",
                f"## `{fact['id']}`",
                "",
                "<!-- fact:begin -->",
                "```yaml",
                payload,
                "```",
                "<!-- fact:end -->",
            ]
        )
    return rt.rebuild_file(rendered_meta, "\n".join(parts))


def _write_fact_domain(domain: str, meta: dict, facts: list[dict]) -> None:
    filepath = fact_domain_path(domain)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    temp = filepath.with_suffix(".md.tmp")
    temp.write_text(_render_fact_domain(domain, meta, facts), encoding="utf-8")
    temp.replace(filepath)


def _fact_value_equal(left: object, right: object) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, default=str
    )


def all_facts(domain: str = "") -> list[dict]:
    facts = []
    for current_domain in ([domain] if domain else list(FACT_DOMAINS)):
        _, domain_facts = load_fact_domain(current_domain)
        for fact in domain_facts:
            item = dict(fact)
            item.setdefault("domain", current_domain)
            facts.append(item)
    return facts


def is_fact_effective(
    fact: dict,
    on_date: datetime.date | None = None,
) -> bool:
    """Return whether an active fact is effective on the requested vault date."""

    if fact.get("status") != "active":
        return False
    effective_date = on_date or rt.today()
    valid_from = fact.get("valid_from")
    valid_until = fact.get("valid_until")
    try:
        starts = datetime.date.fromisoformat(str(valid_from)) if valid_from else None
        ends = datetime.date.fromisoformat(str(valid_until)) if valid_until else None
    except (TypeError, ValueError):
        return False
    return (
        (starts is None or starts <= effective_date)
        and (ends is None or effective_date <= ends)
    )


def _format_fact_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def render_compact_fact_context(facts: list[dict]) -> str:
    title = f"# {rt.OWNER} compact fact context"
    if not facts:
        return f"{title}\n\n当前没有匹配的 active pinned/high facts。"
    lines = [
        title,
        "",
        "以下内容由 fact 层实时生成；只包含当前有效、active 且 priority 为 pinned/high 的事实。",
        "",
    ]
    for fact in sorted(facts, key=lambda item: (item.get("domain", ""), item.get("key", ""))):
        lines.append(
            f"- **{fact.get('domain')}.{fact.get('key')}**: "
            f"{_format_fact_value(fact.get('value'))}"
        )
        note = str(fact.get("note") or "").strip()
        if note:
            lines.append(f"  - note: {note}")
        refs = fact.get("source_refs") or []
        lines.append(f"  - source_refs: {', '.join(str(ref) for ref in refs)}")
    return "\n".join(lines)


def get_facts(domain: str = "", status: str = "active", priority: str = "") -> str:
    """查询结构化事实；默认只返回当前有效的 active facts。

    status=all 用于审计，会包含尚未生效或已经过期但存储状态仍为 active 的版本。
    """
    if domain and domain not in FACT_DOMAINS:
        return f"domain 只能是：{', '.join(FACT_DOMAINS)}。"
    if status != "all" and status not in FACT_STATUSES:
        return f"status 只能是 all / {' / '.join(FACT_STATUSES)}。"
    if priority and priority not in FACT_PRIORITIES:
        return f"priority 只能是：{' / '.join(FACT_PRIORITIES)}。"
    rt.pull_if_stale()
    try:
        facts = all_facts(domain)
    except (OSError, UnicodeError, ValueError) as exc:
        return f"fact 层读取失败：{exc}"
    filtered = [
        fact
        for fact in facts
        if (
            status == "all"
            or (
                status == "active"
                and is_fact_effective(fact)
            )
            or (
                status != "active"
                and fact.get("status") == status
            )
        )
        and (not priority or fact.get("priority") == priority)
    ]
    if not filtered:
        return "没有匹配的 facts。"
    lines = [f"找到 {len(filtered)} 条 facts：", ""]
    for fact in sorted(
        filtered,
        key=lambda item: (
            item.get("domain", ""),
            item.get("key", ""),
            item.get("created", ""),
        ),
    ):
        lines.append(
            f"- **{fact.get('domain')}.{fact.get('key')}** "
            f"(`{fact.get('id')}`, {fact.get('status')}, {fact.get('priority')}): "
            f"{_format_fact_value(fact.get('value'))}"
        )
        lines.append(
            f"  - source_refs: {', '.join(str(ref) for ref in fact.get('source_refs') or [])}"
        )
        lines.append(
            f"  - validity: {fact.get('valid_from') or 'open'} → "
            f"{fact.get('valid_until') or 'open'}"
        )
        if fact.get("superseded_by"):
            lines.append(f"  - superseded_by: {fact['superseded_by']}")
        if str(fact.get("note") or "").strip():
            lines.append(f"  - note: {fact['note'].strip()}")
    return "\n".join(lines)


def _validate_iso_date(value: str, field: str, allow_empty: bool = False) -> str | None:
    if not value:
        return None if allow_empty else f"{field} 必须填写 YYYY-MM-DD。"
    try:
        datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return f"{field} 日期格式不对：{value}，需要 YYYY-MM-DD。"
    return None


@rt.serialized_mutation
def write_fact(
    domain: str,
    key: str,
    value: object,
    source_refs: list[str],
    type: str = "explicit",
    confidence: str = "high",
    priority: str = "normal",
    valid_from: str = "",
    valid_until: str = "",
    note: str = "",
    source: str = "unknown",
) -> str:
    """写入结构化事实，并收敛同 domain+key 的旧 active 版本。"""
    if domain not in FACT_DOMAINS or not FACT_DOMAIN_RE.fullmatch(domain):
        return f"domain 只能是：{', '.join(FACT_DOMAINS)}。"
    if not FACT_KEY_RE.fullmatch(key):
        return "key 格式不合法：使用小写字母/数字，并可用点、短横线或下划线分段。"
    if type not in FACT_TYPES:
        return f"type 只能是：{' / '.join(FACT_TYPES)}。"
    if confidence not in FACT_CONFIDENCES:
        return f"confidence 只能是：{' / '.join(FACT_CONFIDENCES)}。"
    if priority not in FACT_PRIORITIES:
        return f"priority 只能是：{' / '.join(FACT_PRIORITIES)}。"
    refs = [str(ref).strip() for ref in (source_refs or []) if str(ref).strip()]
    if not refs:
        return "source_refs 强制填写，至少提供一个可追溯来源。"
    if value is None:
        return "value 不能是 null；不确定内容应进入 inbox，已失效内容请写新 fact 收敛。"
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return "value 必须是可 JSON 序列化的标量、列表或对象。"

    valid_from = valid_from or rt.today().isoformat()
    error = _validate_iso_date(valid_from, "valid_from")
    if error:
        return error
    error = _validate_iso_date(valid_until, "valid_until", allow_empty=True)
    if error:
        return error
    if valid_until and valid_until < valid_from:
        return "valid_until 不能早于 valid_from。"

    now_iso = rt.now().isoformat(timespec="seconds")
    with _fact_lock:
        try:
            meta, facts = load_fact_domain(domain)
        except (OSError, UnicodeError, ValueError) as exc:
            return f"拒绝写入：现有 {domain} fact 文件无法安全解析：{exc}"
        active_same_key = [
            fact for fact in facts
            if fact.get("key") == key and fact.get("status") == "active"
        ]
        desired = {
            "key": key,
            "value": value,
            "type": type,
            "confidence": confidence,
            "priority": priority,
            "valid_from": valid_from,
            "valid_until": valid_until or None,
            "source_refs": refs,
            "note": note.strip(),
            "source": source,
        }
        for fact in active_same_key:
            comparable = {field: fact.get(field) for field in desired}
            if all(_fact_value_equal(comparable[field], desired[field]) for field in desired):
                return (
                    f"fact 已是 active，无需重复写入：{domain}.{key} "
                    f"(`{fact.get('id')}`)"
                )

        candidate_active = [
            fact for fact in facts
            if fact.get("status") == "active" and fact.get("key") != key
        ] + [{"type": type}]
        explicit_count = sum(fact.get("type") == "explicit" for fact in candidate_active)
        inferred_count = sum(fact.get("type") == "inferred" for fact in candidate_active)
        if inferred_count > int(explicit_count * 0.30):
            return (
                "拒绝写入：该域 active inferred facts 将超过 explicit facts 的 30% 上限 "
                f"（explicit={explicit_count}, inferred={inferred_count}）。"
            )

        safe_key = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
        fact_id = f"{domain}.{safe_key}--{uuid.uuid4().hex[:12]}"
        for fact in active_same_key:
            fact["status"] = "superseded"
            fact["superseded_by"] = fact_id
            fact["updated"] = now_iso
        facts.append(
            {
                "id": fact_id,
                "domain": domain,
                **desired,
                "status": "active",
                "superseded_by": None,
                "created": now_iso,
                "updated": now_iso,
            }
        )
        try:
            _write_fact_domain(domain, meta, facts)
        except (OSError, UnicodeError, yaml.YAMLError, KeyError) as exc:
            return f"fact 写入失败：{exc}"

    filepath = fact_domain_path(domain)
    sync_status = rt.git_sync(
        f"auto: fact {domain}.{key} (source: {source})",
        filepath,
    )
    replaced = f"，取代 {len(active_same_key)} 个旧 active 版本" if active_same_key else ""
    return f"已写入 fact：{domain}.{key} (`{fact_id}`){replaced} {sync_status}"
