from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DEFAULT_POLICY = {
    "maxParticipantsPerOrder": 8,
    "allowIncompleteAtCheckout": True,
    "requiredAtCheckout": ["name", "phone"],
    "requiredBeforeDeparture": ["name", "phone", "idType", "idNumber", "emergencyContactName", "emergencyContactPhone"],
    "insuranceRequired": True,
    "insuranceBearer": "club",
    "allowParticipantReplacement": True,
    "replacementCutoffHours": 24,
}

FIELD_MAP = {
    "name": "name",
    "phone": "phone",
    "idType": "id_type",
    "idNumber": "id_number",
    "emergencyContactName": "emergency_contact_name",
    "emergencyContactPhone": "emergency_contact_phone",
    "relationToPayer": "relation_to_payer",
    "notes": "notes",
}

@dataclass
class ParticipantPolicy:
    data: dict[str, Any]
    def as_dict(self):
        return dict(self.data)

class ParticipantService:
    """ClubOS participant/attendee domain.

    A registration is the payer/order record. Participants are the people who actually
    attend. One payer can buy seats for multiple people. Participant PII belongs to the
    activity business domain, not Medusa. Production deployments should encrypt identity
    fields at rest and enforce auth/field masking; this demo keeps SQLite plain for clarity.
    """

    def from_activity(self, activity: dict[str, Any]) -> ParticipantPolicy:
        raw = activity.get("participant_form_policy_json")
        data = dict(DEFAULT_POLICY)
        if raw:
            try:
                data.update(json.loads(raw) if isinstance(raw, str) else dict(raw))
            except Exception:
                pass
        data["maxParticipantsPerOrder"] = max(1, min(50, int(data.get("maxParticipantsPerOrder") or 8)))
        data["replacementCutoffHours"] = max(0, int(data.get("replacementCutoffHours") or 0))
        data["allowIncompleteAtCheckout"] = bool(data.get("allowIncompleteAtCheckout", True))
        data["insuranceRequired"] = bool(data.get("insuranceRequired", True))
        data["insuranceBearer"] = data.get("insuranceBearer") if data.get("insuranceBearer") in ("club", "customer") else "club"
        data["allowParticipantReplacement"] = bool(data.get("allowParticipantReplacement", True))
        data["requiredAtCheckout"] = list(data.get("requiredAtCheckout") or ["name", "phone"])
        data["requiredBeforeDeparture"] = list(data.get("requiredBeforeDeparture") or data["requiredAtCheckout"])
        return ParticipantPolicy(data)

    def persist(self, c, *, activity_id: int, club_id: int, payload: dict[str, Any]) -> ParticipantPolicy:
        activity = c.execute("SELECT * FROM activities WHERE id=? AND club_id=?", (activity_id, club_id)).fetchone()
        if not activity:
            raise LookupError("活动不存在")
        current = self.from_activity(dict(activity)).as_dict()
        allowed = set(DEFAULT_POLICY.keys())
        for k, v in payload.items():
            if k in allowed:
                current[k] = v
        policy = self.from_activity({"participant_form_policy_json": json.dumps(current, ensure_ascii=False)})
        c.execute("UPDATE activities SET participant_form_policy_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?",
                  (json.dumps(policy.as_dict(), ensure_ascii=False), activity_id, club_id))
        return policy

    def normalize_for_checkout(self, *, activity: dict[str, Any], payer_name: str, payer_phone: str,
                               participants: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], ParticipantPolicy]:
        policy = self.from_activity(activity)
        source = list(participants or [])
        if not source:
            source = [{"name": payer_name or "报名人", "phone": payer_phone or "", "relationToPayer": "本人"}]
        if len(source) > int(policy.data["maxParticipantsPerOrder"]):
            raise ValueError(f"单笔最多报名 {policy.data['maxParticipantsPerOrder']} 人")
        normalized = []
        for idx, p in enumerate(source, 1):
            x = {k: str(p.get(k) or "").strip() for k in FIELD_MAP.keys()}
            if not x["relationToPayer"]:
                x["relationToPayer"] = "本人" if idx == 1 and x["phone"] == str(payer_phone or "") else "同行人"
            required_now = policy.data["requiredAtCheckout"] if policy.data.get("allowIncompleteAtCheckout", True) else policy.data["requiredBeforeDeparture"]
            missing_checkout = [f for f in required_now if not str(x.get(f) or "").strip()]
            if missing_checkout:
                raise ValueError(f"第{idx}位参加人缺少报名必填资料：{', '.join(missing_checkout)}")
            x["formStatus"] = self._form_status(x, policy.data)
            normalized.append(x)
        return normalized, policy

    def _form_status(self, participant: dict[str, Any], policy: dict[str, Any]) -> str:
        missing = [f for f in policy.get("requiredBeforeDeparture", []) if not str(participant.get(f) or "").strip()]
        return "complete" if not missing else "incomplete"

    def create_for_registration(self, c, *, registration_id: int, activity: dict[str, Any], occurrence: dict[str, Any],
                                participants: list[dict[str, Any]], payer_user_id: int, actor: str = "checkout") -> list[int]:
        policy = self.from_activity(activity).data
        participants=list(participants or [])
        if not participants:
            u=c.execute("SELECT name,phone FROM users WHERE id=?",(payer_user_id,)).fetchone()
            participants=[{"name":u["name"] if u else "报名人","phone":u["phone"] if u else "","relationToPayer":"本人"}]
        ids = []
        for p in participants:
            linked = None
            if p.get("phone"):
                u = c.execute("SELECT id FROM users WHERE phone=?", (p["phone"],)).fetchone()
                if u:
                    linked = int(u[0])
            insurance_status = "pending" if policy.get("insuranceRequired") else "not_required"
            c.execute('''INSERT INTO registration_participants(
                registration_id,activity_id,occurrence_id,club_id,payer_user_id,linked_user_id,
                name,phone,relation_to_payer,id_type,id_number,emergency_contact_name,emergency_contact_phone,
                notes,form_status,insurance_status,status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                registration_id, activity["id"], occurrence["id"], activity["club_id"], payer_user_id, linked,
                p.get("name", ""), p.get("phone", ""), p.get("relationToPayer", ""), p.get("idType", ""), p.get("idNumber", ""),
                p.get("emergencyContactName", ""), p.get("emergencyContactPhone", ""), p.get("notes", ""),
                self._form_status(p, policy), insurance_status, "active"))
            pid = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
            ids.append(pid)
            self._log(c, participant_id=pid, registration_id=registration_id, action="create", before=None,
                      after=self.get(c, pid), actor_type=actor, note="报名生成参加人")
        return ids

    def get(self, c, participant_id: int) -> dict[str, Any] | None:
        r = c.execute("SELECT * FROM registration_participants WHERE id=?", (participant_id,)).fetchone()
        return dict(r) if r else None

    def list_for_registration(self, c, registration_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute('''SELECT p.*,
            a.original_amount allocated_original_amount,a.cash_paid allocated_cash_paid,
            a.club_points_used allocated_club_points_used,a.club_point_discount allocated_club_point_discount,
            a.gear_points_used allocated_gear_points_used,a.platform_point_subsidy allocated_platform_point_subsidy,
            a.club_benefit_discount allocated_club_benefit_discount,a.platform_benefit_subsidy allocated_platform_benefit_subsidy,
            a.club_points_earned allocated_club_points_earned,a.refund_cash_amount participant_refund_cash,
            a.retained_cash_amount participant_retained_cash
            FROM registration_participants p LEFT JOIN participant_financial_allocations a ON a.participant_id=p.id
            WHERE p.registration_id=? ORDER BY p.id''', (registration_id,)).fetchall()]

    def list_for_occurrence(self, c, *, club_id: int, occurrence_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute('''SELECT p.*,r.status registration_status,u.name payer_name,u.phone payer_phone
            FROM registration_participants p JOIN registrations r ON r.id=p.registration_id
            JOIN users u ON u.id=r.user_id
            WHERE p.club_id=? AND p.occurrence_id=? AND p.status='active' AND r.status='paid'
            ORDER BY p.id''', (club_id, occurrence_id)).fetchall()]

    def update(self, c, *, participant_id: int, payload: dict[str, Any], actor_type: str = "c_end", note: str = "补充/修改报名资料") -> dict[str, Any]:
        before = self.get(c, participant_id)
        if not before:
            raise LookupError("参加人不存在")
        if before["status"] != "active":
            raise ValueError("当前参加人状态不可修改")
        activity = c.execute("SELECT * FROM activities WHERE id=?", (before["activity_id"],)).fetchone()
        policy = self.from_activity(dict(activity)).data if activity else DEFAULT_POLICY
        sets, vals = [], []
        merged = {
            "name": before.get("name") or "", "phone": before.get("phone") or "",
            "relationToPayer": before.get("relation_to_payer") or "", "idType": before.get("id_type") or "",
            "idNumber": before.get("id_number") or "", "emergencyContactName": before.get("emergency_contact_name") or "",
            "emergencyContactPhone": before.get("emergency_contact_phone") or "", "notes": before.get("notes") or "",
        }
        for api_key, col in FIELD_MAP.items():
            if api_key in payload:
                val = str(payload.get(api_key) or "").strip()
                merged[api_key] = val
                sets.append(f"{col}=?"); vals.append(val)
        status = self._form_status(merged, policy)
        sets.append("form_status=?"); vals.append(status)
        sets.append("updated_at=CURRENT_TIMESTAMP")
        vals.append(participant_id)
        c.execute(f"UPDATE registration_participants SET {', '.join(sets)} WHERE id=?", vals)
        after = self.get(c, participant_id)
        self._log(c, participant_id=participant_id, registration_id=int(before["registration_id"]), action="update",
                  before=before, after=after, actor_type=actor_type, note=note)
        return after

    def replace(self, c, *, participant_id: int, payload: dict[str, Any], actor_type: str = "c_end", reason: str = "转名额/更换参加人") -> dict[str, Any]:
        before = self.get(c, participant_id)
        if not before:
            raise LookupError("参加人不存在")
        if before["status"] != "active":
            raise ValueError("当前参加人状态不可替换")
        reg = c.execute("SELECT * FROM registrations WHERE id=?", (before["registration_id"],)).fetchone()
        if not reg or reg["status"] != "paid":
            raise ValueError("当前订单状态不可转名额")
        activity = c.execute("SELECT * FROM activities WHERE id=?", (before["activity_id"],)).fetchone()
        occurrence = c.execute("SELECT * FROM activity_occurrences WHERE id=?", (before["occurrence_id"],)).fetchone()
        policy = self.from_activity(dict(activity)).data if activity else DEFAULT_POLICY
        if not policy.get("allowParticipantReplacement"):
            raise ValueError("本活动不允许更换参加人")
        if occurrence and occurrence["start_at"]:
            try:
                start = datetime.fromisoformat(str(occurrence["start_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
                hours = (start - datetime.now()).total_seconds() / 3600
                if hours < float(policy.get("replacementCutoffHours") or 0):
                    raise ValueError(f"距离出发不足 {policy.get('replacementCutoffHours')} 小时，不支持自助转名额")
            except ValueError as e:
                if "不支持自助转名额" in str(e):
                    raise
        normalized, _ = self.normalize_for_checkout(activity=dict(activity), payer_name=str(payload.get("name") or ""), payer_phone=str(payload.get("phone") or ""), participants=[payload])
        p = normalized[0]
        c.execute('''UPDATE registration_participants SET name=?,phone=?,relation_to_payer=?,id_type=?,id_number=?,
                     emergency_contact_name=?,emergency_contact_phone=?,notes=?,form_status=?,insurance_status=?,
                     insurance_provider=NULL,insurance_policy_no=NULL,insured_at=NULL,replacement_count=replacement_count+1,updated_at=CURRENT_TIMESTAMP
                     WHERE id=?''', (
            p["name"], p["phone"], p["relationToPayer"], p["idType"], p["idNumber"], p["emergencyContactName"],
            p["emergencyContactPhone"], p["notes"], p["formStatus"], "pending" if policy.get("insuranceRequired") else "not_required", participant_id))
        after = self.get(c, participant_id)
        self._log(c, participant_id=participant_id, registration_id=int(before["registration_id"]), action="replace",
                  before=before, after=after, actor_type=actor_type, note=reason)
        return after

    @staticmethod
    def _split_int(total: int, n: int) -> list[int]:
        total=max(0,int(total or 0)); n=max(1,int(n)); q,r=divmod(total,n)
        return [q+(1 if i<r else 0) for i in range(n)]

    @classmethod
    def _split_money(cls, total: float, n: int) -> list[float]:
        cents=max(0,int(round(float(total or 0)*100)))
        return [x/100 for x in cls._split_int(cents,n)]

    def create_financial_allocations(self, c, *, registration_id: int, participant_ids: list[int],
                                     registration: dict[str, Any], club_points_used: int=0,
                                     gear_points_used: int=0, club_points_earned: int=0):
        ids=list(participant_ids or [])
        if not ids: return []
        if c.execute('SELECT COUNT(*) FROM participant_financial_allocations WHERE registration_id=?',(registration_id,)).fetchone()[0]:
            return [dict(r) for r in c.execute('SELECT * FROM participant_financial_allocations WHERE registration_id=? ORDER BY participant_id',(registration_id,)).fetchall()]
        n=len(ids)
        parts={
            'original':self._split_money(registration.get('original_amount'),n),
            'cash':self._split_money(registration.get('amount'),n),
            'cp':self._split_int(club_points_used,n),
            'cpd':self._split_money(registration.get('club_point_discount'),n),
            'gp':self._split_int(gear_points_used,n),
            'gps':self._split_money(registration.get('platform_point_subsidy'),n),
            'cbd':self._split_money(registration.get('club_benefit_discount'),n),
            'pbs':self._split_money(registration.get('platform_benefit_subsidy'),n),
            'earned':self._split_int(club_points_earned,n),
        }
        for i,pid in enumerate(ids):
            c.execute('''INSERT OR IGNORE INTO participant_financial_allocations(
              registration_id,participant_id,original_amount,cash_paid,club_points_used,club_point_discount,
              gear_points_used,platform_point_subsidy,club_benefit_discount,platform_benefit_subsidy,club_points_earned)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(registration_id,pid,parts['original'][i],parts['cash'][i],parts['cp'][i],parts['cpd'][i],parts['gp'][i],parts['gps'][i],parts['cbd'][i],parts['pbs'][i],parts['earned'][i]))
        return [dict(r) for r in c.execute('SELECT * FROM participant_financial_allocations WHERE registration_id=? ORDER BY participant_id',(registration_id,)).fetchall()]

    def ensure_financial_allocations(self, c, registration_id: int):
        exists=c.execute('SELECT COUNT(*) FROM participant_financial_allocations WHERE registration_id=?',(registration_id,)).fetchone()[0]
        if exists: return
        reg=c.execute('SELECT * FROM registrations WHERE id=?',(registration_id,)).fetchone()
        if not reg: raise LookupError('报名记录不存在')
        ps=c.execute('SELECT id FROM registration_participants WHERE registration_id=? ORDER BY id',(registration_id,)).fetchall()
        if not ps: return
        cp=c.execute('SELECT COALESCE(SUM(points_used),0) FROM point_redemptions WHERE order_kind="activity" AND order_id=? AND point_type="club"',(registration_id,)).fetchone()[0]
        gp=c.execute('SELECT COALESCE(SUM(points_used),0) FROM point_redemptions WHERE order_kind="activity" AND order_id=? AND point_type="gear"',(registration_id,)).fetchone()[0]
        earned=c.execute('SELECT COALESCE(SUM(amount),0) FROM club_point_ledger WHERE source_type="activity_registration" AND source_id=? AND type="earn"',(str(registration_id),)).fetchone()[0]
        self.create_financial_allocations(c,registration_id=registration_id,participant_ids=[int(x['id']) for x in ps],registration=dict(reg),club_points_used=int(cp or 0),gear_points_used=int(gp or 0),club_points_earned=int(earned or 0))

    def financial_allocation(self, c, participant_id: int) -> dict[str, Any] | None:
        p=c.execute('SELECT registration_id FROM registration_participants WHERE id=?',(participant_id,)).fetchone()
        if not p: return None
        self.ensure_financial_allocations(c,int(p['registration_id']))
        r=c.execute('SELECT * FROM participant_financial_allocations WHERE participant_id=?',(participant_id,)).fetchone()
        return dict(r) if r else None

    def update_insurance(self, c, *, participant_id: int, club_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        before = self.get(c, participant_id)
        if not before or int(before["club_id"]) != int(club_id):
            raise LookupError("参加人不存在")
        status = str(payload.get("status") or "insured")
        if status not in {"pending", "submitted", "insured", "enrolling", "cancelling", "cancelled", "cancel_failed", "failed", "not_required"}:
            raise ValueError("无效保险状态")
        provider = str(payload.get("provider") or "").strip() or None
        policy_no = str(payload.get("policyNo") or "").strip() or None
        c.execute('''UPDATE registration_participants SET insurance_status=?,insurance_provider=?,insurance_policy_no=?,
                     insured_at=CASE WHEN ?='insured' THEN CURRENT_TIMESTAMP ELSE insured_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (status, provider, policy_no, status, participant_id))
        after = self.get(c, participant_id)
        self._log(c, participant_id=participant_id, registration_id=int(before["registration_id"]), action="insurance_update",
                  before=before, after=after, actor_type=f"club:{club_id}", note="保险状态更新")
        return after

    def _log(self, c, *, participant_id: int, registration_id: int, action: str, before: dict[str, Any] | None,
             after: dict[str, Any] | None, actor_type: str, note: str):
        c.execute('''INSERT INTO participant_change_logs(participant_id,registration_id,action,before_json,after_json,actor_type,note)
                     VALUES(?,?,?,?,?,?,?)''', (participant_id, registration_id, action,
                     json.dumps(before, ensure_ascii=False, default=str) if before else None,
                     json.dumps(after, ensure_ascii=False, default=str) if after else None,
                     actor_type, note))

    def summary_for_registration(self, c, registration_id: int) -> dict[str, Any]:
        participants = self.list_for_registration(c, registration_id)
        active = [p for p in participants if p.get("status") == "active"]
        refunded = [p for p in participants if p.get("status") == "refunded"]
        return {
            "participantCount": len(active),
            "originalParticipantCount": len(participants),
            "refundedParticipantCount": len(refunded),
            "completeCount": sum(1 for p in active if p.get("form_status") == "complete"),
            "insurancePendingCount": sum(1 for p in active if p.get("insurance_status") in ("pending", "submitted", "failed")),
            "participants": participants,
        }
