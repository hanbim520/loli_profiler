# Architecture

Developer-focused reference for the LoliProfiler codebase. Covers data flow, components, patterns, and pitfalls for contributors.

## Core Data Flow

**GUI Mode:**
1. Android agent hooks malloc/free and sends `RawStackInfo` records via TCP
2. `MainWindow` receives records in real-time, builds `StackTraceModel`
3. User interacts with visualizations (timeline, tree map, call tree, fragmentation)
4. Data can be saved to `.loli` files for later analysis

**CLI Mode:**
1. Android agent hooks malloc/free and sends `RawStackInfo` records via TCP
2. `CliProfiler` receives records and caches to disk (`cache/` directory)
3. After capture completes (duration timeout or Ctrl+C), processes cached records
4. Saves complete `.loli` file for GUI analysis

## Key Components

**Entry Points:**
- `src/main.cpp` — GUI mode entry point (Qt Widgets application)
- `src/main_cli.cpp` — CLI mode entry point (QCoreApplication, no GUI)

**Controllers:**
- `MainWindow` (`src/mainwindow.cpp`) — GUI controller, manages all UI and profiling operations
- `CliProfiler` (`src/cliprofiler.cpp`) — CLI controller, manages headless profiling workflow
- `ProfileComparator` (`src/profilecomparator.cpp`) — Comparison engine for detecting memory regressions between two `.loli` files

**Process Management (Base: `AdbProcess`):**
- `StartAppProcess` — Launches target application via ADB
- `StackTraceProcess` — TCP socket connection to Android profiling agent, receives allocation/deallocation records
- `MemInfoProcess` — Captures system memory info (`/proc/meminfo`) via ADB
- `ScreenshotProcess` — Takes device screenshots for correlation
- `AddressProcess` — Resolves memory addresses to function symbols

**Data Models:**
- `StackTraceModel` — Table model containing `StackRecord` entries (UUID, time, size, address, library)
- `StackTraceProxyModel` — Filtering/sorting proxy for stack traces
- `RawStackInfo` — Raw allocation/deallocation record format from Android agent
- `SMapsSection` — Memory mapping information from `/proc/pid/smaps`

**Android Native Libraries (`plugins/Android/`):**
- Built with NDK for multiple architectures (armeabi, armeabi-v7a, arm64-v8a)
- Hooks malloc/calloc/realloc/free using custom interception techniques
- Communicates with desktop client via TCP socket on port 44515
- Sends stack traces with allocation/deallocation metadata

## Key Data Structures

**`StackRecord`:**
```cpp
struct StackRecord {
    QUuid uuid_;        // Call stack UUID
    quint32 seq_;       // Sequence number
    qint32 time_;       // Timestamp
    qint32 size_;       // Allocation size
    quint64 addr_;      // Memory address
    quint64 funcAddr_;  // Function address
    HashString library_; // Library name (optimized string storage)
};
```

**`RawStackInfo`** (from Android agent):
- Contains allocation/deallocation flag, timestamp, size, address, call stack frames

**Call Stack Maps:**
- `callStackMap_` — Maps UUIDs to call stack sequences (library + function address pairs)
- `symbloMap_` — Address-to-symbol resolution cache
- `freeAddrMap_` — Tracks deallocated addresses to filter out freed memory

## Conditional Compilation

The codebase uses the `NO_GUI_MODE` preprocessor flag to enable CLI-only builds:
- When defined, excludes Qt Widgets/Charts/OpenGL dependencies
- Allows shared components like `ConfigDialog` to function as data containers without UI
- Process classes (`StartAppProcess`, `ScreenshotProcess`) work in both modes
- Build system automatically defines this flag when compiling LoliProfilerCLI

**When adding features to shared components:**
1. Guard GUI-specific code with `#ifndef NO_GUI_MODE`
2. Ensure core functionality works without Qt Widgets dependencies
3. Test both executables after changes
4. Update `CMakeLists.txt` if adding new files

## Configuration Sharing

Both GUI and CLI modes share the same configuration file:
- Windows: `%LOCALAPPDATA%\MoreFun\LoliProfiler\loli3.conf`
- macOS/Linux: `~/.local/share/MoreFun/LoliProfiler/loli3.conf`

Both executables set organization name ("MoreFun") and application name ("LoliProfiler") to ensure config compatibility. This allows CLI to use settings configured in GUI (compiler type, architecture, whitelist, blacklist).

## Android Agent Protocol

The Android agent communicates via TCP socket with binary protocol:
- Port: 44515 (forwarded via ADB)
- Commands: `START_CAPTURE`, `STOP_CAPTURE`, `SMAPS_DUMP`
- Data format: LZ4-compressed `RawStackInfo` records

## Multi-threading

- GUI operations run on main Qt thread
- ADB processes run asynchronously (`QProcess`)
- TCP socket communication is event-driven
- Data processing may use `Qt::Concurrent` for heavy operations
- CLI mode uses `QCoreApplication` (no GUI event loop)

## Signal Handling (CLI Mode)

CLI implements graceful shutdown via SIGINT/SIGTERM handlers:
- Signal handler is async-signal-safe (uses `write()` instead of stdio)
- Invokes `CliProfiler::RequestStop()` via `Qt::QueuedConnection` (thread-safe)
- Ensures proper SMAPS dump and file save before exit
- Allows profiling across app restarts (doesn't auto-exit when app exits)

## Comparison Algorithm

`ProfileComparator` compares two `.loli` files in five steps:
1. Load both profiles and build allocation maps
2. Group allocations by `(library_name, function_address)` key
3. Calculate statistics (new, removed, changed allocations)
4. Build call tree with size/count deltas
5. Export as text or `.loli` format

## File Format

`.loli` files use a binary format:
- Magic number: `0xA4B3C2D1`
- Version: `106`
- Contains: stack records, call stacks, symbols, memory info series, screenshots, SMAPS sections

## Common Pitfalls

1. **Path spaces on Windows** — Always quote paths with spaces when passing to ADB commands
2. **JDWP injection** — Requires debuggable apps or rooted devices
3. **Symbol file structure** — Must match Android library directory layout
4. **Memory optimization** — Use streaming mode for large datasets (CLI enables by default)
5. **Comparison version mismatch** — Both `.loli` files must have the same version/magic number
