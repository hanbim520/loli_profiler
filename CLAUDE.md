# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LoliProfiler is a C/C++ memory profiling tool for Android games and applications built with Qt. It connects to Android devices via ADB to capture and analyze memory allocation patterns, stack traces, and system memory information.

The project builds two executables:
- **LoliProfiler** — Full GUI application with interactive profiling and visualization
- **LoliProfilerCLI** — Console application for automated profiling and CI/CD integration

Both executables share core profiling logic and configuration files, with the CLI version excluding GUI dependencies (Widgets, Charts, OpenGL) for a lighter footprint.

For architecture details, data structures, threading model, and development patterns see **[docs/ARCH.md](docs/ARCH.md)**.

## Build Commands

### Prerequisites
Set these environment variables before building:
- `QT5Path` — Path to Qt 5.12/5.14/5.15 installation
- `MSBUILD_EXE` — Path to MSBuild (Windows only)
- `Ndk_R16_CMD` — Path to Android NDK r16b ndk-build
- `Ndk_R20_CMD` — Path to Android NDK r20/r25 ndk-build

### Build All
**Windows:** `build.bat`  
**macOS:** `sh build.sh`  
**Linux:** `./build_linux_with_docker.sh`

### Build Outputs
- GUI: `./build/cmake/bin/release/LoliProfiler.exe` (Windows) or `LoliProfiler.app` (macOS)
- CLI: `./build/cmake/bin/release/LoliProfilerCLI.exe` (Windows) or `LoliProfilerCLI` (macOS/Linux)
- Final package: `./dist/`

## CLI Quick Reference

```bash
# Profile for 60 seconds with symbol translation
LoliProfilerCLI --app com.example.game --out profile.loli --symbol /path/to/lib.so --duration 60

# Profile until Ctrl+C
LoliProfilerCLI --app com.example.game --out profile.loli --verbose

# Profile with memory optimization (streams data to disk, recommended for large projects)
LoliProfilerCLI --app com.example.game --out profile.loli --enable-memory-optimization

# Compare two profiles to detect memory regressions
LoliProfilerCLI --compare baseline.loli current.loli --out diff.txt

# Compare with skipped root levels (for system libs without symbols)
LoliProfilerCLI --compare baseline.loli current.loli --out diff.txt --skip-root-levels 2

# Dump a single .loli file to text (hierarchical call tree with absolute values)
LoliProfilerCLI --dump profile.loli --out dump.txt --skip-root-levels 2
```
