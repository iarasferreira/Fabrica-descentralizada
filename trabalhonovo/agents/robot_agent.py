# agents/robot_agent.py
# -*- coding: utf-8 -*-

from agents.base_agent import FactoryAgent
from spade.behaviour import CyclicBehaviour
from spade.message import Message

import asyncio
import json
import math


def euclidean_distance(p1, p2):
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    return math.sqrt(dx * dx + dy * dy)


class RobotAgent(FactoryAgent):
    """
    Robot de transporte genérico.

    - Protocolo único 'transport-cnp':
      * CFP: payload com:
          kind: "materials" ou "job"
          origin_pos / supplier_pos
          dest_pos / machine_pos
          (opcional) materials
          (opcional) job + destination_jid
      * PROPOSE: responde com cost calculado a partir da distância
      * ACCEPT-PROPOSAL: executa o transporte, consome energia
      * INFORM: devolve 'status=delivered' ao iniciador

    - Energia:
      * energy ∈ [0, 100]
      * cada transporte consome energia proporcional à distância
      * se não tiver energia suficiente → REFUSE ("low_energy")
      * quando está livre, recarrega automaticamente até 100
    """

    def __init__(
        self,
        jid,
        password,
        env=None,
        name="Robot",
        position=(0, 0),
        speed=1.0,          # fator p/ tempo de viagem
        max_energy=100,
        recharge_rate=5,    # % por tick quando livre
        energy_per_unit=1.0,
        low_energy_threshold=5,
    ):
        super().__init__(jid, password, env=env)
        self.agent_name = name
        self.position = tuple(position)

        self.speed = speed

        # energia
        self.max_energy = max_energy
        self.energy = max_energy
        self.recharge_rate = recharge_rate
        self.energy_per_unit = energy_per_unit
        self.low_energy_threshold = low_energy_threshold

        self.busy = False

    async def setup(self):
        await super().setup()
        await self.log(
            f"[ROBOT] {self.agent_name} pronto. "
            f"pos={self.position}, energy={self.energy}%"
        )
        self.add_behaviour(self.TransportManagerBehaviour())

    class TransportManagerBehaviour(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            # Recarregar se estiver livre e não a 100%
            if not agent.busy and agent.energy < agent.max_energy:
                prev = agent.energy
                agent.energy = min(
                    agent.max_energy, agent.energy + agent.recharge_rate
                )
                if agent.energy != prev:
                    await agent.log(
                        f"[ENERGY] a recarregar: {prev}% → {agent.energy}%"
                    )

            msg = await self.receive(timeout=0.3)
            if not msg:
                await asyncio.sleep(0.2)
                return

            if msg.metadata.get("protocol") != "transport-cnp":
                return

            pf = msg.metadata.get("performative")
            thread_id = msg.metadata.get("thread")

            try:
                task = json.loads(msg.body) if msg.body else {}
            except Exception:
                task = {}

            kind = task.get("kind", "materials")

            # Determinar origem/destino para cálculo de distância
            if kind == "materials":
                origin = task.get("supplier_pos", [0, 0])
                dest = task.get("machine_pos", [0, 0])
            else:  # "job"
                origin = task.get("origin_pos", [0, 0])
                dest = task.get("dest_pos", [0, 0])

            d1 = euclidean_distance(agent.position, origin)
            d2 = euclidean_distance(origin, dest)
            total_dist = d1 + d2
            energy_needed = max(
                1, int(math.ceil(total_dist * agent.energy_per_unit))
            )

            # CFP → PROPOSE / REFUSE
            if pf == "cfp":
                if agent.busy:
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "transport-cnp")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": "busy"})
                    await self.send(rep)
                    return

                if agent.energy < energy_needed or agent.energy <= agent.low_energy_threshold:
                    # Sem energia → recusa e continua a recarregar
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "transport-cnp")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": "low_energy"})
                    await self.send(rep)

                    await agent.log(
                        f"[ROBOT] REFUSE (low_energy) para {msg.sender} "
                        f"(energy={agent.energy}%, needed={energy_needed}%)"
                    )
                    return

                # custo: distância + termo opcional com quantidade de materiais
                total_qty = 0
                if kind == "materials":
                    total_qty = sum(task.get("materials", {}).values())

                cost = total_dist + 0.1 * total_qty

                rep = Message(to=str(msg.sender))
                rep.set_metadata("protocol", "transport-cnp")
                rep.set_metadata("performative", "propose")
                rep.set_metadata("thread", thread_id)
                rep.body = json.dumps(
                    {
                        "cost": cost,
                        "distance": total_dist,
                        "energy_needed": energy_needed,
                    }
                )
                await self.send(rep)

                await agent.log(
                    f"[ROBOT] PROPOSE para {msg.sender}: cost={cost:.2f}, "
                    f"dist={total_dist:.2f}, energy_needed={energy_needed} "
                    f"(energy atual={agent.energy}%)"
                )
                return

            # ACCEPT-PROPOSAL → executar transporte
            if pf == "accept-proposal":
                agent.busy = True

                # Segurança: recalcular energy_needed
                if agent.energy < energy_needed:
                    await agent.log(
                        f"[ROBOT] ERRO: energy insuficiente na hora do ACCEPT "
                        f"(energy={agent.energy}%, needed={energy_needed}%)."
                    )
                    # Falha "tardia": envia REFUSE de fallback
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "transport-cnp")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": "late_low_energy"})
                    await self.send(rep)
                    agent.busy = False
                    return

                # Consome energia
                prev_e = agent.energy
                agent.energy = max(0, agent.energy - energy_needed)

                await agent.log(
                    f"[ROBOT] ACCEPT-PROPOSAL recebido. "
                    f"Vai transportar kind={kind}, dist={total_dist:.2f}, "
                    f"energy {prev_e}% → {agent.energy}%"
                )

                travel_time = max(0.5, total_dist * agent.speed)
                await asyncio.sleep(travel_time)

                # Atualizar posição para o destino final
                agent.position = tuple(dest)

                # INFORM para o iniciador (supplier ou máquina)
                inf = Message(to=str(msg.sender))
                inf.set_metadata("protocol", "transport-cnp")
                inf.set_metadata("performative", "inform")
                inf.set_metadata("thread", thread_id)
                inf.body = json.dumps({"status": "delivered", "kind": kind})
                await self.send(inf)

                # Se for job, também notifica a máquina destino com o próprio job
                if kind == "job":
                    job = task.get("job", {})
                    dest_jid = task.get("destination_jid")

                    if dest_jid and job:
                        job_msg = Message(to=dest_jid)
                        job_msg.set_metadata("protocol", "job-transfer")
                        job_msg.set_metadata("performative", "inform")
                        job_msg.set_metadata("thread", thread_id)
                        job_msg.body = json.dumps(
                            {"status": "job_delivered", "job": job}
                        )
                        await self.send(job_msg)

                        await agent.log(
                            f"[ROBOT] Job {job.get('id')} entregue a {dest_jid} "
                            f"(via job-transfer)."
                        )

                await agent.log(
                    f"[ROBOT] Transporte concluído. "
                    f"thread={thread_id}, kind={kind}, pos_final={agent.position}"
                )

                agent.busy = False
                return

            # REJECT-PROPOSAL → apenas log
            if pf == "reject-proposal":
                await agent.log(
                    f"[ROBOT] REJECT recebido de {msg.sender} "
                    f"(thread={thread_id})."
                )
                return
