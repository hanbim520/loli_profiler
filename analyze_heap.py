#!/usr/bin/env python3
"""
Automated heap analysis using MCP server + Claude Code.

Instead of dumping the entire data file (potentially hundreds of thousands of
lines) into Claude's prompt, this script launches an MCP server that loads the
data and exposes query tools. Claude interactively explores the heap data,
searches source code, and writes a report — with only ~10-20KB of focused data
entering the context window.

Supports both file formats:
  - Diff files from `LoliProfilerCLI --compare` (two-profile comparison)
  - Snapshot files from `LoliProfilerCLI --dump` (single-profile export)

Usage:
    # Analyze a diff file
    python analyze_heap.py diff.txt --repo /path/to/source -o report.md

    # Analyze a snapshot file
    python analyze_heap.py snapshot.txt --repo /path/to/source -o report.md

    # HTML output
    python analyze_heap.py diff.txt --repo /path/to/source -o report.html

    # Different repos for baseline/comparison (diff only)
    python analyze_heap.py diff.txt --base-repo /v1 --target-repo /v2

    # Custom minimum size threshold
    python analyze_heap.py diff.txt --repo /path/to/source --min-size 1.0
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

# Import markdown to HTML converter
try:
    from markdown_to_html import convert_file as convert_md_to_html
    HTML_CONVERSION_AVAILABLE = True
except ImportError:
    HTML_CONVERSION_AVAILABLE = False


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m {secs:.0f}s"


def detect_file_mode(data_file: str) -> str:
    """Detect whether a heap data file is a diff or snapshot.

    Checks the first few lines for the report title to determine the format.
    Returns 'snapshot' or 'diff'.
    """
    with open(data_file, 'r', encoding='utf-8') as f:
        for line in f:
            if 'Profile Report' in line:
                return 'snapshot'
            if 'Comparison Report' in line:
                return 'diff'
            # Stop scanning after the first non-blank, non-=== line
            if line.strip() and not line.strip().startswith('==='):
                break
    return 'diff'  # default to diff for backward compatibility


def find_claude_command() -> str:
    """Find the claude CLI command."""
    for cmd in ('claude-internal', 'claude'):
        path = shutil.which(cmd)
        if path:
            return path
    return ""


def build_prompt(output_file: str,
                 base_repo: str,
                 target_repo: str,
                 min_size_mb: float,
                 mode: str = "diff",
                 data_file: str = "") -> str:
    """Build the analysis prompt for Claude.

    Uses a single template for both snapshot and diff modes, with only
    the title, header rows, and source section varying.
    """
    same_repo = os.path.normpath(base_repo) == os.path.normpath(target_repo)

    # --- Mode-specific bits (kept minimal) ---
    if mode == "snapshot":
        report_title = "内存快照分析报告"
        header_rows = f"| 代码版本 | {os.path.basename(base_repo)} |"
        source_section = f"Source code repository: {base_repo}" if base_repo else "No source code repository provided."
        source_repo = base_repo
    else:
        report_title = "内存对比分析报告"
        header_rows = (
            f"| 基线版本 | {os.path.basename(base_repo)} |\n"
            f"| 对比版本 | {os.path.basename(target_repo)} |"
        )
        if same_repo:
            source_section = f"""Source code repository: {base_repo}
The two profiles are from different runs/builds of the same codebase."""
        else:
            source_section = f"""Baseline source code: {base_repo}
Comparison source code: {target_repo}
The profiles are from different versions. You can diff files between repos."""
        source_repo = target_repo if os.path.isdir(target_repo) else base_repo

    source_grep_instruction = (
        f'Use Grep and Read tools to search the source repository at {source_repo} '
        'for the function name. Analyze: what data structures are allocated, '
        'buffer sizes, container growth, caching behavior.'
        if base_repo else 'No source repo — skip source code analysis.'
    )

    return f"""You are a memory analysis agent. Analyze a LoliProfiler {report_title} using the loli-heap MCP tools,
then produce a structured Chinese-language report. Follow the phases below EXACTLY in order.
Do NOT skip phases. Do NOT reorder phases.

