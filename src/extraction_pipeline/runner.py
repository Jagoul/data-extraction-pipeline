"""Drive many extraction jobs to completion, one round of requests at a time.

Each round collects every pending request from every active job, keys it by `custom_id`
(`<job_id>__<request key>`), and sends the round either as one Message Batch or as synchronous
calls, whichever the SLA planner allows. Results come back in any order and are routed to their
job by `custom_id`, never by position. Failed requests are resubmitted in the next round with
whatever modification their job chose: unchanged after an API error or expiry, as chunks after
truncation, or as a correction request after a validation failure.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from extraction_pipeline.config import Settings
from extraction_pipeline.jobs import ExtractionJob
from extraction_pipeline.llm import BatchClient, CallOutcome, CallStatus, MessagesClient
from extraction_pipeline.sla import RoundRecord, SlaPolicy, sla_report

SEPARATOR = "__"
MAX_ROUNDS = 8

ProgressHook = Callable[[str], None]


def custom_id(job: ExtractionJob, key: str) -> str:
    return f"{job.job_id}{SEPARATOR}{key}"


def split_custom_id(value: str) -> tuple[str, str]:
    job_id, key = value.rsplit(SEPARATOR, 1)
    return job_id, key


@dataclass
class RunOutcome:
    jobs: list[ExtractionJob]
    rounds: list[RoundRecord] = field(default_factory=list)
    sla: dict[str, Any] = field(default_factory=dict)

    def results(self) -> list[dict[str, Any]]:
        return [job.result() for job in self.jobs]


class PipelineRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        sync_client: MessagesClient | None = None,
        batch_client: BatchClient | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        progress: ProgressHook = lambda _message: None,
    ) -> None:
        self.settings = settings
        self.sync_client = sync_client
        self.batch_client = batch_client
        self.clock = clock
        self.sleep = sleep
        self.progress = progress
        self.policy = SlaPolicy(sla_seconds=settings.sla_hours * 3600)

    def run(self, jobs: Iterable[ExtractionJob], *, force_sync: bool = False) -> RunOutcome:
        jobs = list(jobs)
        by_id = {job.job_id: job for job in jobs}
        if len(by_id) != len(jobs):
            raise ValueError("job ids must be unique")
        rounds: list[RoundRecord] = []
        start = self.clock()

        for number in range(1, MAX_ROUNDS + 1):
            requests = {
                custom_id(job, key): params
                for job in jobs
                if not job.done
                for key, params in job.pending.items()
            }
            if not requests:
                break
            no_batch_client = self.batch_client is None
            plan = self.policy.plan(
                self.clock() - start, rounds, force_sync=force_sync or no_batch_client
            )
            self.progress(
                f"round {number}: {len(requests)} request(s) via {plan.mode} ({plan.reason})"
            )
            if plan.mode == "batch":
                record = self._batch_round(number, requests, by_id, plan.reason)
            else:
                record = self._sync_round(number, requests, by_id, plan.reason)
            rounds.append(record)
        else:
            for job in jobs:
                if not job.done:
                    job.abandon(f"still unfinished after {MAX_ROUNDS} rounds")

        return RunOutcome(jobs=jobs, rounds=rounds, sla=sla_report(rounds, self.policy.sla_seconds))

    # --- Rounds ------------------------------------------------------------------------------

    def _batch_round(
        self,
        number: int,
        requests: dict[str, dict[str, Any]],
        by_id: dict[str, ExtractionJob],
        reason: str,
    ) -> RoundRecord:
        assert self.batch_client is not None
        batch_id = self.batch_client.submit(requests)
        self.progress(f"  submitted batch {batch_id}")
        while not (status := self.batch_client.status(batch_id)).ended:
            self.progress(f"  {batch_id}: {status.counts.get('processing', '?')} still processing")
            self.sleep(self.settings.poll_seconds)

        seen: set[str] = set()
        for cid, outcome in self.batch_client.results(batch_id):
            seen.add(cid)
            self._deliver(cid, outcome, by_id)
        for cid in requests.keys() - seen:  # never returned: treat as retryable
            self._deliver(cid, CallOutcome(CallStatus.API_ERROR, detail="no result"), by_id)

        started = status.created_at.timestamp()
        ended = status.ended_at.timestamp() if status.ended_at else self.clock()
        self.progress(f"  batch ended: {status.counts}")
        return RoundRecord(number, "batch", len(requests), started, ended, batch_id, reason)

    def _sync_round(
        self,
        number: int,
        requests: dict[str, dict[str, Any]],
        by_id: dict[str, ExtractionJob],
        reason: str,
    ) -> RoundRecord:
        if self.sync_client is None:
            raise RuntimeError("the SLA planner chose synchronous calls, but no sync client is set")
        started = self.clock()
        for cid, params in requests.items():
            self._deliver(cid, self.sync_client.create(params), by_id)
        return RoundRecord(number, "sync", len(requests), started, self.clock(), None, reason)

    def _deliver(self, cid: str, outcome: CallOutcome, by_id: dict[str, ExtractionJob]) -> None:
        job_id, key = split_custom_id(cid)
        job = by_id.get(job_id)
        if job is None or key not in job.pending:
            self.progress(f"  ignoring unexpected result {cid}")
            return
        job.observe(key, outcome)
