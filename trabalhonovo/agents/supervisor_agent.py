# agents/supervisor_agent.py
# -*- coding: utf-8 -*-

import asyncio
import json
import random

from agents.base_agent import FactoryAgent
from spade.behaviour import CyclicBehaviour
from spade.message import Message


class SupervisorAgent(FactoryAgent):
    def __init__(
        self,
        jid,
        password,
        env=None,
        machines=None,
        job_dispatch_every=5,
    ):
        super().__init__(jid, password, env=env)
        self.machines = machines or []      # lista de JIDs (strings)
        self.job_dispatch_every = job_dispatch_every
        self.last_dispatch_time = 0
        self.job_counter = 0

    async def setup(self):
        await super().setup()
        await self.log(
            f"Supervisor pronto. job_dispatch_every={self.job_dispatch_every}, "
            f"máquinas={self.machines}"
        )
        self.add_behaviour(self.JobDispatcher())

    class JobDispatcher(CyclicBehaviour):
        async def run(self):
            agent = self.agent
            env = agent.env
            t = env.time

            # de N em N ticks lança um novo job
            if t > 0 and (t - agent.last_dispatch_time) >= agent.job_dispatch_every:
                agent.last_dispatch_time = t
                agent.job_counter += 1

                job_id = agent.job_counter
                job_spec = {
                    "job_id": job_id,
                    "duration": random.randint(3, 8),
                }

                thread_id = f"job-dispatch-{job_id}-{t}"

                # Enviar CFP para todas as máquinas
                for m_jid in agent.machines:
                    msg = Message(to=m_jid)
                    msg.set_metadata("protocol", "job-dispatch")
                    msg.set_metadata("performative", "cfp")
                    msg.set_metadata("thread", thread_id)
                    msg.body = json.dumps(job_spec)
                    await self.send(msg)

                await agent.log(
                    f"[SCHEDULER] CFP enviado para máquinas {agent.machines} "
                    f"com job_id={job_id}, duration={job_spec['duration']}"
                )

                if env:
                    env.metrics["jobs_created"] += 1

                # Recolher PROPOSE / REFUSE
                proposals = []
                deadline = asyncio.get_event_loop().time() + 3  # 3s para respostas

                while asyncio.get_event_loop().time() < deadline:
                    rep = await self.receive(timeout=0.5)
                    if not rep:
                        continue
                    if rep.metadata.get("protocol") != "job-dispatch":
                        continue
                    if rep.metadata.get("thread") != thread_id:
                        continue

                    pf = rep.metadata.get("performative")
                    try:
                        data = json.loads(rep.body) if rep.body else {}
                    except Exception:
                        data = {}

                    if pf == "propose":
                        cost = data.get("cost")
                        if cost is not None:
                            proposals.append((str(rep.sender), cost))
                    elif pf == "refuse":
                        await agent.log(
                            f"[SCHEDULER] REFUSE de {rep.sender} "
                            f"(motivo={data.get('reason')})"
                        )

                if not proposals:
                    await agent.log(
                        f"[SCHEDULER] Nenhuma máquina aceitou job {job_id}."
                    )
                    await asyncio.sleep(0.5)
                    return

                # Escolher máquina com menor custo (tie-break aleatório)
                min_cost = min(cost for _, cost in proposals)
                best_candidates = [
                    (jid, cost) for jid, cost in proposals if cost == min_cost
                ]
                winner_jid, winner_cost = random.choice(best_candidates)

                await agent.log(
                    f"[SCHEDULER] Máquina escolhida para job {job_id}: "
                    f"{winner_jid} (cost={winner_cost}, candidatos={proposals})"
                )

                # Enviar ACCEPT ao vencedor e REJECT aos outros
                for jid, _ in proposals:
                    if jid == winner_jid:
                        acc = Message(to=jid)
                        acc.set_metadata("protocol", "job-dispatch")
                        acc.set_metadata("performative", "accept-proposal")
                        acc.set_metadata("thread", thread_id)
                        acc.body = json.dumps(job_spec)
                        await self.send(acc)
                    else:
                        rej = Message(to=jid)
                        rej.set_metadata("protocol", "job-dispatch")
                        rej.set_metadata("performative", "reject-proposal")
                        rej.set_metadata("thread", thread_id)
                        rej.body = json.dumps({"reason": "not_selected"})
                        await self.send(rej)

            await asyncio.sleep(0.5)
