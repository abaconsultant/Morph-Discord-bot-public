# Conversation Logs — Morph Discord Bot

---

## Session: 2026-05-03 — Trial Invite Flow Fix + Setup Panel UX

### สิ่งที่แก้ไข / เพิ่ม

#### 1. แก้ Bug หลัก — Trial Invite ไม่สร้าง record ใน Supabase
**Root cause:** Race condition ใน `on_member_join`

เมื่อสมาชิกใช้ single-use invite, Discord ส่ง 2 events:
1. `GUILD_MEMBER_ADD` → `on_member_join` เริ่มทำงาน
2. `INVITE_DELETE` → `on_invite_delete` ลบ invite ออกจาก cache

ปัญหาคือ `old_cache = self.invite_cache.get(guild.id, {})` อยู่ **หลัง** `await guild.invites()` ทำให้ระหว่างรอ await, `on_invite_delete` ทำงานก่อนและลบ invite ออกจาก cache ไปแล้ว

**Fix:** ย้าย snapshot cache ขึ้นมา **ก่อน** await และใช้ `dict()` copy:
```python
old_cache = dict(self.invite_cache.get(guild.id, {}))  # ก่อน await
current_invites = await guild.invites()
```

#### 2. RoleSelect Dropdown แทน Role ID Text Input
ใน Setup Panel → สร้าง Trial Invite เปลี่ยนจากกรอก Role ID เป็น dropdown เลือก Role:
- `GenInviteRoleView`: View มี RoleSelect + ปุ่มยืนยัน
- `GenInviteDetailsModal`: Modal สำหรับกรอก days + max_uses เท่านั้น
- `@ui.select(cls=ui.RoleSelect, ...)` — syntax ถูกต้องสำหรับ discord.py 2.x

#### 3. Reset Config ลบ Trial Invites ด้วย
เพิ่ม logic ใน `ResetConfirmView.confirm` ให้ deactivate trial invites ทุกอันของ guild ด้วย

#### 4. ปุ่ม ⚡ Force Trial Check ใน Setup Panel
เพิ่มปุ่มใน Row 1 ของ Setup Panel สำหรับ trigger expiry check ทันที (ใช้ทดสอบ auto-kick)
- เรียก `interaction.client.get_cog("TrialsCog").trial_expiry_check()`
- คำสั่ง `\force_trial_check` ก็เพิ่มด้วย

### Commits
- `f3f4b18` fix: copy invite cache snapshot in on_member_join to prevent race with on_invite_delete
- `7a36633` fix: snapshot invite cache BEFORE await guild.invites() to fix race with on_invite_delete (fix จริง)
- `76a5409` feat: add \force_trial_check command for testing expiry/kick
- `3ada2ba` feat: add Force Trial Check button to Setup Panel row 1

### ผลทดสอบ
- ✅ Trial Invite สร้าง record ใน `trial_members` Supabase สำเร็จ
- ✅ `trial_invites.uses` อัปเดตเป็น 1 หลัง join
- ✅ Force Trial Check + แก้ `expires_at` ใน Supabase → auto-kick ทำงาน

### วิธีทดสอบ expiry/kick ในอนาคต
1. Supabase SQL Editor: `UPDATE trial_members SET expires_at = NOW() - INTERVAL '1 minute' WHERE discord_id = '...' AND revoked = 0;`
2. กด ⚡ Force Trial Check ใน Setup Panel
