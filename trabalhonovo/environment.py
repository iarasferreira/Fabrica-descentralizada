# environment.py
# -*- coding: utf-8 -*-
import asyncio
import random

class FactoryEnvironment:

    def __init__(self):
        self.time = 0
        self.metrics = {
        # suppliers / CNP
        "requests_ok": 0,
        "requests_refused": 0,
        "delivered_flour": 0,
        "delivered_sugar": 0,
        "delivered_butter": 0,

        # máquinas / manutenção
        "machine_failures": 0,
        "repairs_started": 0,
        "repairs_finished": 0,
        "downtime_ticks": 0,   

        # CNP com suppliers
        "cnp_cfp": 0,
        "cnp_accepts": 0,

        # jobs
        "jobs_completed": 0,
        "jobs_delegated": 0,
        "jobs_lost": 0,
        "jobs_redistributed": 0,
        "jobs_received": 0,
    }


        self.agents = []
        self.maintenance_crews = []  # lista de maintenance crews
        self.external_failure_rate = 0.0
        self.global_job_id = 0

    def register_agent(self, agent):
        self.agents.append(agent)

    def get_new_job_id(self):
        self.global_job_id += 1
        return self.global_job_id


    async def tick(self):
        """Avança 1 tick no tempo e aplica falhas externas (se configuradas)."""
        self.time += 1

        for m in self.agents:
            if not getattr(m, "is_machine", False):
                continue

            # Falhas externas opcionais (se quiseres este efeito global)
            if (
                self.external_failure_rate > 0
                and not m.is_failed
                and random.random() < self.external_failure_rate
            ):
                m.is_failed = True
                self.metrics["machine_failures"] += 1

                await m.log(
                    f"[FAILURE] {m.agent_name} falhou (detetado pelo ambiente)."
                )

                if hasattr(m, "try_delegate_current_job"):
                    await m.try_delegate_current_job()

        await asyncio.sleep(0.1)

