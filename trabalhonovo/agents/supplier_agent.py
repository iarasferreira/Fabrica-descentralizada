# agents/supplier_agent.py
# -*- coding: utf-8 -*-

from agents.base_agent import FactoryAgent
from spade.behaviour import CyclicBehaviour
from spade.message import Message

import json
import math
import time


class SupplierAgent(FactoryAgent):
    """
    Supplier que participa no CNP de materiais com as máquinas (supply-cnp)
    e delega o transporte em robots (transport-cnp).
    """

    def __init__(
        self,
        jid,
        password,
        env=None,
        name="Supplier",
        stock_init=None,
        position=(0, 0),
        robots=None,     # lista de JIDs de robots
    ):
        super().__init__(jid, password, env=env)
        self.agent_name = name
        self.position = position

        self.stock = stock_init or {
            "material_1": 100,
            "material_2": 100,
        }

        # Robots disponíveis
        self.robots = robots or []

        # Guardar info das entregas pendentes:
        #   key: thread_id (supply-cnp)
        #   value: {"machine_jid": str, "materials": dict}
        self.pending_deliveries = {}

    async def setup(self):
        await super().setup()
        await self.log(
            f"(Supplier {self.agent_name}) stock={self.stock}, pos={self.position}, "
            f"robots={self.robots}"
        )
        self.add_behaviour(self.CNPBehaviour())

    class CNPBehaviour(CyclicBehaviour):
        async def run(self):
            agent = self.agent
            msg = await self.receive(timeout=1)
            if not msg:
                return

            protocol = msg.metadata.get("protocol")
            pf = msg.metadata.get("performative")
            thread_id = msg.metadata.get("thread")

            # ======================================================
            # 1) CNP-RECURSOS: protocolo 'supply-cnp' (Máquina ↔ Supplier)
            # ======================================================
            if protocol == "supply-cnp":
                try:
                    data = json.loads(msg.body) if msg.body else {}
                except Exception:
                    data = {}

                # CFP → decide PROPOSE / REFUSE
                if pf == "cfp":
                    requested = data.get("materials", {})
                    machine_pos = data.get("machine_pos", [0, 0])

                    # verificar stock suficiente
                    has_stock = True
                    for mat, qty in requested.items():
                        if agent.stock.get(mat, 0) < qty:
                            has_stock = False
                            break

                    if not has_stock:
                        rep = Message(to=str(msg.sender))
                        rep.set_metadata("protocol", "supply-cnp")
                        rep.set_metadata("performative", "refuse")
                        rep.set_metadata("thread", thread_id)
                        rep.body = json.dumps({"reason": "no_stock"})
                        await self.send(rep)

                        await agent.log(
                            f"[SUPPLIER/{agent.agent_name}] REFUSE para {msg.sender} "
                            "(stock insuficiente)"
                        )
                        return

                    # custo = distância + 0.5 * quantidade total
                    dx = machine_pos[0] - agent.position[0]
                    dy = machine_pos[1] - agent.position[1]
                    distance = math.sqrt(dx * dx + dy * dy)
                    total_qty = sum(requested.values())
                    cost = distance + 0.5 * total_qty

                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "supply-cnp")
                    rep.set_metadata("performative", "propose")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps(
                        {
                            "cost": cost,
                            "distance": distance,
                            "can_supply": requested,
                        }
                    )
                    await self.send(rep)

                    await agent.log(
                        f"[SUPPLIER/{agent.agent_name}] PROPOSE para {msg.sender}: "
                        f"cost={cost:.2f}, dist={distance:.2f}, materials={requested}"
                    )
                    return

                # ACCEPT-PROPOSAL → lançar CNP-Transporte com robots
                if pf == "accept-proposal":
                    requested = data.get("materials", {})
                    machine_pos = data.get("machine_pos", [0, 0])

                    # Reservar stock (debitamos já)
                    for mat, qty in requested.items():
                        agent.stock[mat] = max(0, agent.stock.get(mat, 0) - qty)

                    machine_jid = str(msg.sender)
                    safe_thread = thread_id

                    # Guardar info desta entrega pendente
                    agent.pending_deliveries[safe_thread] = {
                        "machine_jid": machine_jid,
                        "materials": requested,
                    }

                    await agent.log(
                        f"[SUPPLIER/{agent.agent_name}] ACCEPT-PROPOSAL de {machine_jid}. "
                        f"Materiais reservados={requested}. Lançando CNP-Transporte..."
                    )

                    # Lançar CNP-Transporte para robots
                    if not agent.robots:
                        await agent.log(
                            "[SUPPLIER] ERRO: não há robots configurados. "
                            "Não é possível entregar materiais."
                        )
                        # Poderias aqui marcar falha e devolver stock, se quiseres
                        return

                    transport_payload = {
                        "kind":"materials", 
                        "supplier_pos": list(agent.position),
                        "machine_pos": list(machine_pos),
                        "materials": requested,
                        "initiator_jid": str(agent.jid),
                    }

                    # Enviar CFP a todos os robots
                    for r_jid in agent.robots:
                        m = Message(to=r_jid)
                        m.set_metadata("protocol", "transport-cnp")
                        m.set_metadata("performative", "cfp")
                        m.set_metadata("thread", safe_thread)
                        m.body = json.dumps(transport_payload)
                        await self.send(m)

                    # Recolher PROPOSE dos robots
                    proposals = []
                    deadline = time.time() + 3  # 3 s

                    while time.time() < deadline:
                        rep = await self.receive(timeout=0.5)
                        if not rep:
                            continue
                        if rep.metadata.get("protocol") != "transport-cnp":
                            continue
                        if rep.metadata.get("thread") != safe_thread:
                            continue

                        pf2 = rep.metadata.get("performative")
                        if pf2 == "propose":
                            try:
                                d2 = json.loads(rep.body) if rep.body else {}
                                cost = float(d2.get("cost", 9999))
                                proposals.append((str(rep.sender), cost))
                            except Exception:
                                continue
                        elif pf2 == "refuse":
                            await agent.log(
                                f"[SUPPLIER] Robot {rep.sender} recusou (busy/low_energy)."
                            )

                    if not proposals:
                        await agent.log(
                            f"[SUPPLIER] Nenhum robot aceitou transportar (thread={safe_thread})."
                        )
                        # Opcional: devolver stock, notificar máquina de falha, etc.
                        return

                    # Escolher robot com menor custo
                    proposals.sort(key=lambda x: x[1])
                    winner_jid, winner_cost = proposals[0]

                    await agent.log(
                        f"[SUPPLIER] Robot vencedor={winner_jid} com cost={winner_cost:.2f}. "
                        "Enviando ACCEPT-PROPOSAL (transporte)."
                    )

                    # REJECT aos outros
                    for r_jid, _ in proposals[1:]:
                        rej = Message(to=r_jid)
                        rej.set_metadata("protocol", "transport-cnp")
                        rej.set_metadata("performative", "reject-proposal")
                        rej.set_metadata("thread", safe_thread)
                        rej.body = json.dumps({"reason": "not_selected"})
                        await self.send(rej)

                    # ACCEPT ao vencedor
                    acc = Message(to=winner_jid)
                    acc.set_metadata("protocol", "transport-cnp")
                    acc.set_metadata("performative", "accept-proposal")
                    acc.set_metadata("thread", safe_thread)
                    acc.body = json.dumps(transport_payload)
                    await self.send(acc)

                    # A PARTIR DAQUI, QUEM CONTINUA O FLUXO É O RAMO 'transport-cnp' / INFORM
                    return

                # REJECT-PROPOSAL da máquina → só log
                if pf == "reject-proposal":
                    await agent.log(
                        f"[SUPPLIER/{agent.agent_name}] REJECT recebido de {msg.sender}"
                    )
                    return

            # ======================================================
            # 2) CNP-TRANSPORTE: protocolo 'transport-cnp' (Supplier ↔ Robots)
            #    Aqui tratamos o INFORM do robot quando terminar o transporte.
            # ======================================================
            if protocol == "transport-cnp":
                # Só precisamos de tratar INFORM (transporte concluído)
                if pf == "inform":
                    info = agent.pending_deliveries.get(thread_id)
                    if not info:
                        await agent.log(
                            f"[SUPPLIER] INFORM de transporte com thread desconhecida: {thread_id}"
                        )
                        return

                    machine_jid = info["machine_jid"]
                    materials = info["materials"]

                    # Remover da lista de pendentes
                    agent.pending_deliveries.pop(thread_id, None)

                    # Enviar INFORM final para a máquina (supply-cnp)
                    inf = Message(to=machine_jid)
                    inf.set_metadata("protocol", "supply-cnp")
                    inf.set_metadata("performative", "inform")
                    inf.set_metadata("thread", thread_id)
                    inf.body = json.dumps(
                        {
                            "status": "delivered",
                            "materials": materials,
                        }
                    )
                    await self.send(inf)

                    await agent.log(
                        f"[SUPPLIER/{agent.agent_name}] Transporte concluído. "
                        f"INFORM (delivered) enviado para {machine_jid}. "
                        f"Stock atual={agent.stock}"
                    )
                    return
