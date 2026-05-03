# Conversation Logs — Morph Discord Bot

---

## Session: 2026-05-03 (ช่วงบ่าย) — Bug Fixes + Cleanup + New Features

### สิ่งที่แก้ไข / เพิ่ม

#### 1. ลบ guild_licenses และ license_tokens (ไม่ได้ใช้)
ลบออกจาก 4 ไฟล์: `supabase_schema.sql`, `db.py`, `db_pg.py`, `api.py`
- ลบ table definitions, functions ทั้งหมด, API endpoints `/api/licenses`
- ปรับ `get_guilds_overview` ให้ไม่ query `guild_licenses` แล้ว
- รวม 407 บรรทัด

#### 2. แก้ Bug — 2 คน Join พร้อมกัน ได้ Trial แค่คนเดียว
**Root cause:** Handler แรก complete + update cache ก่อน Handler ที่สอง snapshot
Handler ที่สองเห็น old_cache = current_cache → ไม่ detect change → skip

**Fix:** เพิ่ม `asyncio.Lock` ต่อ guild ใน `on_member_join`
```python
self._join_locks: dict[int, asyncio.Lock] = {}
async with self._join_locks[guild.id]:
    await self._process_member_join(member)
```
Handler ทำงานทีละคนต่อ guild → Handler สองเห็น cache ที่ Handler แรก update แล้ว → detect ได้ถูกต้อง

#### 3. รองรับ Discord Membership Screening (Rules Gate)
เมื่อเปิด Membership Screening ใน Server:
- `on_member_join`: ถ้า `member.pending=True` → เก็บ invite code ใน `_pending_members` → รอก่อน
- `on_member_update`: จับตอน `pending: True → False` (กด Agree Rules) → ให้ Role + บันทึก Trial

รองรับทั้งสองแบบอัตโนมัติ ไม่ต้องตั้งค่าเพิ่ม

### Commits
- `a565286` chore: remove guild_licenses and license_tokens (unused)
- `0087acd` fix: add per-guild asyncio.Lock to prevent race when 2 members join simultaneously
- `5f0c41c` feat: support Discord Membership Screening — hold trial role until member accepts rules

---

## Session: 2026-05-03 (ช่วงเช้า) — Trial Invite Flow Fix + Setup Panel UX

### สิ่งที่แก้ไข / เพิ่ม

#### 1. แก้ Bug หลัก — Trial Invite ไม่สร้าง record ใน Supabase
**Root cause:** Race condition ใน `on_member_join`

`old_cache` อยู่ **หลัง** `await guild.invites()` ทำให้ระหว่างรอ await, `on_invite_delete` ทำงานก่อนและลบ invite ออกจาก cache ไปแล้ว

**Fix:** ย้าย snapshot cache ขึ้นมา **ก่อน** await และใช้ `dict()` copy

#### 2. RoleSelect Dropdown แทน Role ID Text Input
ใน Setup Panel → สร้าง Trial Invite เปลี่ยนจากกรอก Role ID เป็น dropdown เลือก Role

#### 3. Reset Config ลบ Trial Invites ด้วย

#### 4. ปุ่ม ⚡ Force Trial Check ใน Setup Panel
- กด trigger expiry check ทันที (ใช้ทดสอบ auto-kick)
- คำสั่ง `\force_trial_check` ก็เพิ่มด้วย

### Commits
- `7a36633` fix: snapshot invite cache BEFORE await guild.invites()
- `76a5409` feat: add \force_trial_check command
- `3ada2ba` feat: add Force Trial Check button to Setup Panel row 1

### วิธีทดสอบ expiry/kick
1. Supabase SQL Editor: `UPDATE trial_members SET expires_at = NOW() - INTERVAL '1 minute' WHERE discord_id = '...' AND revoked = 0;`
2. กด ⚡ Force Trial Check ใน Setup Panel
