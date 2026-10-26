"""Mutex/semaphore services for the single guest execution thread.

Slot layout was recovered from the supplied SDK's actual registration table.
Creating/scheduling background ARM threads remains explicitly unsupported.
"""


class ThreadUnsupported(RuntimeError):
    pass


class ThreadBridge:
    def __init__(self, runtime):
        self.runtime = runtime
        self.current_thread = 4000
        self.mutexes = {}
        self.semaphores = {}
        self.next_mutex = 5000
        self.next_semaphore = 6000
        self.error_code = 0

    def dispatch(self, name):
        slot = int(name.removeprefix("thread_slot_"))
        a = self.runtime.argument
        record = self.runtime.trace[-1]
        if slot == 0:
            return self.error_code
        if slot == 1:
            text = b"" if not self.error_code else b"Thread synchronization operation failed"
            pointer = self.runtime.allocate(len(text) + 1)
            self.runtime.cpu.mem_write(pointer, text + b"\0")
            return pointer
        if slot == 4:
            record["operation"] = "current_thread"
            return self.current_thread
        if slot in (9, 10):
            handle = self.next_mutex
            self.next_mutex += 1
            self.mutexes[handle] = {"recursive": slot == 10, "owner": None, "depth": 0}
            record.update({"operation": "create_recursive_mutex" if slot == 10 else "create_mutex",
                           "handle": handle})
            return handle
        if slot in (11, 12, 13):
            handle = a(0)
            if handle not in self.mutexes:
                self.error_code = 1
                return 1
            mutex = self.mutexes[handle]
            if slot == 11:
                record["operation"] = "destroy_mutex"
                if mutex["owner"] is not None:
                    self.error_code = 1
                    return 1
                del self.mutexes[handle]
                return 0
            if slot == 12:
                record["operation"] = "lock_mutex"
                if mutex["owner"] is None:
                    mutex["owner"], mutex["depth"] = self.current_thread, 1
                    return 0
                if mutex["owner"] == self.current_thread and mutex["recursive"]:
                    mutex["depth"] += 1
                    return 0
                if a(1) == 0:
                    self.error_code = 1
                    return 1
                raise ThreadUnsupported("Mutex wait requires another runnable guest thread")
            record["operation"] = "unlock_mutex"
            if mutex["owner"] != self.current_thread or mutex["depth"] == 0:
                self.error_code = 1
                return 1
            mutex["depth"] -= 1
            if not mutex["depth"]:
                mutex["owner"] = None
            return 0
        if slot == 14:
            handle = self.next_semaphore
            self.next_semaphore += 1
            self.semaphores[handle] = a(0)
            record.update({"operation": "create_semaphore", "handle": handle})
            return handle
        if slot in (15, 16, 17, 18):
            handle = a(0)
            if handle not in self.semaphores:
                self.error_code = 1
                return 1
            if slot == 15:
                del self.semaphores[handle]
                return 0
            if slot == 16:
                if self.semaphores[handle]:
                    self.semaphores[handle] -= 1
                    return 0
                if a(1) == 0:
                    self.error_code = 1
                    return 1
                raise ThreadUnsupported("Semaphore wait requires another runnable guest thread")
            if slot == 17:
                self.semaphores[handle] += 1
                return 0
            return self.semaphores[handle]
        raise ThreadUnsupported("Thread API slot not implemented: " + str(slot))
