from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from backend.app.harness_lab.bootstrap import harness_lab_services  # noqa: E402
from backend.app.harness_lab.types import RunRequest, SessionRequest, WorkerRegisterRequest  # noqa: E402
from backend.app.harness_lab.workers.runtime_client import WorkerExecutionLoop, WorkerRuntimeClient  # noqa: E402


def _emit(payload: Any, output_format: str = "text") -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
        return
    if isinstance(payload, list):
        for item in payload:
            print(item)
        return
    print(payload)


def _latest_run_id() -> str | None:
    runs = harness_lab_services.runtime.list_runs(limit=1)
    return runs[0].run_id if runs else None


def _latest_session_id() -> str | None:
    sessions = harness_lab_services.runtime.list_sessions(limit=1)
    return sessions[0].session_id if sessions else None


def _default_control_plane_url() -> str:
    configured = os.getenv("HARNESS_CONTROL_PLANE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    port = os.getenv("PORT", "4600").strip() or "4600"
    return f"http://127.0.0.1:{port}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hlab", description="Harness Lab CLI control surface")
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="Create a session and execute a run")
    submit.add_argument("goal")
    submit.add_argument("--path", dest="path_hint", default="")
    submit.add_argument("--shell", dest="shell_command", default="")
    submit.add_argument("--execution-mode", default="single_worker")

    subparsers.add_parser("doctor", help="Run local control-plane diagnostics")

    attach = subparsers.add_parser("attach", help="Inspect the latest or requested run")
    attach.add_argument("--run-id", default="")

    eval_cmd = subparsers.add_parser("eval", help="Run offline replay or benchmark evaluation")
    eval_cmd.add_argument("--suite", choices=["replay", "benchmark"], default="replay")
    eval_cmd.add_argument("--candidate-id", default="")
    eval_cmd.add_argument("--trace-ref", action="append", default=[])

    subparsers.add_parser("candidates", help="List improvement candidates")

    promote = subparsers.add_parser("promote", help="Publish a candidate")
    promote.add_argument("candidate_id")

    rollback = subparsers.add_parser("rollback", help="Rollback a candidate")
    rollback.add_argument("candidate_id")

    subparsers.add_parser("approvals", help="List approval inbox")
    leases = subparsers.add_parser("leases", help="List worker leases")
    leases.add_argument("--status", default="")
    leases.add_argument("--worker-id", default="")

    workers = subparsers.add_parser("workers", help="List or register workers")
    workers.add_argument("--register", action="store_true")
    workers.add_argument("--label", default="cli-worker")
    workers.add_argument("--capability", action="append", default=[])

    worker = subparsers.add_parser("worker", help="Operate a remote-style worker daemon")
    worker_subparsers = worker.add_subparsers(dest="worker_command", required=True)

    worker_register = worker_subparsers.add_parser("register", help="Register a worker")
    worker_register.add_argument("--worker-id", default="")
    worker_register.add_argument("--label", default="cli-worker")
    worker_register.add_argument("--capability", action="append", default=[])
    worker_register.add_argument("--control-plane-url", default="")

    worker_status = worker_subparsers.add_parser("status", help="Inspect worker status")
    worker_status.add_argument("--worker-id", default="")
    worker_status.add_argument("--control-plane-url", default="")

    worker_serve = worker_subparsers.add_parser("serve", help="Run a polling worker daemon")
    worker_serve.add_argument("--worker-id", default="")
    worker_serve.add_argument("--label", default="cli-worker")
    worker_serve.add_argument("--capability", action="append", default=[])
    worker_serve.add_argument("--control-plane-url", default="")
    worker_serve.add_argument("--interval", type=float, default=1.0)
    worker_serve.add_argument("--once", action="store_true")
    worker_serve.add_argument("--max-tasks", type=int, default=1)

    runs = subparsers.add_parser("runs", help="Inspect run execution state")
    run_subparsers = runs.add_subparsers(dest="runs_command", required=True)
    runs_watch = run_subparsers.add_parser("watch", help="Watch a run until it finishes")
    runs_watch.add_argument("--run-id", default="")
    runs_watch.add_argument("--interval", type=float, default=1.0)
    runs_watch.add_argument("--once", action="store_true")

    subparsers.add_parser("serve", help="Start the FastAPI control plane")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "doctor":
        _emit(harness_lab_services.doctor_report(), args.output_format)
        return

    if args.command == "submit":
        context: dict[str, Any] = {}
        if args.path_hint:
            context["path"] = args.path_hint
        if args.shell_command:
            context["shell_command"] = args.shell_command
        session = harness_lab_services.runtime.create_session(
            SessionRequest(
                goal=args.goal,
                context=context,
                execution_mode=args.execution_mode,
            )
        )
        run = asyncio.run(harness_lab_services.runtime.create_run(RunRequest(session_id=session.session_id)))
        _emit(
            {
                "session_id": session.session_id,
                "run_id": run.run_id,
                "status": run.status,
                "policy_id": run.policy_id,
                "workflow_template_id": run.workflow_template_id,
                "assigned_worker_id": run.assigned_worker_id,
            },
            args.output_format,
        )
        return

    if args.command == "attach":
        run_id = args.run_id or _latest_run_id()
        if not run_id:
            raise SystemExit("No run is available to attach to.")
        run = harness_lab_services.runtime.get_run(run_id)
        _emit(run.model_dump(), args.output_format)
        return

    if args.command == "eval":
        latest_run_id = _latest_run_id()
        trace_refs = args.trace_ref or ([] if args.candidate_id else [latest_run_id] if latest_run_id else [])
        report = harness_lab_services.improvement.evaluate_candidate(
            suite=args.suite,
            candidate_id=args.candidate_id or None,
            trace_refs=trace_refs,
        )
        _emit(report.model_dump(), args.output_format)
        return

    if args.command == "candidates":
        _emit([item.model_dump() for item in harness_lab_services.improvement.list_candidates()], args.output_format)
        return

    if args.command == "promote":
        try:
            candidate = harness_lab_services.improvement.publish_candidate(args.candidate_id)
        except ValueError as exc:
            try:
                gate = harness_lab_services.improvement.get_candidate_gate(args.candidate_id)
                payload = {"error": str(exc), "gate": gate.model_dump()}
            except ValueError:
                payload = {"error": str(exc)}
            _emit(payload, args.output_format)
            raise SystemExit(1) from exc
        _emit(candidate.model_dump(), args.output_format)
        return

    if args.command == "rollback":
        candidate = harness_lab_services.improvement.rollback_candidate(args.candidate_id)
        _emit(candidate.model_dump(), args.output_format)
        return

    if args.command == "approvals":
        _emit([item.model_dump() for item in harness_lab_services.runtime.list_approvals()], args.output_format)
        return

    if args.command == "leases":
        _emit(
            [
                item.model_dump()
                for item in harness_lab_services.runtime.list_leases(
                    status=args.status or None,
                    worker_id=args.worker_id or None,
                )
            ],
            args.output_format,
        )
        return

    if args.command == "workers":
        if args.register:
            worker = harness_lab_services.workers.register_worker(
                WorkerRegisterRequest(label=args.label, capabilities=args.capability, version="v1")
            )
            _emit(worker.model_dump(), args.output_format)
            return
        _emit([item.model_dump() for item in harness_lab_services.workers.list_workers()], args.output_format)
        return

    if args.command == "worker":
        if args.worker_command == "register":
            control_plane_url = args.control_plane_url.strip()
            if control_plane_url:
                client = WorkerRuntimeClient(control_plane_url)
                loop = WorkerExecutionLoop(client, poll_interval_seconds=1.0)
                worker = loop.register(
                    worker_id=args.worker_id or None,
                    label=args.label,
                    capabilities=args.capability,
                )
                _emit(worker.model_dump(), args.output_format)
                return
            worker = harness_lab_services.workers.register_worker(
                WorkerRegisterRequest(
                    worker_id=args.worker_id or None,
                    label=args.label,
                    capabilities=args.capability,
                    version="v1",
                )
            )
            _emit(worker.model_dump(), args.output_format)
            return

        if args.worker_command == "status":
            if args.control_plane_url.strip():
                client = WorkerRuntimeClient(args.control_plane_url.strip())
                worker_detail = client.get_worker(args.worker_id)
                _emit(worker_detail, args.output_format)
                return
            if args.worker_id:
                recent_leases = harness_lab_services.runtime.list_leases(worker_id=args.worker_id)[-5:]
                recent_lease_ids = {lease.lease_id for lease in recent_leases}
                _emit(
                    {
                        "worker": harness_lab_services.workers.get_worker(args.worker_id).model_dump(),
                        "health_summary": harness_lab_services.runtime.get_worker_health_summary(args.worker_id).model_dump(),
                        "recent_leases": [lease.model_dump() for lease in recent_leases],
                        "recent_events": [
                            event.model_dump()
                            for event in harness_lab_services.runtime.list_events(limit=500)
                            if event.payload.get("worker_id") == args.worker_id
                            or event.payload.get("lease_id") in recent_lease_ids
                        ][-10:],
                    },
                    args.output_format,
                )
                return
            _emit([item.model_dump() for item in harness_lab_services.workers.list_workers()], args.output_format)
            return

        if args.worker_command == "serve":
            control_plane_url = args.control_plane_url.strip() or _default_control_plane_url()
            client = WorkerRuntimeClient(control_plane_url)
            loop = WorkerExecutionLoop(client, poll_interval_seconds=max(0.1, args.interval))
            result = loop.serve(
                worker_id=args.worker_id or None,
                label=args.label,
                capabilities=args.capability,
                interval_seconds=max(0.1, args.interval),
                once=args.once,
                max_tasks=max(1, args.max_tasks),
            )
            result["control_plane_url"] = control_plane_url
            _emit(result, args.output_format)
            return

    if args.command == "runs" and args.runs_command == "watch":
        run_id = args.run_id or _latest_run_id()
        if not run_id:
            raise SystemExit("No run is available to watch.")
        while True:
            run = harness_lab_services.runtime.get_run(run_id)
            payload = {
                "run_id": run.run_id,
                "status": run.status,
                "assigned_worker_id": run.assigned_worker_id,
                "current_attempt_id": run.current_attempt_id,
                "active_lease_id": run.active_lease_id,
                "coordination_snapshot": harness_lab_services.runtime.run_coordination_snapshot(run_id).model_dump(),
                "timeline_summary": harness_lab_services.runtime.run_timeline_summary(run_id),
                "status_summary": harness_lab_services.runtime.run_status_summary(run_id),
                "result": run.result,
            }
            _emit(payload, args.output_format)
            if args.once or run.status in {"completed", "failed", "awaiting_approval", "cancelled"}:
                return
            time.sleep(max(0.1, args.interval))

    if args.command == "serve":
        from backend.app.main import main as serve_main

        serve_main()
        return


if __name__ == "__main__":
    main()
