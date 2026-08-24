"""MYRA background task implementations.

Phase 2 orchestrator refactor: each task's body was extracted verbatim from
background_orchestrator.py into a `run(ctx: TaskContext)` entrypoint.
The orchestrator keeps thin `_task_*` wrappers delegating to these modules.
"""
