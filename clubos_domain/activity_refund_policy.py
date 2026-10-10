from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime
import json
from typing import Any


@dataclass
class RefundRule:
    min_hours_before: float
    cash_refund_percent: float
    label: str

    def as_dict(self):
        return {
            'minHoursBefore': self.min_hours_before,
            'cashRefundPercent': self.cash_refund_percent,
            'label': self.label,
        }


@dataclass
class ActivityRefundPolicy:
    enabled: bool
    rules: list[RefundRule]
    after_start_cash_refund_percent: float
    note: str

    def as_dict(self):
        return {
            'enabled': self.enabled,
            'rules': [r.as_dict() for r in self.rules],
            'afterStartCashRefundPercent': self.after_start_cash_refund_percent,
            'note': self.note,
        }


class ActivityRefundPolicyService:
    """Club-owned activity refund policy.

    The policy governs the *cash* refund percentage by time-to-start. Club/Gear Points,
    redeemed benefit vouchers, seat release, and earned-point reversal stay in ClubOS
    domain logic. In v0.11 they are fully restored/reversed once a cancellation succeeds;
    only cash can be retained as a cancellation fee.
    """

    DEFAULT_POLICY = ActivityRefundPolicy(
        enabled=True,
        rules=[RefundRule(0, 100, '活动开始前可退款')],
        after_start_cash_refund_percent=0,
        note='默认规则：活动开始前现金全额退；活动开始后不退。俱乐部可按活动单独调整。',
    )

    def from_activity(self, activity: dict[str, Any]) -> ActivityRefundPolicy:
        raw = activity.get('refund_policy_json')
        enabled = bool(int(activity.get('refund_enabled', 1) or 0))
        if not raw:
            base = self.DEFAULT_POLICY
            return ActivityRefundPolicy(enabled=enabled, rules=list(base.rules),
                                        after_start_cash_refund_percent=base.after_start_cash_refund_percent,
                                        note=base.note)
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            data = {}
        # 库里可能存进字面量 'null'（如跨库迁移时 JSON null 被写成字符串）：
        # json.loads('null') 返回 None，下面 data.get 会直接 AttributeError 把整个
        # 活动 GET / 公开详情 / 退款报价全部打成 500。非 dict 一律按「未配置」处理。
        if not isinstance(data, dict):
            data = {}
        rules = []
        for x in data.get('rules') or []:
            try:
                h = max(0.0, float(x.get('minHoursBefore') or 0))
                pct = max(0.0, min(100.0, float(x.get('cashRefundPercent') or 0)))
                label = str(x.get('label') or '').strip() or f'出发前 {h:g} 小时'
                rules.append(RefundRule(h, pct, label))
            except Exception:
                continue
        if not rules:
            rules = list(self.DEFAULT_POLICY.rules)
        rules.sort(key=lambda r: r.min_hours_before, reverse=True)
        after = max(0.0, min(100.0, float(data.get('afterStartCashRefundPercent') or 0)))
        note = str(data.get('note') or self.DEFAULT_POLICY.note)
        return ActivityRefundPolicy(enabled=enabled, rules=rules,
                                    after_start_cash_refund_percent=after, note=note)

    def normalize_update(self, payload: dict[str, Any], current: ActivityRefundPolicy | None = None) -> ActivityRefundPolicy:
        current = current or self.DEFAULT_POLICY
        enabled = bool(payload.get('enabled', current.enabled))
        src_rules = payload.get('rules')
        if src_rules is None:
            rules = list(current.rules)
        else:
            rules = []
            for x in src_rules:
                h = max(0.0, float(x.get('minHoursBefore') or 0))
                pct = max(0.0, min(100.0, float(x.get('cashRefundPercent') or 0)))
                label = str(x.get('label') or '').strip() or f'出发前 {h:g} 小时'
                rules.append(RefundRule(h, pct, label))
            if not rules:
                rules = list(self.DEFAULT_POLICY.rules)
        # duplicated boundaries are ambiguous for both users and accounting
        hours = [round(r.min_hours_before, 6) for r in rules]
        if len(hours) != len(set(hours)):
            raise ValueError('退款规则中不能出现重复的时间门槛')
        rules.sort(key=lambda r: r.min_hours_before, reverse=True)
        after = max(0.0, min(100.0, float(payload.get('afterStartCashRefundPercent', current.after_start_cash_refund_percent) or 0)))
        note = str(payload.get('note', current.note) or '').strip()
        return ActivityRefundPolicy(enabled=enabled, rules=rules,
                                    after_start_cash_refund_percent=after, note=note)

    def persist(self, c, activity_id: int, club_id: int, policy: ActivityRefundPolicy):
        data = policy.as_dict().copy()
        enabled = data.pop('enabled')
        c.execute('''UPDATE activities SET refund_enabled=?,refund_policy_json=?,updated_at=CURRENT_TIMESTAMP
                     WHERE id=? AND club_id=?''',
                  (1 if enabled else 0, json.dumps(data, ensure_ascii=False), activity_id, club_id))

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        s = str(value).strip().replace('Z', '+00:00')
        for candidate in (s, s.replace(' ', 'T')):
            try:
                dt = datetime.fromisoformat(candidate)
                # Keep comparisons simple for the prototype: if provider timestamp is aware,
                # compare using local wall-clock fields after removing timezone.
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
                return dt
            except Exception:
                pass
        try:
            return datetime.strptime(s[:10], '%Y-%m-%d')
        except Exception:
            return None

    def evaluate(self, *, activity: dict[str, Any], occurrence: dict[str, Any] | None,
                 cash_paid: float, now: datetime | None = None) -> dict[str, Any]:
        policy = self.from_activity(activity)
        now = now or datetime.now()
        start = self._parse_dt((occurrence or {}).get('start_at') if occurrence else activity.get('event_date'))
        if not start:
            # Missing start time must not silently invent a cancellation bucket.
            return {
                'eligible': False,
                'reason': '缺少有效团期开始时间，不能自动计算退款规则',
                'cashRefundPercent': 0,
                'cashRefundAmount': 0,
                'retainedCashAmount': round(max(0.0, float(cash_paid or 0)), 2),
                'hoursBefore': None,
                'matchedRule': None,
                'policy': policy.as_dict(),
            }
        hours_before = (start - now).total_seconds() / 3600.0
        if not policy.enabled:
            pct = 0.0
            label = '本活动不支持自主退款'
        elif hours_before < 0:
            pct = policy.after_start_cash_refund_percent
            label = '活动已开始'
        else:
            pct = None
            label = None
            for rule in policy.rules:
                if hours_before >= rule.min_hours_before:
                    pct = rule.cash_refund_percent
                    label = rule.label
                    break
            if pct is None:
                pct = 0.0
                label = '未匹配可退款规则'
        cash = max(0.0, float(cash_paid or 0))
        refund = round(cash * float(pct) / 100.0, 2)
        retained = round(max(0.0, cash - refund), 2)
        eligible = bool(policy.enabled and float(pct) > 0)
        return {
            'eligible': eligible,
            'reason': label,
            'cashRefundPercent': round(float(pct), 2),
            'cashRefundAmount': refund,
            'retainedCashAmount': retained,
            'hoursBefore': round(hours_before, 2),
            'matchedRule': label,
            'policy': policy.as_dict(),
            'nonCashRestoration': 'FULL_ON_SUCCESS',
        }
