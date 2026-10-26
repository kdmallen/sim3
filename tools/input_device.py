"""Stateful pointer/keyboard input, including reproducible diagnostic taps."""
import collections
import struct


class InputDevice:
    def __init__(self, runtime, taps=()):
        self.runtime = runtime
        self.x = self.y = 0
        self.buttons = set()
        self.keys = set()
        self.key_edges = {}
        self.pointer_events = collections.deque()
        self.keyboard_events = collections.deque()
        self.device_events = collections.deque()
        self.character_events = collections.deque()
        self.scheduled = collections.defaultdict(list)
        self.events_dispatched = 0
        self.accelerometer_available = False
        self.accelerometer_active = False
        for frame, x, y in taps:
            self.scheduled[frame].append((x, y, True))
            self.scheduled[frame + 1].append((x, y, False))

    def motion(self, x, y):
        self.x, self.y = int(x), int(y)
        self.pointer_events.append((1, struct.pack("<2i", self.x, self.y)))

    def button(self, button, pressed, x=None, y=None):
        if x is not None:
            self.x, self.y = int(x), int(y)
        if pressed:
            self.buttons.add(button)
        else:
            self.buttons.discard(button)
        self.pointer_events.append((0, struct.pack("<4i", button, int(pressed), self.x, self.y)))

    def key(self, key, pressed):
        self.keyboard_events.append((int(key), bool(pressed)))

    def character(self, codepoint):
        self.character_events.append(int(codepoint))

    def on_frame(self, frame):
        for x, y, pressed in self.scheduled.pop(frame, []):
            if pressed:
                self.motion(x, y)
            self.button(0, pressed, x, y)

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
        events = list(self.pointer_events)
        self.pointer_events.clear()
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
            events.append((0, struct.pack("<2i", key, int(pressed))))
        while self.character_events:
            events.append((1, struct.pack("<I", self.character_events.popleft())))
        return self.callback_requests("s3eKeyboard", events)

    def poll_device(self):
        events = list(self.device_events)
        self.device_events.clear()
        return self.callback_requests("s3eDevice", events)

    def key_state(self, key):
        return int(key in self.keys) | self.key_edges.get(key, 0)

    def acceleration(self, axis):
        if not self.accelerometer_active:
            return 0
        if axis == "X":
            return 1000 * (int(39 in self.keys or 68 in self.keys) - int(37 in self.keys or 65 in self.keys))
        if axis == "Y":
            return 1000 * (int(40 in self.keys or 83 in self.keys) - int(38 in self.keys or 87 in self.keys))
        return 1000
