"""Stateful pointer/keyboard input, including reproducible diagnostic taps."""
import collections
import struct
import time
import math


class InputDevice:
    # Verified game callback consumes SDK arrow keys 9..12 and Back 126.
    SDK_KEYS = {37: 9, 38: 10, 39: 11, 40: 12, 27: 126, 8: 3, 9: 2, 13: 4}

    def __init__(self, runtime, taps=(), clock=time.monotonic):
        self.runtime = runtime
        self.x = self.y = 0
        self.buttons = set()
        self.keys = set()
        self.held_keys = set()
        self.key_edges = {}
        self.pointer_events = collections.deque()
        self.keyboard_events = collections.deque()
        self.device_events = collections.deque()
        self.character_events = collections.deque()
        self.scheduled = collections.defaultdict(list)
        self.events_dispatched = 0
        self.accelerometer_available = False
        self.accelerometer_active = False
        self.clock = clock
        self.shake_started = None
        self.jerk_started = None
        self.wheel_remainder = 0
        self.wheel_steps = 0
        self.zoom_anchor = None
        self.zoom_position = None
        self.zoom_gesture = None
        self.physical_position = (0, 0)
        self.zoom_gestures_completed = 0
        self.zoom_needs_probe = False
        self.zoom_calibrated = False
        self.zoom_probe_hits = 0
        self.zoom_probe_misses = 0
        self.last_zoom_knob = None
        self.last_zoom_drag = None
        for frame, x, y in taps:
            self.scheduled[frame].append((x, y, True))
            self.scheduled[frame + 1].append((x, y, False))

    def motion(self, x, y, virtual=False):
        if not virtual:
            self.physical_position = (int(x), int(y))
            if self.zoom_gesture is not None:
                return
        self.x, self.y = int(x), int(y)
        self.pointer_events.append((1, struct.pack("<2i", self.x, self.y)))

    def button(self, button, pressed, x=None, y=None, virtual=False):
        if not virtual and self.zoom_gesture is not None:
            # A real click takes ownership back from the short wheel gesture.
            self.zoom_gesture = None
            self.button(0, False, virtual=True)
        if not virtual and x is not None:
            self.physical_position = (int(x), int(y))
        if x is not None:
            self.x, self.y = int(x), int(y)
        if pressed:
            self.buttons.add(button)
        else:
            self.buttons.discard(button)
        self.pointer_events.append((0, struct.pack("<4i", button, int(pressed), self.x, self.y)))

    def key(self, key, pressed):
        if pressed:
            self.held_keys.add(key)
        else:
            self.held_keys.discard(key)
        if pressed and key == 116:  # F5: repeatable phone shake.
            self.shake_started = self.clock()
        if pressed and key == 117:  # F6: quick upward jerk for fishing.
            self.jerk_started = self.clock()
        self.keyboard_events.append((int(key), bool(pressed)))

    def wheel(self, delta, x=None, y=None):
        if x is not None:
            self.physical_position = (int(x), int(y))
        # Ctrl+wheel can align the gesture with a differently positioned HUD.
        if 17 in self.held_keys and x is not None:
            anchor = (int(x), int(y))
            if self.zoom_anchor is None or sum(abs(a - b) for a, b in zip(anchor, self.zoom_anchor)) > 8:
                self.zoom_anchor = anchor
                self.zoom_position = int(y)
                self.zoom_calibrated = True
        self.wheel_remainder += int(delta)
        steps = int(self.wheel_remainder / 120)
        self.wheel_remainder -= steps * 120
        self.wheel_steps = max(-20, min(20, self.wheel_steps + steps))
        if steps:
            self.zoom_needs_probe = True

    def observe_zoom_knob(self, position):
        self.zoom_needs_probe = False
        if position is None:
            self.zoom_probe_misses += 1
            if not self.zoom_calibrated:
                self.wheel_steps = 0
            return
        x, y = position
        self.last_zoom_knob = position
        self.zoom_probe_hits += 1
        if not self.zoom_calibrated:
            self.zoom_anchor = (x, round(self.runtime.surface_height * 0.30))
        self.zoom_position = y

    def character(self, codepoint):
        self.character_events.append(int(codepoint))

    def on_frame(self, frame):
        for x, y, pressed in self.scheduled.pop(frame, []):
            if pressed:
                self.motion(x, y)
            self.button(0, pressed, x, y)
        self.advance_zoom()

    def advance_zoom(self):
        if self.zoom_gesture is None:
            if not self.wheel_steps or self.buttons:
                return
            if self.zoom_needs_probe and self.runtime.graphics is not None and self.runtime.window is not None:
                return
            width, height = self.runtime.surface_width, self.runtime.surface_height
            anchor = self.zoom_anchor or (width - 28, round(height * 0.30))
            current = self.zoom_position if self.zoom_position is not None else round(height * 0.17)
            span = round(height * 0.19)
            low, high = max(20, anchor[1] - span), min(height - 20, anchor[1] + span)
            target = max(low, min(high, current - self.wheel_steps * max(1, round(height / 60))))
            self.wheel_steps = 0
            self.zoom_gesture = {"phase": 0, "x": anchor[0], "start": current, "target": target}
            self.last_zoom_drag = {"x": anchor[0], "start": current, "target": target}
            self.motion(anchor[0], current, virtual=True)
            self.button(0, True, virtual=True)
            return
        gesture = self.zoom_gesture
        if gesture["phase"] == 0:
            # Give the game's widget a full update to capture the thumb.
            gesture["phase"] = 1
        elif gesture["phase"] == 1:
            self.motion(gesture["x"], (gesture["start"] + gesture["target"]) // 2, virtual=True)
            gesture["phase"] = 2
        elif gesture["phase"] == 2:
            self.motion(gesture["x"], gesture["target"], virtual=True)
            gesture["phase"] = 3
        elif gesture["phase"] == 3:
            self.button(0, False, virtual=True)
            self.zoom_position = gesture["target"]
            gesture["phase"] = 4
        else:
            self.zoom_gesture = None
            self.motion(*self.physical_position, virtual=True)
            self.zoom_gestures_completed += 1

    def callback_requests(self, family, events):
        requests = []
        for event, content in events:
            targets = [(function, user) for (kind, registered_event, function), user
                       in self.runtime.callbacks.items() if kind == family and registered_event == event]
            if targets:
                pointer = self.runtime.allocate(len(content)) if content is not None else 0
                if content is not None:
                    self.runtime.cpu.mem_write(pointer, content)
                for function, user in targets:
                    requests.append((function, (pointer, user)))
                self.events_dispatched += 1
        return requests

    def poll_pointer(self):
        events = []
        buttons = set()
        while self.pointer_events:
            event = self.pointer_events[0]
            if event[0] == 0:
                button = struct.unpack_from("<i", event[1])[0]
                # Preserve a short click even when down/up both arrive between
                # slow game frames. Its release belongs to the next update.
                if button in buttons:
                    break
                buttons.add(button)
            events.append(self.pointer_events.popleft())
        return self.callback_requests("s3ePointer", events)

    def poll_keyboard(self):
        self.key_edges.clear()
        events = []
        while self.keyboard_events:
            key, pressed = self.keyboard_events.popleft()
            if pressed:
                self.keys.add(key)
                self.key_edges[key] = self.key_edges.get(key, 0) | 2
            else:
                self.keys.discard(key)
                self.key_edges[key] = self.key_edges.get(key, 0) | 4
            events.append((0, struct.pack("<2i", self.SDK_KEYS.get(key, key), int(pressed))))
        while self.character_events:
            events.append((1, struct.pack("<I", self.character_events.popleft())))
        return self.callback_requests("s3eKeyboard", events)

    def poll_device(self):
        events = list(self.device_events)
        self.device_events.clear()
        return self.callback_requests("s3eDevice", events)

    def key_state(self, key):
        key = next((physical for physical, sdk in self.SDK_KEYS.items() if sdk == key), key)
        return int(key in self.keys) | self.key_edges.get(key, 0)

    def acceleration(self, axis):
        if not self.accelerometer_active:
            return 0
        now = self.clock()
        if self.shake_started is not None:
            elapsed = now - self.shake_started
            if 0 <= elapsed < 0.8 or 116 in self.held_keys:
                # Time-based alternating acceleration, in SDK milli-g units.
                # Different frequencies avoid a constant sample when a slow
                # frame rate happens to align with one oscillation period.
                return (round(2400 * math.cos(elapsed * 31)) if axis == "X" else
                        round(-2400 * math.sin(elapsed * 23)) if axis == "Y" else
                        1000 + round(2400 * math.cos(elapsed * 41)))
        if self.jerk_started is not None and 0 <= now - self.jerk_started < 0.12:
            return 0 if axis == "X" else -3200 if axis == "Y" else 3200
        if axis == "X":
            return 1000 * (int(39 in self.keys or 68 in self.keys) - int(37 in self.keys or 65 in self.keys))
        if axis == "Y":
            return 1000 * (int(40 in self.keys or 83 in self.keys) - int(38 in self.keys or 87 in self.keys))
        return 1000
