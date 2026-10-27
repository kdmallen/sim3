"""Gardening Tips + F5 reward for the fingerprinted 1.6.11 game only."""
import struct

from unicorn import UC_HOOK_CODE, UcError
from unicorn.arm_const import UC_ARM_REG_R0


class MoneyCheat:
    REWARD = 1000
    LIMIT = 999999  # Original award routine's cap.
    GARDEN_ENTRY = 0x8D9A4
    AWARD = 0xC4B24
    HELP_VTABLE = 0x1E2208
    SIM_VTABLE = 0x1E23B0

    def __init__(self, runtime):
        self.runtime = runtime
        self.help = 0
        self.pending = False
        self.running = False
        self.notice = None
        self.profile = 0
        self.before = 0
        self.awards = 0
        self.total_added = 0
        self.last_balance = None
        signatures = {
            self.GARDEN_ENTRY: bytes.fromhex("0120a0e17c0190e5"),
            0xC1630: bytes.fromhex("900790e51eff2fe1"),
            self.AWARD: bytes.fromhex("10402de90040a0e1903790e56c209fe5"),
        }
        self.enabled = runtime.image.base == 0x4A000000 and all(
            runtime.image.code[offset:offset + len(data)] == data
            for offset, data in signatures.items())
        if self.enabled:
            address = runtime.image.base + self.GARDEN_ENTRY
            runtime.cpu.hook_add(UC_HOOK_CODE, self.enter_gardening,
                                 begin=address, end=address)

    def word(self, address):
        return struct.unpack("<I", self.runtime.cpu.mem_read(address, 4))[0]

    def enter_gardening(self, cpu, address, size, data):
        self.help = cpu.reg_read(UC_ARM_REG_R0)
        self.pending = False

    def active_profile(self):
        if not self.enabled or not self.help:
            return 0
        base = self.runtime.image.base
        try:
            if self.word(self.help) != base + self.HELP_VTABLE:
                return 0
            if self.word(self.help + 0xF8) != 5:
                return 0
            popup = self.word(self.help + 0x17C)
            if not popup or self.word(popup + 0x90) != 0x860:
                return 0
            profile = self.word(self.help + 0x24)
            if not profile or self.word(profile) != base + self.SIM_VTABLE:
                return 0
            if self.word(profile + 0x790) > self.LIMIT:
                return 0
            return profile
        except UcError:
            return 0

    def shake(self):
        if not self.running and self.active_profile():
            self.pending = True

    def start_pending(self):
        r = self.runtime
        if (not self.pending or self.running or r.callback_stack
                or r.threads.current_thread != 4000):
            return False
        self.pending = False
        profile = self.active_profile()  # The popup may have closed since F5.
        if not profile:
            return False
        self.profile = profile
        self.before = self.word(profile + 0x790)
        self.running = True
        # Use the existing guest callback return bridge. Profile is an existing
        # object, never an owned event payload to free after the call.
        r.invoke_callbacks([(r.image.base + self.AWARD, (profile, self.REWARD))],
                           owned_payloads=(), on_complete=self.complete)
        return True

    def complete(self):
        if not self.running:
            raise RuntimeError("Money reward returned without an active call")
        balance = self.word(self.profile + 0x790)
        added = balance - self.before
        self.running = False
        self.profile = 0
        self.awards += 1
        self.total_added += added
        self.last_balance = balance
        self.notice = (added, balance)

    def show_pending_notice(self):
        # Modal native UI runs only outside emu_start, after the guest return
        # has committed. A blocking dialog inside a CPU hook can outlive its slice.
        if self.notice is not None and self.runtime.window is not None:
            notice, self.notice = self.notice, None
            self.runtime.window.show_money_reward(*notice)

    def report(self):
        return {"enabled": self.enabled, "awards": self.awards,
                "total_added": self.total_added, "last_balance": self.last_balance}
