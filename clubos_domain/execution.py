from __future__ import annotations
import json
from typing import Any

EXECUTION_STATES = ["preparing", "departed", "in_progress", "completed"]
CHECKIN_STATES = {"pending", "checked_in", "no_show", "cancelled"}
GROUP_TYPES = {"vehicle", "leader", "room", "custom"}


class ActivityExecutionService:
    """Occurrence-level execution domain for ClubOS.

    Commerce owns payment/order primitives. ClubOS owns what happens after a person has
    successfully registered: departure readiness, insurance, groups, notices, attendance,
    leaders and the execution lifecycle. Everything is scoped to club + occurrence.
    """

    def _occurrence(self, c, *, club_id: int, occurrence_id: int) -> dict[str, Any]:
        r = c.execute('''SELECT o.*,a.title activity_title,a.location activity_location,a.participant_form_policy_json
                         FROM activity_occurrences o JOIN activities a ON a.id=o.activity_id
                         WHERE o.id=? AND o.club_id=?''', (occurrence_id, club_id)).fetchone()
        if not r:
            raise LookupError("团期不存在")
        return dict(r)

    def list_occurrences(self, c, *, club_id: int) -> list[dict[str, Any]]:
        rs = c.execute('''SELECT o.*,a.title activity_title,a.location activity_location,
            (SELECT COUNT(*) FROM registration_participants p JOIN registrations r ON r.id=p.registration_id
             WHERE p.occurrence_id=o.id AND p.status='active' AND r.status='paid') named_participants,
            (SELECT COUNT(*) FROM registration_participants p JOIN registrations r ON r.id=p.registration_id
             WHERE p.occurrence_id=o.id AND p.status='active' AND r.status='paid' AND p.form_status!='complete') incomplete_participants,
            (SELECT COUNT(*) FROM registration_participants p JOIN registrations r ON r.id=p.registration_id
             WHERE p.occurrence_id=o.id AND p.status='active' AND r.status='paid' AND p.insurance_status IN ('pending','submitted','failed')) insurance_pending,
            (SELECT COUNT(*) FROM participant_checkins ci WHERE ci.occurrence_id=o.id AND ci.checkin_type='departure' AND ci.status='checked_in') checked_in
            FROM activity_occurrences o JOIN activities a ON a.id=o.activity_id
            WHERE o.club_id=? ORDER BY o.start_at''', (club_id,)).fetchall()
        return [dict(r) for r in rs]

    def get_settings(self, c, *, club_id: int, occurrence_id: int) -> dict[str, Any]:
        self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        r = c.execute("SELECT * FROM occurrence_execution_settings WHERE occurrence_id=?", (occurrence_id,)).fetchone()
        if r:
            return dict(r)
        c.execute('''INSERT INTO occurrence_execution_settings(occurrence_id,club_id) VALUES(?,?)''', (occurrence_id, club_id))
        return dict(c.execute("SELECT * FROM occurrence_execution_settings WHERE occurrence_id=?", (occurrence_id,)).fetchone())

    def update_settings(self, c, *, club_id: int, occurrence_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_settings(c, club_id=club_id, occurrence_id=occurrence_id)
        mapping = {
            "meetingTime": "meeting_time", "meetingLocation": "meeting_location",
            "emergencyPhone": "emergency_phone", "leaderNote": "leader_note",
        }
        sets, vals = [], []
        for api_key, col in mapping.items():
            if api_key in payload:
                sets.append(f"{col}=?")
                vals.append(str(payload.get(api_key) or "").strip() or None)
        if sets:
            vals.append(occurrence_id)
            c.execute(f"UPDATE occurrence_execution_settings SET {', '.join(sets)},updated_at=CURRENT_TIMESTAMP WHERE occurrence_id=?", vals)
            self._log(c, occurrence_id, "settings_updated", f"club:{club_id}", payload)
        return self.get_settings(c, club_id=club_id, occurrence_id=occurrence_id)

    def set_status(self, c, *, club_id: int, occurrence_id: int, status: str, actor: str, note: str = "") -> dict[str, Any]:
        occ = self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        status = str(status or "").strip()
        if status not in EXECUTION_STATES:
            raise ValueError("无效执行状态")
        current = str(occ.get("execution_status") or "preparing")
        if current == status:
            return {"ok": True, "status": status, "idempotent": True}
        if current == "completed":
            raise ValueError("活动已完成，不能回退执行状态")
        if EXECUTION_STATES.index(status) < EXECUTION_STATES.index(current):
            raise ValueError("执行状态不能回退")
        c.execute("UPDATE activity_occurrences SET execution_status=?,execution_updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?",
                  (status, occurrence_id, club_id))
        self._log(c, occurrence_id, "execution_status", actor, {"from": current, "to": status, "note": note})
        return {"ok": True, "status": status, "previousStatus": current}

    def add_leader(self, c, *, club_id: int, occurrence_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        raw_id = payload.get("leaderId")
        leader_id = int(raw_id) if raw_id not in (None, "") else None
        name = str(payload.get("name") or "").strip()
        phone = str(payload.get("phone") or "").strip() or None
        role = str(payload.get("role") or "领队").strip() or "领队"
        if leader_id:
            # 从领队资源库挑人：姓名/电话以名册为准，否则同一个人每次手打都会变成"新领队"，
            # 历史带队记录就再也对不上，自动推荐也就无从谈起。
            roster = c.execute("SELECT * FROM club_leaders WHERE id=? AND club_id=?",
                               (leader_id, club_id)).fetchone()
            if not roster:
                raise LookupError("领队资源库里没有这个人")
            if c.execute("SELECT id FROM occurrence_leaders WHERE occurrence_id=? AND leader_id=?",
                         (occurrence_id, leader_id)).fetchone():
                raise ValueError("这位领队已经安排在本团期了")
            name = name or str(roster["name"] or "").strip()
            phone = phone or (str(roster["phone"]).strip() or None if roster["phone"] else None)
            role = str(payload.get("role") or roster["role"] or role).strip() or role
        if not name:
            raise ValueError("领队姓名不能为空")
        c.execute('''INSERT INTO occurrence_leaders(occurrence_id,club_id,leader_id,name,phone,role,note)
                     VALUES(?,?,?,?,?,?,?)''', (occurrence_id, club_id, leader_id, name, phone, role,
                     str(payload.get("note") or "").strip() or None))
        lid = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        self._log(c, occurrence_id, "leader_added", f"club:{club_id}",
                  {"leaderId": lid, "rosterId": leader_id, "name": name})
        return dict(c.execute("SELECT * FROM occurrence_leaders WHERE id=?", (lid,)).fetchone())

    def remove_leader(self, c, *, club_id: int, occurrence_id: int, assignment_id: int) -> dict[str, Any]:
        """把某位领队从这一场撤下来。撤下即不再计入该人的带队历史，但事件日志留痕。"""
        self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        found = c.execute("SELECT id,name FROM occurrence_leaders WHERE id=? AND occurrence_id=? AND club_id=?",
                          (assignment_id, occurrence_id, club_id)).fetchone()
        if not found:
            raise LookupError("这条领队安排不存在")
        c.execute("DELETE FROM occurrence_leaders WHERE id=?", (assignment_id,))
        self._log(c, occurrence_id, "leader_removed", f"club:{club_id}",
                  {"assignmentId": assignment_id, "name": found["name"]})
        return {"ok": True, "removed": assignment_id}

    def list_leaders(self, c, occurrence_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute("SELECT * FROM occurrence_leaders WHERE occurrence_id=? ORDER BY id", (occurrence_id,)).fetchall()]

    def add_group(self, c, *, club_id: int, occurrence_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        group_type = str(payload.get("groupType") or "vehicle").strip()
        if group_type not in GROUP_TYPES:
            raise ValueError("无效分组类型")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("分组名称不能为空")
        capacity = payload.get("capacity")
        capacity = max(0, int(capacity)) if capacity not in (None, "") else None
        c.execute('''INSERT INTO execution_groups(occurrence_id,club_id,group_type,name,leader_name,leader_phone,capacity,note)
                     VALUES(?,?,?,?,?,?,?,?)''', (occurrence_id, club_id, group_type, name,
                     str(payload.get("leaderName") or "").strip() or None, str(payload.get("leaderPhone") or "").strip() or None,
                     capacity, str(payload.get("note") or "").strip() or None))
        gid = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        self._log(c, occurrence_id, "group_added", f"club:{club_id}", {"groupId": gid, "groupType": group_type, "name": name})
        return dict(c.execute("SELECT * FROM execution_groups WHERE id=?", (gid,)).fetchone())

    def list_groups(self, c, occurrence_id: int) -> list[dict[str, Any]]:
        groups = [dict(r) for r in c.execute('''SELECT g.*,
            (SELECT COUNT(*) FROM participant_group_assignments a WHERE a.group_id=g.id) assigned_count
            FROM execution_groups g WHERE g.occurrence_id=? ORDER BY g.group_type,g.id''', (occurrence_id,)).fetchall()]
        return groups

    def assign_group(self, c, *, club_id: int, group_id: int, participant_id: int) -> dict[str, Any]:
        g = c.execute("SELECT * FROM execution_groups WHERE id=? AND club_id=?", (group_id, club_id)).fetchone()
        if not g:
            raise LookupError("分组不存在")
        g = dict(g)
        p = c.execute("SELECT * FROM registration_participants WHERE id=? AND club_id=? AND occurrence_id=? AND status='active'",
                      (participant_id, club_id, g["occurrence_id"])).fetchone()
        if not p:
            raise LookupError("参加人不存在或不属于该团期")
        if g.get("capacity"):
            used = c.execute("SELECT COUNT(*) FROM participant_group_assignments WHERE group_id=?", (group_id,)).fetchone()[0]
            existing = c.execute("SELECT id FROM participant_group_assignments WHERE participant_id=? AND group_type=?", (participant_id, g["group_type"])).fetchone()
            if not existing and int(used) >= int(g["capacity"]):
                raise ValueError("该分组已满")
        c.execute("DELETE FROM participant_group_assignments WHERE participant_id=? AND group_type=?", (participant_id, g["group_type"]))
        c.execute('''INSERT INTO participant_group_assignments(occurrence_id,participant_id,group_id,group_type)
                     VALUES(?,?,?,?)''', (g["occurrence_id"], participant_id, group_id, g["group_type"]))
        self._log(c, int(g["occurrence_id"]), "participant_group_assigned", f"club:{club_id}",
                  {"participantId": participant_id, "groupId": group_id, "groupType": g["group_type"]})
        return {"ok": True, "participantId": participant_id, "groupId": group_id, "groupType": g["group_type"]}

    def create_notice(self, c, *, club_id: int, occurrence_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        title = str(payload.get("title") or "活动通知").strip()
        content = str(payload.get("content") or "").strip()
        if not content:
            raise ValueError("通知内容不能为空")
        channel = str(payload.get("channel") or "manual").strip()
        audience = str(payload.get("audience") or "all").strip()
        c.execute('''INSERT INTO activity_notices(occurrence_id,club_id,title,content,audience,channel,status)
                     VALUES(?,?,?,?,?,?,?)''', (occurrence_id, club_id, title, content, audience, channel, "draft"))
        nid = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        return dict(c.execute("SELECT * FROM activity_notices WHERE id=?", (nid,)).fetchone())

    def send_notice(self, c, *, club_id: int, occurrence_id: int, notice_id: int, actor: str) -> dict[str, Any]:
        n = c.execute("SELECT * FROM activity_notices WHERE id=? AND occurrence_id=? AND club_id=?", (notice_id, occurrence_id, club_id)).fetchone()
        if not n:
            raise LookupError("通知不存在")
        if n["status"] == "sent":
            return dict(n)
        # v0.13 records outbound intent/status only. SMS/WeChat adapters plug in later.
        c.execute("UPDATE activity_notices SET status='sent',sent_at=CURRENT_TIMESTAMP WHERE id=?", (notice_id,))
        self._log(c, occurrence_id, "notice_sent", actor, {"noticeId": notice_id, "channel": n["channel"]})
        return dict(c.execute("SELECT * FROM activity_notices WHERE id=?", (notice_id,)).fetchone())

    def list_notices(self, c, occurrence_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute("SELECT * FROM activity_notices WHERE occurrence_id=? ORDER BY id DESC", (occurrence_id,)).fetchall()]

    def checkin(self, c, *, club_id: int, occurrence_id: int, participant_id: int, status: str, actor: str, note: str = "") -> dict[str, Any]:
        self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        status = str(status or "checked_in")
        if status not in CHECKIN_STATES:
            raise ValueError("无效签到状态")
        p = c.execute('''SELECT p.* FROM registration_participants p JOIN registrations r ON r.id=p.registration_id
                         WHERE p.id=? AND p.club_id=? AND p.occurrence_id=? AND p.status='active' AND r.status='paid' ''',
                      (participant_id, club_id, occurrence_id)).fetchone()
        if not p:
            raise LookupError("参加人不存在")
        c.execute('''INSERT INTO participant_checkins(participant_id,occurrence_id,checkin_type,status,checked_at,note,actor_type)
                     VALUES(?,?,'departure',?,CASE WHEN ?='pending' THEN NULL ELSE CURRENT_TIMESTAMP END,?,?)
                     ON CONFLICT(participant_id,checkin_type) DO UPDATE SET status=excluded.status,
                     checked_at=excluded.checked_at,note=excluded.note,actor_type=excluded.actor_type''',
                  (participant_id, occurrence_id, status, status, note or None, actor))
        self._log(c, occurrence_id, "checkin", actor, {"participantId": participant_id, "status": status, "note": note})
        return dict(c.execute("SELECT * FROM participant_checkins WHERE participant_id=? AND checkin_type='departure'", (participant_id,)).fetchone())

    def batch_insurance_submit(self, c, *, club_id: int, occurrence_id: int, participant_ids: list[int], provider: str = "") -> dict[str, Any]:
        self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        ids = [int(x) for x in participant_ids]
        if not ids:
            ids = [int(r[0]) for r in c.execute('''SELECT p.id FROM registration_participants p JOIN registrations r ON r.id=p.registration_id
                 WHERE p.club_id=? AND p.occurrence_id=? AND p.status='active' AND r.status='paid' AND p.insurance_status IN ('pending','failed')''',
                 (club_id, occurrence_id)).fetchall()]
        updated = 0
        for pid in ids:
            p = c.execute("SELECT id FROM registration_participants WHERE id=? AND club_id=? AND occurrence_id=? AND status='active'", (pid, club_id, occurrence_id)).fetchone()
            if not p:
                continue
            c.execute('''UPDATE registration_participants SET insurance_status='submitted',insurance_provider=COALESCE(?,insurance_provider),updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                      (provider or None, pid))
            updated += 1
        self._log(c, occurrence_id, "insurance_batch_submitted", f"club:{club_id}", {"count": updated, "provider": provider})
        return {"ok": True, "updated": updated}

    def dashboard(self, c, *, club_id: int, occurrence_id: int) -> dict[str, Any]:
        occ = self._occurrence(c, club_id=club_id, occurrence_id=occurrence_id)
        settings = self.get_settings(c, club_id=club_id, occurrence_id=occurrence_id)
        participants = [dict(r) for r in c.execute('''SELECT p.*,u.name payer_name,u.phone payer_phone,
            COALESCE(ci.status,'pending') checkin_status,
            (SELECT g.name FROM participant_group_assignments pa JOIN execution_groups g ON g.id=pa.group_id WHERE pa.participant_id=p.id AND pa.group_type='vehicle' LIMIT 1) vehicle_group,
            (SELECT g.name FROM participant_group_assignments pa JOIN execution_groups g ON g.id=pa.group_id WHERE pa.participant_id=p.id AND pa.group_type='leader' LIMIT 1) leader_group
            FROM registration_participants p JOIN registrations r ON r.id=p.registration_id JOIN users u ON u.id=r.user_id
            LEFT JOIN participant_checkins ci ON ci.participant_id=p.id AND ci.checkin_type='departure'
            WHERE p.club_id=? AND p.occurrence_id=? AND p.status='active' AND r.status='paid' ORDER BY p.id''',
            (club_id, occurrence_id)).fetchall()]
        total = len(participants)
        incomplete = sum(1 for p in participants if p.get("form_status") != "complete")
        insurance_pending = sum(1 for p in participants if p.get("insurance_status") in {"pending", "submitted", "failed"})
        checked_in = sum(1 for p in participants if p.get("checkin_status") == "checked_in")
        no_show = sum(1 for p in participants if p.get("checkin_status") == "no_show")
        unassigned_vehicle = sum(1 for p in participants if not p.get("vehicle_group"))
        pending_refunds = c.execute('''SELECT COUNT(*) FROM registrations r WHERE r.occurrence_id=? AND r.status='paid' AND r.refund_status IN ('requested','processing')''', (occurrence_id,)).fetchone()[0]
        issues = []
        if int(occ.get("sold") or 0) != total:
            issues.append(f"已售名额 {int(occ.get('sold') or 0)}，当前实名参加人 {total}，请核对历史/导入数据")
        if incomplete:
            issues.append(f"{incomplete} 人报名资料待补")
        if insurance_pending:
            issues.append(f"{insurance_pending} 人保险待处理")
        if pending_refunds:
            issues.append(f"{pending_refunds} 笔退款处理中")
        if total and unassigned_vehicle and self.list_groups(c, occurrence_id):
            vehicle_groups = [g for g in self.list_groups(c, occurrence_id) if g.get("group_type") == "vehicle"]
            if vehicle_groups:
                issues.append(f"{unassigned_vehicle} 人尚未分配车辆")
        return {
            "occurrence": occ,
            "settings": settings,
            "summary": {
                "sold": int(occ.get("sold") or 0), "participantCount": total, "incompleteCount": incomplete,
                "insurancePendingCount": insurance_pending, "checkedInCount": checked_in, "noShowCount": no_show,
                "unassignedVehicleCount": unassigned_vehicle, "pendingRefundCount": int(pending_refunds),
                "isReady": len(issues) == 0,
            },
            "issues": issues,
            "leaders": self.list_leaders(c, occurrence_id),
            "groups": self.list_groups(c, occurrence_id),
            "notices": self.list_notices(c, occurrence_id),
            "participants": participants,
        }

    def leader_brief(self, c, *, club_id: int, occurrence_id: int) -> dict[str, Any]:
        d = self.dashboard(c, club_id=club_id, occurrence_id=occurrence_id)
        # Same domain data, deliberately excludes commerce/member economics.
        return {
            "occurrence": d["occurrence"], "settings": d["settings"], "summary": d["summary"],
            "issues": d["issues"], "leaders": d["leaders"], "groups": d["groups"],
            "notices": [n for n in d["notices"] if n.get("status") == "sent"], "participants": d["participants"],
        }

    def _log(self, c, occurrence_id: int, event_type: str, actor_type: str, payload: dict[str, Any] | None = None):
        c.execute('''INSERT INTO execution_event_logs(occurrence_id,event_type,actor_type,payload_json)
                     VALUES(?,?,?,?)''', (occurrence_id, event_type, actor_type, json.dumps(payload or {}, ensure_ascii=False, default=str)))
