"""Guest mutex/semaphore state and deferred ARM workers.

Slot layout was recovered from the supplied SDK's actual registration table.
Workers run cooperatively at a main-thread yield, on separate guest stacks.
Host GUI operations stay on the native window thread.
"""
import collections
import struct
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR


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
        self.workers = {}
        self.pending = collections.deque()
        self.main_context = None
        self.main_heap = 0

    def create(self, function, data, stack_bytes):
        if len(self.workers) >= 127:
            return 0
        stack_bytes = max(256 * 1024, stack_bytes or 0)
        if stack_bytes > 8 * 1024 * 1024 or not function:
            return 0
        stack = self.runtime.allocate(stack_bytes)
        if not stack:
            return 0
        identifier = next(value for value in range(4001, 4128) if value not in self.workers)
        self.workers[identifier] = {"function": function, "data": data,
                                    "stack": stack, "stack_bytes": stack_bytes,
                                    "detached": False, "state": "pending", "result": None}
        self.pending.append(identifier)
        return identifier

    def set_current(self, identifier):
        self.current_thread = identifier
        if hasattr(self.runtime, "fast_services"):
            self.runtime.cpu.mem_write(self.runtime.fast_services.thread_address, struct.pack("<I", identifier))

    def start_pending(self):
        runtime = self.runtime
        if self.current_thread != 4000 or not self.pending or runtime.callback_stack:
            return False
        identifier = self.pending.popleft()
        worker = self.workers[identifier]
        # Resume the completed DeviceYield after the worker has returned.
        runtime.cpu.reg_write(UC_ARM_REG_R0, 0)
        runtime.jump(runtime.cpu.reg_read(UC_ARM_REG_LR))
        self.main_context = runtime.cpu.context_save()
        self.main_heap = runtime.current_heap
        runtime.current_heap = 0
        self.set_current(identifier)
        worker["state"] = "running"
        runtime.cpu.reg_write(UC_ARM_REG_SP, worker["stack"] + worker["stack_bytes"] - 16)
        runtime.cpu.reg_write(UC_ARM_REG_R0, worker["data"])
        runtime.cpu.reg_write(UC_ARM_REG_LR, runtime.import_trap("__thread_return"))
        runtime.jump(worker["function"])
        return True

    def complete(self, result):
        if self.current_thread == 4000 or self.main_context is None:
            raise ThreadUnsupported("No active guest worker to complete")
        runtime = self.runtime
        identifier = self.current_thread
        worker = self.workers[identifier]
        worker.update(state="completed", result=result)
        runtime.cpu.context_restore(self.main_context)
        self.main_context = None
        runtime._api_control_changed = True
        runtime.current_heap = self.main_heap
        self.set_current(4000)
        runtime.release(worker["stack"])
        worker["stack"] = 0
        if worker["detached"]:
            del self.workers[identifier]

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
        if slot == 3:
            result = self.create(a(0), a(1), a(2))
            record.update(operation="create_worker", handle=result)
            return result
        if slot == 5:
            self.complete(a(0))
            return None
        if slot == 6:
            worker = self.workers.get(a(0))
            if worker is None:
                return 1
            worker["detached"] = True
            if worker["state"] == "completed":
                del self.workers[a(0)]
            record["operation"] = "detach_worker"
            return 0
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
