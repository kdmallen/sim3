/* A native GUI entry point for the bundled Python application.
 * No compiler runtime or separately installed Python is required.
 * Compiled with the Microsoft x64 ABI; wchar_t must be 16 bits.
 */
typedef unsigned short Wide;
typedef unsigned long long Size;
extern __attribute__((ms_abi)) Wide *GetCommandLineW(void);
extern __attribute__((ms_abi)) Wide **CommandLineToArgvW(const Wide *, int *);
extern __attribute__((ms_abi)) void *LocalAlloc(unsigned int, Size);
extern __attribute__((ms_abi)) void ExitProcess(unsigned int);
extern __attribute__((ms_abi)) int Py_Main(int, Wide **);

void entry(void) {
    int count = 0;
    Wide **original = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!original || count < 1 || count > 1024) ExitProcess(1);
    Wide **arguments = LocalAlloc(0x40, (Size)(count + 3) * sizeof(Wide *));
    if (!arguments) ExitProcess(1);
    arguments[0] = original[0];
    arguments[1] = (Wide *)L"-c";
    arguments[2] = (Wide *)L"from player import launch; raise SystemExit(launch())";
    for (int i = 1; i < count; ++i) arguments[i + 2] = original[i];
    ExitProcess((unsigned int)Py_Main(count + 2, arguments));
}
