# environment.py
# -*- coding: utf-8 -*-

import asyncio


class FactoryEnvironment:
    def __init__(self):
        self.time = 0
        self.metrics = {
            # jobs / produção
            "jobs_created": 0,
            "jobs_completed": 0,
            "jobs_lost": 0,

            # supply
            "supply_requests_ok": 0,
            "supply_requests_failed": 0,

            # falhas e manutenção
            "machine_failures": 0,
            "repairs_started": 0,
            "repairs_finished": 0,
            "downtime_ticks": 0,

            # transferência de jobs
            "jobs_transferred": 0,
        }
        self.agents = []

    def register_agent(self, agent):
        """Regista agente no ambiente."""
        self.agents.append(agent)

    async def tick(self):
        """Avança o tempo global 1 unidade."""
        self.time += 1
        await asyncio.sleep(0.1)

