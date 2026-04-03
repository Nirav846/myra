---
name: debugging-code
description: Use a real debugger (via dap CLI) to set breakpoints, step through execution, and inspect live state. Use this when print-statement debugging is insufficient or when investigating complex logic bugs.
author: AlmogBaku
---

# Debugging with dap

You are an expert at using the `dap` CLI to debug applications. Instead of guessing or adding print statements, you should use the debugger to observe the actual state of the running program.

## When to use
- When a bug is hard to reproduce with static analysis.
- When you need to see the value of variables at a specific line of code.
- When you need to trace the execution flow (stepping in/out/over).
- When investigating stack traces or crashes.

## Workflow
1. **Identify the target:** Determine the entry point (script, test, or binary).
2. **Set Breakpoints:** Use `dap break <file>:<line>`.
3. **Start Debugging:** Use `dap debug <command>`.
4. **Inspect State:** Observe local variables and call stack.
5. **Navigate:** Use `dap step`, `dap next`, or `dap continue`.
6. **Evaluate:** Use `dap eval "<expression>"` to check hypotheses.

## Best Practices
- Always set at least one breakpoint before calling `dap debug`.
- Use `dap eval` to test hypotheses about why a condition is failing.
