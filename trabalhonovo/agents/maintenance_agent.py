# agents/maintenance_agent.py
# -*- coding: utf-8 -*-

import asyncio
import json
import math

from spade.behaviour import CyclicBehaviour
from spade.message import Message

from agents.base_agent import FactoryAgent


class MaintenanceAgent(FactoryAgent):
    """
    Maintenance Crew:

    - Protocolo 'maintenance-cnp' iniciado pelas máquinas que falham.
    - Recebe CFP com info da máquina (jid, posição).
    - Se disponível:
        * calcula custo com base na distância + tempo de reparação
        * responde PROPOSE
    - Se for escolhida:
        * recebe ACCEPT-PROPOSAL com repair_time
        * simula a reparação (RepairExecutor)
        * envia INFORM 'repair_completed' para a máquina.
    """

    def __init__(
        self,
        jid,
        password,
        env=None,
        name="MaintenanceCrew",
        position=(0, 0),
        repair_time_range=(3, 8),
    ):
        super().__init__(jid, password, env=env)
        self.agent_name = name
        self.position = position
        self.repair_time_range = repair_time_range

        self.crew_status = "available"  # "available" | "busy"
        self.current_repair = None
        self.repair_ticks_remaining = 0

    async def setup(self):
        await super().setup()
        await self.log(
            f"[MAINT] {self.agent_name} pronto. "
            f"pos={self.position}, status={self.crew_status}"
        )
        self.add_behaviour(self.MaintenanceParticipant())
        self.add_behaviour(self.RepairExecutor())

    # ----------------------------------------------------------
    # CNP participant: responde a CFP/ACCEPT/REJECT
    # ----------------------------------------------------------
    class MaintenanceParticipant(CyclicBehaviour):
        async def run(self):
            agent = self.agent
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            if msg.metadata.get("protocol") != "maintenance-cnp":
                return

            pf = msg.metadata.get("performative")
            thread_id = msg.metadata.get("thread")

            try:
                data = json.loads(msg.body) if msg.body else {}
            except Exception:
                data = {}

            # CFP → decidir se faz PROPOSE ou REFUSE
            if pf == "cfp":
                machine_jid = data.get("machine_jid")
                machine_pos = data.get("machine_pos", [0, 0])

                if agent.crew_status != "available":
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "maintenance-cnp")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": agent.crew_status})
                    await self.send(rep)

                    await agent.log(
                        f"[MAINT-CNP] REFUSE para {machine_jid} (status={agent.crew_status})"
                    )
                    return

                dx = machine_pos[0] - agent.position[0]
                dy = machine_pos[1] - agent.position[1]
                distance = math.sqrt(dx * dx + dy * dy)

                repair_time = random_int_in_range(agent.repair_time_range)
                cost = distance + repair_time

                rep = Message(to=str(msg.sender))
                rep.set_metadata("protocol", "maintenance-cnp")
                rep.set_metadata("performative", "propose")
                rep.set_metadata("thread", thread_id)
                rep.body = json.dumps(
                    {
                        "crew": agent.agent_name,
                        "cost": cost,
                        "repair_time": repair_time,
                        "distance": distance,
                    }
                )
                await self.send(rep)

                await agent.log(
                    f"[MAINT-CNP] PROPOSE para {machine_jid}: "
                    f"cost={cost:.2f}, repair_time={repair_time}, dist={distance:.2f}"
                )
                return

            # ACCEPT-PROPOSAL → agendar reparação
            if pf == "accept-proposal":
                machine_jid = data.get("machine_jid")
                machine_pos = data.get("machine_pos", [0, 0])
                repair_time = int(data.get("repair_time", 5))

                agent.current_repair = {
                    "machine_jid": machine_jid,
                    "machine_pos": machine_pos,
                    "thread": thread_id,
                }
                agent.repair_ticks_remaining = repair_time
                agent.crew_status = "busy"

                await agent.log(
                    f"[MAINT-CNP] ACCEPT de {machine_jid}. "
                    f"Iniciar reparação ({repair_time} ticks)."
                )

                # Opcional: informar que começou
                inf = Message(to=machine_jid)
                inf.set_metadata("protocol", "maintenance-cnp")
                inf.set_metadata("performative", "inform")
                inf.set_metadata("thread", thread_id)
                inf.body = json.dumps({"status": "repair_started"})
                await self.send(inf)
                return

            # REJECT-PROPOSAL → só log
            if pf == "reject-proposal":
                await agent.log("[MAINT-CNP] REJECT recebido.")
                return

    # ----------------------------------------------------------
    # Executor da reparação
    # ----------------------------------------------------------
    class RepairExecutor(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            if agent.current_repair is None:
                agent.crew_status = "available"
                await asyncio.sleep(0.5)
                return

            # avançar 1 tick de reparação
            if agent.repair_ticks_remaining > 0:
                agent.repair_ticks_remaining -= 1
                await agent.log(
                    f"[REPAIR] Reparando {agent.current_repair['machine_jid']}: "
                    f"{agent.repair_ticks_remaining} ticks restantes."
                )

            if agent.repair_ticks_remaining <= 0:
                machine_jid = agent.current_repair["machine_jid"]
                thread_id = agent.current_repair["thread"]

                inf = Message(to=machine_jid)
                inf.set_metadata("protocol", "maintenance-cnp")
                inf.set_metadata("performative", "inform")
                inf.set_metadata("thread", thread_id)
                inf.body = json.dumps({"status": "repair_completed"})
                await self.send(inf)

                await agent.log(
                    f"[REPAIR] Reparação concluída para {machine_jid}. "
                    "INFORM 'repair_completed' enviado."
                )

                agent.current_repair = None
                agent.crew_status = "available"

                if agent.env:
                    agent.env.metrics["repairs_finished"] += 1

            await asyncio.sleep(1)


def random_int_in_range(rng):
    """Helper simples para randint sem ter de importar random aqui em cima."""
    import random
    return random.randint(rng[0], rng[1])
