"""Original game PCM effects and MP3 music through Windows' built-in WinMM."""
import collections
import ctypes as C
import hashlib
import os
import queue
import threading


class WaveFormat(C.Structure):
    _pack_ = 2
    _fields_ = [("tag", C.c_ushort), ("channels", C.c_ushort),
                ("rate", C.c_uint), ("bytes_per_second", C.c_uint),
                ("alignment", C.c_ushort), ("bits", C.c_ushort), ("extra", C.c_ushort)]


class WaveHeader(C.Structure):
    _fields_ = [("data", C.c_void_p), ("length", C.c_uint), ("recorded", C.c_uint),
                ("user", C.c_size_t), ("flags", C.c_uint), ("loops", C.c_uint),
                ("next", C.c_void_p), ("reserved", C.c_size_t)]


class WindowsAudio:
    CHANNELS = 16

    def __init__(self):
        self.dll = C.WinDLL("winmm")
        signatures = {
            "waveOutGetNumDevs": (C.c_uint, []),
            "waveOutOpen": (C.c_uint, [C.POINTER(C.c_void_p), C.c_uint, C.POINTER(WaveFormat), C.c_size_t, C.c_size_t, C.c_uint]),
            "waveOutClose": (C.c_uint, [C.c_void_p]),
            "waveOutReset": (C.c_uint, [C.c_void_p]),
            "waveOutPause": (C.c_uint, [C.c_void_p]),
            "waveOutRestart": (C.c_uint, [C.c_void_p]),
            "waveOutSetVolume": (C.c_uint, [C.c_void_p, C.c_uint]),
            "waveOutPrepareHeader": (C.c_uint, [C.c_void_p, C.POINTER(WaveHeader), C.c_uint]),
            "waveOutUnprepareHeader": (C.c_uint, [C.c_void_p, C.POINTER(WaveHeader), C.c_uint]),
            "waveOutWrite": (C.c_uint, [C.c_void_p, C.POINTER(WaveHeader), C.c_uint]),
            "mciSendStringW": (C.c_uint, [C.c_wchar_p, C.c_wchar_p, C.c_uint, C.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.dll, name)
            function.restype, function.argtypes = result, arguments
        self.available = bool(self.dll.waveOutGetNumDevs())
        self.channels = {}
        self.music_open = False
        self.music_paused = False
        self.music_repeats = 0
        self.music_alias = "sims3_music"
        self.last_error = None
        self.pcm_plays = 0
        self.music_plays = 0
        self.nonzero_pcm_plays = 0
        self.music_worker = None
        self.music_queue = queue.Queue()
        self.music_state = 0
        self.music_generation = 0

    def start_music_worker(self):
        if self.music_worker is not None:
            return
        self.music_worker = threading.Thread(target=self._music_loop, name="Sims3 music", daemon=True)
        self.music_worker.start()

    def _music_loop(self):
        # MCI/decoder calls can block. This thread never accesses the guest CPU.
        # SDK status reads use cached state and remain immediate on the game thread.
        ole = C.WinDLL("ole32")
        initialized = ole.CoInitializeEx(None, 0) in (0, 1)
        try:
            while True:
                try:
                    item = self.music_queue.get(timeout=0.25)
                except queue.Empty:
                    item = None
                if item is not None:
                    generation, operation, arguments = item
                    if operation == "close":
                        self._stop_music()
                        return
                    result = getattr(self, "_" + operation)(*arguments)
                    if operation == "play_music" and not result and generation == self.music_generation:
                        self.music_state = 0
                generation = self.music_generation
                state = self._music_status()
                if generation == self.music_generation and self.music_queue.empty():
                    self.music_state = state
        except Exception as exc:
            self.last_error = "Music worker: " + str(exc)
            self.music_state = 0
        finally:
            if initialized:
                ole.CoUninitialize()

    def _queue_music(self, operation, arguments=(), state=None):
        self.music_generation += 1
        if state is not None:
            self.music_state = state
        self.music_queue.put((self.music_generation, operation, arguments))

    def command(self, command):
        output = C.create_unicode_buffer(256)
        result = self.dll.mciSendStringW(command, output, len(output), None)
        if result:
            self.last_error = "MCI error " + str(result) + ": " + command.split()[0]
            return None
        return output.value

    def stop_effect(self, channel):
        state = self.channels.get(channel)
        if state is None:
            return True
        self.dll.waveOutReset(state["handle"])
        if state.get("prepared"):
            result = self.dll.waveOutUnprepareHeader(state["handle"], C.byref(state["header"]), C.sizeof(WaveHeader))
            if result:
                self.last_error = "waveOutUnprepareHeader error " + str(result)
                return False  # Retain memory until Windows releases the header.
        result = self.dll.waveOutClose(state["handle"])
        if result:
            self.last_error = "waveOutClose error " + str(result)
            return False
        del self.channels[channel]
        return True

    def busy(self, channel):
        state = self.channels.get(channel)
        return state is not None and not bool(state["header"].flags & 1)

    def set_effect_volume(self, channel, volume):
        if channel in self.channels:
            value = round(max(0, min(256, volume)) * 65535 / 256)
            self.dll.waveOutSetVolume(self.channels[channel]["handle"], value | value << 16)

    def play_effect(self, channel, pcm, rate, repeats, volume):
        if not self.available or not self.stop_effect(channel):
            return False
        # The guest owns its source buffer and can free/reuse it immediately.
        # Windows retains this separate snapshot until WHDR_DONE/reset.
        buffer = C.create_string_buffer(pcm)
        format_ = WaveFormat(1, 1, rate, rate * 2, 2, 16, 0)
        handle = C.c_void_p()
        result = self.dll.waveOutOpen(C.byref(handle), 0xFFFFFFFF, C.byref(format_), 0, 0, 0)
        if result:
            self.last_error = "waveOutOpen error " + str(result)
            return False
        header = WaveHeader(data=C.addressof(buffer), length=len(pcm))
        if repeats != 1:
            header.flags = 4 | 8  # WHDR_BEGINLOOP | WHDR_ENDLOOP
            header.loops = 0xFFFFFFFF if repeats == 0 else repeats
        state = {"handle": handle, "buffer": buffer, "header": header, "prepared": False}
        self.channels[channel] = state
        self.set_effect_volume(channel, volume)
        result = self.dll.waveOutPrepareHeader(handle, C.byref(header), C.sizeof(header))
        if not result:
            state["prepared"] = True
            result = self.dll.waveOutWrite(handle, C.byref(header), C.sizeof(header))
        if result:
            self.last_error = "waveOut queue error " + str(result)
            self.stop_effect(channel)
            return False
        self.pcm_plays += 1
        self.nonzero_pcm_plays += int(any(pcm))
        return True

    def stop_music(self):
        if self.music_worker:
            self._queue_music("stop_music", state=0)
            return
        self._stop_music()

    def _stop_music(self):
        if self.music_open:
            self.command("close " + self.music_alias)
        self.music_open = self.music_paused = False

    def play_music(self, path, repeats, volume):
        if self.music_worker:
            if not self.music_worker.is_alive():
                return False
            self._queue_music("play_music", (path, repeats, volume), state=1)
            return self.available
        return self._play_music(path, repeats, volume)

    def _play_music(self, path, repeats, volume):
        self._stop_music()
        if not self.available:
            return False
        alias = self.music_alias
        if self.command(f'open "{path}" type mpegvideo alias {alias}') is None:
            return False
        self.music_open = True
        self.command("set " + alias + " time format milliseconds")
        self._set_music_volume(volume)
        self.music_repeats = repeats
        if self.command("play " + alias + " from 0") is None:
            self._stop_music()
            return False
        self.music_plays += 1
        return True

    def music_status(self):
        return self.music_state if self.music_worker else self._music_status()

    def _music_status(self):
        if not self.music_open:
            return 0
        state = self.command("status " + self.music_alias + " mode")
        if state == "stopped" and not self.music_paused and self.music_repeats != 1:
            if self.music_repeats > 1:
                self.music_repeats -= 1
            if self.command("play " + self.music_alias + " from 0") is not None:
                state = "playing"
        return 2 if self.music_paused else int(state == "playing")

    def set_music_volume(self, volume):
        if self.music_worker:
            self._queue_music("set_music_volume", (volume,))
            return
        self._set_music_volume(volume)

    def _set_music_volume(self, volume):
        if self.music_open:
            self.command(f"setaudio {self.music_alias} volume to {round(volume * 1000 / 256)}")

    def pause_music(self):
        if self.music_worker:
            self._queue_music("pause_music", state=2)
            return
        self._pause_music()

    def _pause_music(self):
        if self.music_open and self.command("pause " + self.music_alias) is not None:
            self.music_paused = True

    def resume_music(self):
        if self.music_worker:
            self._queue_music("resume_music", state=1)
            return
        self._resume_music()

    def _resume_music(self):
        if self.music_open and self.music_paused and self.command("resume " + self.music_alias) is not None:
            self.music_paused = False

    def close(self):
        if getattr(self, "closed", False):
            return
        self.closed = True
        if self.music_worker:
            self._queue_music("close", state=0)
            self.music_worker.join(timeout=3)
        else:
            self._stop_music()
        for channel in list(self.channels):
            self.stop_effect(channel)


class Audio:
    def __init__(self, runtime, device=None):
        self.runtime = runtime
        self.device = device
        self.volume = 256
        self.rates = [22050] * WindowsAudio.CHANNELS
        self.volumes = [256] * WindowsAudio.CHANNELS
        self.events = collections.deque(maxlen=128)
        self.last_error = None

    def enable_windows(self):
        if os.name == "nt":
            try:
                self.device = WindowsAudio()
                self.device.start_music_worker()
            except OSError as exc:
                self.last_error = str(exc)

    @property
    def available(self):
        return self.device is not None and self.device.available

    def dispatch(self, name):
        a = self.runtime.argument
        args = [a(index) for index in range(4)]
        event = {"function": name, "args": args}
        if name != "s3eAudioGetInt":
            self.events.append(event)
        device = self.device
        if name == "s3eAudioGetInt":
            property_ = args[0]
            if property_ > 7:
                raise RuntimeError("Audio property not implemented: " + str(property_))
            if property_ == 1:
                return device.music_status() if self.available else 0
            return {0: self.volume if self.available else 0, 2: 0, 3: 256 if self.available else 0,
                    4: 0, 5: int(self.available), 6: int(self.available), 7: int(self.available)}.get(property_, 0)
        if name == "s3eSoundGetInt":
            if args[0] > 7:
                raise RuntimeError("Sound property not implemented: " + str(args[0]))
            if not self.available:
                return 0 if args[0] in (3, 5) else 0xFFFFFFFF
            if args[0] == 4:
                mask = sum(1 << index for index in range(len(self.rates)) if device.busy(index))
                return mask | (0xFFFFFFFF << len(self.rates) & 0xFFFFFFFF)
            return {0:256, 1:22050, 2:22050, 3:len(self.rates), 5:1, 6:0, 7:1}[args[0]]
        if name == "s3eAudioIsCodecSupported":
            return int(self.available and args[0] == 2)  # SDK codec 2 is MP3.
        if name == "s3eSoundGetFreeChannel":
            return next((index for index in range(len(self.rates)) if not device.busy(index)), 0xFFFFFFFF) if self.available else 0xFFFFFFFF
        if name == "s3eAudioSetInt" and args[0] == 0:
            self.volume = max(0, min(256, C.c_int32(args[1]).value))
            if self.available:
                device.set_music_volume(self.volume)
            return 0 if self.available else 1
        if name == "s3eAudioPlay":
            path = self.runtime.string(args[0])
            event["path"] = path
            if not self.available:
                return 1
            try:
                content = self.runtime.filesystem.read_path(path)
                cache = self.runtime.filesystem.saves.parent / "cache/audio"
                cache.mkdir(parents=True, exist_ok=True)
                target = cache / (hashlib.sha256(content).hexdigest() + ".mp3")
                if not target.exists():
                    target.write_bytes(content)
                result = device.play_music(str(target), args[1], self.volume)
            except (OSError, KeyError, ValueError) as exc:
                self.last_error = str(exc)
                result = False
            event["accepted"] = result  # Actual completed starts are counted by music_plays.
            return 0 if result else 1
        if name in ("s3eAudioStop", "s3eAudioPause", "s3eAudioResume"):
            if not self.available:
                return 1
            {"s3eAudioStop":device.stop_music, "s3eAudioPause":device.pause_music,
             "s3eAudioResume":device.resume_music}[name]()
            return 0
        if name.startswith("s3eSoundChannel"):
            channel = args[0]
            if channel >= len(self.rates) or not self.available:
                return 1
            if name == "s3eSoundChannelSetInt":
                property_, value = args[1:3]
                if property_ == 1 and 100 <= value <= 192000:
                    self.rates[channel] = value
                elif property_ == 0 and 0 < value <= 0x100000:
                    self.rates[channel] = max(100, min(192000, 22050 * value // 65536))
                elif property_ == 3:
                    self.volumes[channel] = max(0, min(256, C.c_int32(value).value))
                    device.set_effect_volume(channel, self.volumes[channel])
                else:
                    return 1
                return 0
            if name == "s3eSoundChannelPlay":
                pointer, samples, repeats = args[1:]
                offset = a(4)
                event["offset_samples"] = offset
                if not pointer or not 0 < samples <= 8 * 1024 * 1024 or offset >= samples:
                    return 1
                content = bytes(self.runtime.cpu.mem_read(pointer + offset * 2, (samples - offset) * 2))
                result = device.play_effect(channel, content, self.rates[channel], repeats, self.volumes[channel])
                event.update(played=result, sample_rate=self.rates[channel], pcm_bytes=len(content))
                return 0 if result else 1
            if name == "s3eSoundChannelStop":
                return 0 if device.stop_effect(channel) else 1
            if name == "s3eSoundChannelUnRegister":
                return 1  # No callback was registered; preserve the SDK's error.
        raise RuntimeError("Audio API/property not implemented: " + name + " " + str(args))

    def report(self):
        return {"available": self.available, "backend":"Windows WinMM PCM + MCI MP3" if self.device else None,
                "pcm_plays":getattr(self.device,"pcm_plays",0), "music_plays":getattr(self.device,"music_plays",0),
                "nonzero_pcm_plays":getattr(self.device,"nonzero_pcm_plays",0),
                "last_error":self.last_error or getattr(self.device,"last_error",None), "events":list(self.events)}

    def close(self):
        if self.device:
            self.device.close()