{source_section}

═══════════════════════════════════════════════════════════════
PHASE 1: DATA LOADING & THREAD DISCOVERY (you do this yourself)
═══════════════════════════════════════════════════════════════

Execute these tool calls in order:

1. load_file("{os.path.abspath(data_file)}")
2. get_summary() — record the total size, total allocations, root count for the report header.
3. search_function("Thread") — find all thread-related nodes.
   From the results, identify **thread entry-point nodes**: nodes whose function name
   contains a thread identifier (e.g. *Thread*::Run, *ThreadFunc, *ThreadMain,
   *Thread*Proc, *thread_create*) AND whose size >= {min_size_mb} MB.
   Record each thread's node_id, function name, and size.
4. get_top_allocations(30, {min_size_mb}) — find the 30 largest allocation nodes.
   For each result, check whether it is a descendant of any thread entry-point
   found in step 3 (use get_call_path to check if uncertain).
   Nodes NOT under any identified thread go into the "Others" group.

After Phase 1 you must have:
  - A list of thread entry-points: [(node_id, name, size_mb), ...]
  - A list of "Others" nodes: [(node_id, name, size_mb), ...]
  - Summary stats for the report header

═══════════════════════════════════════════════════════════════
PHASE 2: PER-THREAD HOTSPOT DISCOVERY (parallel sub-agents)
═══════════════════════════════════════════════════════════════

For EACH thread entry-point from Phase 1, dispatch ONE sub-agent with the Agent tool.
Also dispatch ONE sub-agent for the "Others" group.
Launch ALL these sub-agents IN PARALLEL (multiple Agent tool calls in a single message).

Each **thread sub-agent** receives this brief (fill in the bracketed values):

    You are analyzing memory allocations under thread "[thread_name]" (node_id=[id], size=[X] MB)
    in a LoliProfiler heap profile.

    Execute these steps:
    1. Call get_subtree([thread_node_id], 6) to see the call tree structure.
    2. Starting from the largest children, call get_children recursively (up to 3 levels)
       to find the hotspot leaf nodes — functions where memory actually accumulates.
    3. Select the top 3-5 hotspots by size (each must be >= {min_size_mb} MB).
       Skip generic wrappers (operator new, malloc, FMemory::Malloc, etc.) — pick
       the deepest FUNCTIONAL-level function in each branch.
    4. For each hotspot, call get_call_path(node_id) to get the full calling context.

    Return your results as EXACTLY this format (no other text):
    THREAD: [thread_name]
    THREAD_NODE_ID: [id]
    THREAD_SIZE_MB: [size]
    HOTSPOTS:
    - node_id: [id] | function: [name] | size_mb: [X.X] | call_path: [root > ... > func]
    - node_id: [id] | function: [name] | size_mb: [X.X] | call_path: [root > ... > func]
    ...

The **"Others" sub-agent** receives this brief:

    You are analyzing large memory allocations that are NOT under any identified thread.
    The following nodes were identified as "Others":
    [list each node_id, name, size_mb]

    For each node (up to 10):
    1. Call get_call_path(node_id) to see its full context.
    2. Call get_children(node_id) to see sub-allocations.
    3. Determine the top 3-5 hotspots across all "Others" nodes (>= {min_size_mb} MB).
       Skip generic wrappers — pick the deepest functional-level function.

    Return your results as EXACTLY this format:
    THREAD: Others
    THREAD_NODE_ID: N/A
    THREAD_SIZE_MB: [total]
    HOTSPOTS:
    - node_id: [id] | function: [name] | size_mb: [X.X] | call_path: [root > ... > func]
    ...

═══════════════════════════════════════════════════════════════
PHASE 3: PER-HOTSPOT DEEP ANALYSIS (parallel sub-agents)
═══════════════════════════════════════════════════════════════

Collect ALL hotspots from ALL Phase 2 sub-agents. For EACH hotspot, dispatch a sub-agent.
Launch ALL these sub-agents IN PARALLEL (multiple Agent tool calls in a single message).

