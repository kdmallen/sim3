"""Continuous game time, independent of real input/audio/diagnostic clocks."""
import time


class GameClock:
    def __init__(self, clock=time.monotonic_ns):
        self.clock = clock
        self.anchor = clock()
        self.elapsed = 0
        self.speed = 1

    def nanoseconds(self):
        return self.elapsed + (self.clock() - self.anchor) * self.speed

    def set_speed(self, speed):
        if speed not in (1, 2, 4, 6, 8, 10, 12):
            raise ValueError("Game speed must be 1, 2, 4, 6, 8, 10, or 12")
        now = self.clock()
        self.elapsed += (now - self.anchor) * self.speed
        self.anchor = now
        self.speed = speed

    def cycle(self):
        self.set_speed({1: 2, 2: 4, 4: 6, 6: 8, 8: 10, 10: 12, 12: 1}[self.speed])
