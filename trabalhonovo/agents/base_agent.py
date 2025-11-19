# agents/base_agent.py
# -*- coding: utf-8 -*-

from spade.agent import Agent
import datetime


class FactoryAgent(Agent):
    def __init__(self, jid, password, env=None):
        super().__init__(jid, password)
        self.env = env

    async def setup(self):
        """Chamada quando o agente arranca."""
        if self.env:
            self.env.register_agent(self)
        await self.log("iniciado.")

    async def log(self, msg: str):
        """Log simples com timestamp e jid do agente."""
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}][{self.jid}] {msg}")