Each **hotspot sub-agent** receives this brief:

    You are performing deep analysis on a memory hotspot in a LoliProfiler heap profile.
    Function: [function_name]
    Node ID: [node_id]
    Size: [X.X] MB
    Thread context: [thread_name]
    Call path: [call_path_summary from Phase 2]

    Execute these steps:
    1. Call get_call_path([node_id]) to get the full annotated call stack.
    2. Call get_children([node_id]) to see what sub-allocations exist below.
    3. {source_grep_instruction}

    Return your analysis as EXACTLY this format:

    HOTSPOT_ANALYSIS:
    function: [full function name]
    node_id: [id]
    size_mb: [X.X]
    thread: [thread_name]

    CALL_STACK (use tree format with └── and indentation, include size at each frame):
    FunctionRoot (XX.X MB)
    └── ChildFunction (XX.X MB)
        └── GrandchildFunction (XX.X MB)
            └── ... down to the hotspot function

    CODE_LOCATION: [file:line if found, or "N/A"]

    SOURCE_ANALYSIS:
    [2-3 sentences: what this code does, what data structures it allocates]

    ROOT_CAUSE:
    [2-3 sentences: why this allocation is large]

    OPTIMIZATION:
    [2-3 sentences: specific, actionable optimization suggestions]

═══════════════════════════════════════════════════════════════
PHASE 4: REPORT ASSEMBLY (you do this yourself)
═══════════════════════════════════════════════════════════════

Gather ALL results from Phase 2 and Phase 3. Assemble the final report using
EXACTLY this template. Do not add extra sections. Do not remove sections.
Fill in all [...] placeholders with actual data.

---BEGIN TEMPLATE---

# {report_title}

| 项目 | 值 |
|------|-----|
| 生成时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
| 分析工具 | LoliProfiler + Claude Code |
{header_rows}

## 内存分布概况

| 指标 | 值 |
|------|-----|
| 总内存 | [total size from get_summary] |
| 总分配次数 | [total allocs from get_summary] |
| 根节点数 | [root count] |
| 识别线程数 | [number of threads found in Phase 1] |

[1-2 sentences summarizing the overall memory distribution across threads]

## 线程内存分布

| 线程 | 内存占用 | 占比 |
|------|---------|------|
| [Thread 1 name] | [X.X MB] | [X.X%] |
| [Thread 2 name] | [X.X MB] | [X.X%] |
| ... | ... | ... |
| Others | [X.X MB] | [X.X%] |

## [Thread 1 name] ([X.X MB])

### [1] [函数名] — [X.X MB]

**完整调用栈:**
```
FunctionRoot (XX.X MB)
└── ChildFunction (XX.X MB)
    └── GrandchildFunction (XX.X MB)
        └── HotspotFunction (XX.X MB)
```

**代码位置:** [file:line or N/A]

**源码分析:** [from Phase 3 SOURCE_ANALYSIS]

**内存占用原因:** [from Phase 3 ROOT_CAUSE]

**优化建议:** [from Phase 3 OPTIMIZATION]

---

(repeat for all hotspots in this thread, numbered sequentially, sorted by size descending)

## [Thread 2 name] ([X.X MB])

(same structure as above)

## Others ([X.X MB])

(same structure for non-thread hotspots)

## 优化优先级建议

| 优先级 | 函数 | 线程 | 内存占用 | 难度 | 建议 |
|--------|------|------|---------|------|------|
| 1 | [func] | [thread] | [X.X MB] | [高/中/低] | [one-line summary] |
| 2 | ... | ... | ... | ... | ... |

(list all hotspots ranked by impact, top priority first)

## 总结

[3-5 sentences: overall memory distribution patterns, key findings, top 3 recommendations]

## 分析统计

| 指标 | 值 |
|------|-----|
| 分析热点数 | [total hotspots analyzed across all threads] |
| 识别线程数 | [number of threads found] |
| 派遣子代理数 | [total sub-agents dispatched in Phase 2 + Phase 3] |
| MCP 工具调用次数 | [estimated total loli-heap tool calls across all agents] |

---END TEMPLATE---

IMPORTANT RULES:
- Only analyze allocations >= {min_size_mb} MB.
- This is READ-ONLY analysis. Do NOT modify any source code files.
- Skip generic wrappers (operator new, malloc, FMemory::*, etc.) — drill to functional-level functions.
- All prose in Chinese. All code/function names in English.
- Sort threads by size descending. Sort hotspots within each thread by size descending.
- The "分析统计" table MUST be the very last content in the file.
  The harness will append a "总耗时" row to it. Ensure the table ends with a normal
  table row followed by a newline. Do NOT put any text after the stats table.

CRITICAL: Save the complete report to: {output_file}
Use the Write tool to save it. After saving, confirm with: "Report saved to: {output_file}"
"""


def build_mcp_config() -> dict:
    """Build the MCP server configuration.

    The server starts empty (no --file) so it connects to Claude instantly.
    The prompt instructs Claude to call load_file() as the first step.
    """
    server_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'mcp_server', 'heap_explorer_server.py'
    )
    return {
        "mcpServers": {
            "loli-heap": {
                "command": sys.executable,
                "args": [server_script],
                "env": {}
            }
        }
    }


def run_analysis(data_file: str,
                 base_repo: str,
                 target_repo: str,
                 output_file: str,
                 min_size_mb: float = 2.0,
                 timeout: int = 1800) -> bool:
    """Run Claude with MCP server to analyze heap data.

    Returns True on success, False otherwise.
    """
    claude_cmd = find_claude_command()
    if not claude_cmd:
        print("ERROR: 'claude-internal' or 'claude' command not found!", file=sys.stderr)
        print("Please ensure Claude CLI is installed and in your PATH.", file=sys.stderr)
        return False

    mode = detect_file_mode(data_file)
    print(f"Using Claude command: {claude_cmd}")
    print(f"Data file: {data_file} [mode: {mode}]")

    # Write temporary MCP config
    mcp_config = build_mcp_config()
    with tempfile.TemporaryDirectory(prefix='loli_mcp_') as tmp_dir:
        mcp_config_path = os.path.join(tmp_dir, '.mcp.json')
        with open(mcp_config_path, 'w') as f:
            json.dump(mcp_config, f, indent=2)

        print(f"MCP config: {mcp_config_path}")
        print(f"MCP server: {mcp_config['mcpServers']['loli-heap']['args']}")

        # Build prompt
        abs_output = os.path.abspath(output_file)
        prompt = build_prompt(abs_output, base_repo, target_repo, min_size_mb, mode=mode, data_file=data_file)

        prompt_size_kb = len(prompt.encode('utf-8')) / 1024
        print(f"Prompt size: {prompt_size_kb:.1f} KB (no data embedded — uses MCP tools)")
        print()

        try:
            if mode == "snapshot":
                cwd = base_repo if os.path.isdir(base_repo) else os.getcwd()
            else:
                cwd = target_repo if os.path.isdir(target_repo) else base_repo
            is_windows = sys.platform.startswith('win')

            # WARNING: --dangerously-skip-permissions allows Claude to execute
            # arbitrary tools without user confirmation. This is required for
            # batch/CI usage but should only be used in trusted environments.
            t_start = time.monotonic()
            result = subprocess.run(
                [
                    claude_cmd, '-p',
                    '--verbose',
                    '--dangerously-skip-permissions',
                    '--mcp-config', mcp_config_path,
                ],
                input=prompt,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                cwd=cwd,
                shell=is_windows,
            )
            duration_sec = time.monotonic() - t_start

            if result.returncode != 0:
                print(f"Claude analysis failed (exit {result.returncode}):", file=sys.stderr)
                if result.stderr:
                    print(result.stderr[:2000], file=sys.stderr)
                return False

            # Check if Claude wrote the report
            if os.path.exists(abs_output):
                with open(abs_output, 'r', encoding='utf-8') as f:
                    report = f.read()
                print("ANALYSIS COMPLETE")
                print(f"Report written by Claude to: {abs_output}")
            elif result.stdout.strip():
                # Fallback: save stdout
                report = result.stdout.strip()
                with open(abs_output, 'w', encoding='utf-8') as f:
                    f.write(report)
                print("ANALYSIS COMPLETE")
                print(f"Report written to: {abs_output}")
            else:
                print("Claude returned empty response", file=sys.stderr)
                return False

            # Print timing to console
            print(f"\nAnalysis duration: {_format_duration(duration_sec)}")

            # Preview
            print()
            print("Report preview:")
            print("-" * 80)
            lines = report.split('\n')
            for line in lines[:60]:
                print(line)
            if len(lines) > 60:
                print(f"\n... ({len(lines) - 60} more lines in full report)")
            print()

            return True

        except subprocess.TimeoutExpired:
            print(f"Analysis timed out after {timeout // 60} minutes", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return False


def main():
    parser = argparse.ArgumentParser(
        description='Analyze LoliProfiler heap data (diff or snapshot) via MCP server + Claude AI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze a diff with same repo for both versions
  python analyze_heap.py diff.txt --repo /path/to/game/source

  # Analyze a heap snapshot
  python analyze_heap.py snapshot.txt --repo /path/to/game/source

  # Analyze with different repos for baseline and comparison
  python analyze_heap.py diff.txt --base-repo /path/to/v1 --target-repo /path/to/v2

  # HTML output with custom threshold
  python analyze_heap.py diff.txt --repo /path/to/source --min-size 1.0 -o report.html

Compared to analyze_memory_diff.py, this version:
  - Sends ~2KB prompt instead of megabytes of data
  - Claude explores data interactively via MCP tools
  - Works with arbitrarily large files (tested with 588K+ nodes)
        """
    )

    parser.add_argument('data_file', help='Path to LoliProfilerCLI output file (diff from --compare, or snapshot from --dump)')
    parser.add_argument('--repo',
                        help='Path to source code repo (used for both baseline and comparison)')
    parser.add_argument('--base-repo',
                        help='Path to baseline version source code repo')
    parser.add_argument('--target-repo',
                        help='Path to comparison version source code repo')
    parser.add_argument('--output', '-o',
                        help='Output report file path (.md or .html, default: auto-generated .md)')
    parser.add_argument('--min-size', type=float, default=2.0,
                        help='Minimum allocation size threshold in MB (default: 2.0)')
    parser.add_argument('--timeout', '-t', type=int, default=1800,
                        help='Analysis timeout in seconds (default: 1800 = 30 minutes)')

    args = parser.parse_args()

    # Validate
    if not os.path.exists(args.data_file):
        print(f"Error: Data file not found: {args.data_file}", file=sys.stderr)
        return 1

    if args.repo:
        base_repo = target_repo = args.repo
    elif args.base_repo and args.target_repo:
        base_repo = args.base_repo
        target_repo = args.target_repo
    else:
        print("Error: Must specify either --repo or both --base-repo and --target-repo",
              file=sys.stderr)
        return 1

    if not os.path.exists(base_repo):
        print(f"Error: Base repo path not found: {base_repo}", file=sys.stderr)
        return 1
    if not os.path.exists(target_repo):
        print(f"Error: Target repo path not found: {target_repo}", file=sys.stderr)
        return 1

    # Output path
    output_file = args.output or f"memory_analysis_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    output_file = os.path.abspath(output_file)

    output_html = output_file.lower().endswith('.html')
    if output_html:
        md_output_file = output_file[:-5] + '.md'
    else:
        md_output_file = output_file

    # Run
    success = run_analysis(
        args.data_file,
        base_repo,
        target_repo,
        md_output_file,
        min_size_mb=args.min_size,
        timeout=args.timeout,
    )

    # HTML conversion
    if success and output_html:
        if HTML_CONVERSION_AVAILABLE:
            print("Converting markdown to HTML...")
            if convert_md_to_html(md_output_file, output_file):
                print(f"HTML report written to: {output_file}")
            else:
                print(f"Warning: HTML conversion failed, markdown at: {md_output_file}",
                      file=sys.stderr)
        else:
            print(f"Warning: HTML conversion requested but markdown_to_html.py not available",
                  file=sys.stderr)
            print(f"Markdown report available at: {md_output_file}", file=sys.stderr)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
