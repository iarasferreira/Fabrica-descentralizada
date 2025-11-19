# agents/machine_agent.py
# -*- coding: utf-8 -*-

from agents.base_agent import FactoryAgent
from spade.behaviour import CyclicBehaviour
from spade.message import Message

import asyncio
import json
import random


class MachineAgent(FactoryAgent):
    def __init__(
        self,
        jid,
        password,
        env=None,
        name="Machine",
        position=(0, 0),
        suppliers=None,
        material_requirements=None,
        max_queue=15,
        failure_rate=0.05,
        maintenance_crews=None,  # lista de JIDs das crew
        robots=None,  # lista de JIDs dos robots
    ):
        super().__init__(jid, password, env=env)
        self.agent_name = name
        self.position = position

        # fila de jobs
        self.job_queue = []
        self.current_job = None
        self.current_job_ticks = 0

        self.max_queue = max_queue

        # suppliers conhecidos (lista de JIDs em string)
        self.suppliers = suppliers or []

        # materiais por job
        self.material_requirements = material_requirements or {
            "material_1": 10,
            "material_2": 5,
        }

        # --- Pipeline ---
        self.pipeline_template = ["step0", "step1", "step2", "step3"]
        self.step_durations = {
            "step0": 1,
            "step1": 3,
            "step2": 4,
            "step3": 2,
        }

        # --- Falhas / Manutenção ---
        self.failure_rate = failure_rate
        self.is_failed = False
        self.maintenance_crews = maintenance_crews or []
        self.robots = robots or []
        self.maintenance_requested = False
        self.repair_in_progress = False

        # --- Transferência de jobs ---
        self.job_transfer_done = False  # se já tentou/fez transferência do job atual

        # marcar que é máquina
        self.is_machine = True

    async def setup(self):
        await super().setup()
        await self.log(
            f"Machine {self.agent_name} pronta. pos={self.position}, "
            f"max_queue={self.max_queue}, suppliers={self.suppliers}, "
            f"pipeline={self.pipeline_template}, failure_rate={self.failure_rate}"
        )

        self.add_behaviour(self.JobDispatchParticipant())
        self.add_behaviour(self.JobExecutor())
        self.add_behaviour(self.MaintenanceRequester())
        self.add_behaviour(self.MaintenanceResponseHandler())
        self.add_behaviour(self.JobTransferInitiator())
        self.add_behaviour(self.JobTransferParticipant())
        self.add_behaviour(self.JobTransferReceiver())

    # ==========================================================
    # FUNÇÃO AUXILIAR: registar falha
    # ==========================================================
    async def handle_failure(self, job):
        """Marca a máquina como falhada e prepara manutenção/transferência."""
        if self.is_failed:
            return

        self.is_failed = True
        self.maintenance_requested = False
        self.job_transfer_done = False

        if self.env:
            self.env.metrics["machine_failures"] += 1

        step = job["pipeline"][job["current_step_idx"]]
        await self.log(
            f"[FAILURE] {self.agent_name} falhou durante job {job['id']} "
            f"na etapa {step}."
        )

    async def transfer_job_via_robots(self, job, dest_machine_jid, beh):
        """
        Usa robots (transport-cnp, kind='job') para transportar o job
        desta máquina até dest_machine_jid.
        - job: dicionário com estado (id, pipeline, current_step_idx, remaining_ticks, ...)
        - dest_machine_jid: JID em string
        - beh: behaviour que chama (para usar send/receive)
        """
        if not self.robots:
            await self.log("[TRANSFER/ROBOT] ERRO: nenhum robot configurado.")
            return False

        # descobrir posição da máquina destino
        dest_pos = None
        if self.env:
            for a in self.env.agents:
                if getattr(a, "is_machine", False) and str(a.jid) == dest_machine_jid:
                    dest_pos = list(getattr(a, "position", [0, 0]))
                    break

        if dest_pos is None:
            await self.log(
                f"[TRANSFER/ROBOT] ERRO: não encontrei posição da máquina {dest_machine_jid}."
            )
            return False

        origin_pos = list(self.position)

        # construir payload genérico para o robot
        task = {
            "kind": "job",
            "origin_pos": origin_pos,
            "dest_pos": dest_pos,
            "job": job,
            "destination_jid": dest_machine_jid,
        }

        thread_id = f"jobtrans-{job['id']}-{self.env.time if self.env else random.randint(0,9999)}"

        # Enviar CFP para todos os robots
        for r_jid in self.robots:
            m = Message(to=r_jid)
            m.set_metadata("protocol", "transport-cnp")
            m.set_metadata("performative", "cfp")
            m.set_metadata("thread", thread_id)
            m.body = json.dumps(task)
            await beh.send(m)

        await self.log(
            f"[TRANSFER/ROBOT] CFP enviado aos robots {self.robots} "
            f"para job {job['id']} (thread={thread_id})."
        )

        # Recolher PROPOSE
        proposals = []
        deadline = asyncio.get_event_loop().time() + 3

        while asyncio.get_event_loop().time() < deadline:
            rep = await beh.receive(timeout=0.5)
            if not rep:
                continue
            if rep.metadata.get("protocol") != "transport-cnp":
                continue
            if rep.metadata.get("thread") != thread_id:
                continue

            pf = rep.metadata.get("performative")
            try:
                data = json.loads(rep.body) if rep.body else {}
            except Exception:
                data = {}

            if pf == "propose":
                cost = float(data.get("cost", 9999))
                proposals.append((str(rep.sender), cost))
            elif pf == "refuse":
                await self.log(
                    f"[TRANSFER/ROBOT] Robot {rep.sender} recusou "
                    f"(motivo={data.get('reason')})."
                )

        if not proposals:
            await self.log(
                f"[TRANSFER/ROBOT] Nenhum robot aceitou transportar job {job['id']}."
            )
            return False

        # escolher robot com menor custo
        proposals.sort(key=lambda x: x[1])
        winner_jid, winner_cost = proposals[0]

        await self.log(
            f"[TRANSFER/ROBOT] Robot escolhido p/ job {job['id']}: "
            f"{winner_jid} (cost={winner_cost})."
        )

        # REJECT aos restantes
        for jid, _ in proposals[1:]:
            rej = Message(to=jid)
            rej.set_metadata("protocol", "transport-cnp")
            rej.set_metadata("performative", "reject-proposal")
            rej.set_metadata("thread", thread_id)
            rej.body = json.dumps({"reason": "not_selected"})
            await beh.send(rej)

        # ACCEPT ao vencedor
        acc = Message(to=winner_jid)
        acc.set_metadata("protocol", "transport-cnp")
        acc.set_metadata("performative", "accept-proposal")
        acc.set_metadata("thread", thread_id)
        acc.body = json.dumps(task)
        await beh.send(acc)

        # Esperar INFORM de conclusão
        deadline_inf = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline_inf:
            rep = await beh.receive(timeout=0.5)
            if not rep:
                continue
            if rep.metadata.get("protocol") != "transport-cnp":
                continue
            if rep.metadata.get("thread") != thread_id:
                continue
            if rep.metadata.get("performative") != "inform":
                continue

            try:
                data = json.loads(rep.body) if rep.body else {}
            except Exception:
                data = {}

            if data.get("status") == "delivered" and data.get("kind") == "job":
                await self.log(
                    f"[TRANSFER/ROBOT] Transporte do job {job['id']} concluído "
                    f"por {winner_jid}. Job será entregue à máquina destino pelo robot."
                )
                if self.env:
                    self.env.metrics["jobs_transferred"] += 1
                return True

        await self.log(
            f"[TRANSFER/ROBOT] Timeout à espera de INFORM do robot p/ job {job['id']}."
        )
        return False


    # ==========================================================
    # PASSO 1 – CNP com Supervisor (job-dispatch)
    # ==========================================================
    class JobDispatchParticipant(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            if msg.metadata.get("protocol") != "job-dispatch":
                return

            pf = msg.metadata.get("performative")
            thread_id = msg.metadata.get("thread")

            try:
                job_spec = json.loads(msg.body) if msg.body else {}
            except Exception:
                job_spec = {}

            # CFP → decidir se propõe ou recusa
            if pf == "cfp":
                # se falhada, recusa logo
                if agent.is_failed:
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "job-dispatch")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": "failed"})
                    await self.send(rep)
                    await agent.log(
                        f"[JOB-DISPATCH] REFUSE para {msg.sender} (falhada)."
                    )
                    return

                # fila cheia → recusa
                if len(agent.job_queue) >= agent.max_queue:
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "job-dispatch")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": "queue_full"})
                    await self.send(rep)

                    await agent.log(
                        f"[JOB-DISPATCH] REFUSE para {msg.sender} "
                        f"(fila cheia: {len(agent.job_queue)})"
                    )
                    return

                # custo = tamanho da fila (menos = melhor)
                cost = len(agent.job_queue)

                rep = Message(to=str(msg.sender))
                rep.set_metadata("protocol", "job-dispatch")
                rep.set_metadata("performative", "propose")
                rep.set_metadata("thread", thread_id)
                rep.body = json.dumps({"status": "available", "cost": cost})
                await self.send(rep)

                await agent.log(
                    f"[JOB-DISPATCH] PROPOSE para {msg.sender} com cost={cost}"
                )
                return

            # ACCEPT-PROPOSAL → enfileirar o job (já com pipeline)
            if pf == "accept-proposal":
                job_id = job_spec.get("job_id")

                job = {
                    "id": job_id,
                    "pipeline": agent.pipeline_template.copy(),
                    "current_step_idx": 0,   # começa em step0
                    "materials_ok": False,   # tratado em step0
                }
                agent.job_queue.append(job)

                await agent.log(
                    f"[JOB-DISPATCH] ACCEPT de {msg.sender}. "
                    f"Job {job_id} enfileirado (fila={len(agent.job_queue)})."
                )

                rep = Message(to=str(msg.sender))
                rep.set_metadata("protocol", "job-dispatch")
                rep.set_metadata("performative", "inform")
                rep.set_metadata("thread", thread_id)
                rep.body = json.dumps({"status": "accepted", "job_id": job_id})
                await self.send(rep)
                return

            # REJECT-PROPOSAL → só logar
            if pf == "reject-proposal":
                await agent.log(
                    f"[JOB-DISPATCH] REJECT recebido de {msg.sender}."
                )
                return

    # ==========================================================
    # PASSO 2 – CNP com Suppliers (supply-cnp) → materiais
    # ==========================================================
    async def request_materials_via_cnp(self, beh, job):
        """Lança um CNP 'supply-cnp' aos suppliers para obter materiais."""
        if not self.suppliers:
            await self.log("[SUPPLY] Nenhum supplier configurado. Job não avança.")
            return False

        materials_needed = self.material_requirements
        machine_pos = list(self.position)

        payload = {
            "materials": materials_needed,
            "machine_pos": machine_pos,
        }

        thread_id = f"supply-{self.agent_name}-{random.randint(0, 9999)}"

        # Enviar CFP para todos os suppliers
        for sup_jid in self.suppliers:
            m = Message(to=sup_jid)
            m.set_metadata("protocol", "supply-cnp")
            m.set_metadata("performative", "cfp")
            m.set_metadata("thread", thread_id)
            m.body = json.dumps(payload)
            await beh.send(m)

        await self.log(
            f"[SUPPLY] CFP enviado aos suppliers {self.suppliers} "
            f"para materiais={materials_needed} (thread={thread_id})"
        )

        # Recolher PROPOSE
        proposals = []
        deadline = asyncio.get_event_loop().time() + 3  # 3s

        while asyncio.get_event_loop().time() < deadline:
            rep = await beh.receive(timeout=0.5)
            if not rep:
                continue
            if rep.metadata.get("protocol") != "supply-cnp":
                continue
            if rep.metadata.get("thread") != thread_id:
                continue

            pf = rep.metadata.get("performative")
            if pf == "propose":
                try:
                    data = json.loads(rep.body)
                    cost = float(data.get("cost", 9999))
                    proposals.append((str(rep.sender), cost, data))
                except Exception:
                    continue
            elif pf == "refuse":
                await self.log(f"[SUPPLY] Supplier {rep.sender} recusou (sem stock).")

        if not proposals:
            await self.log("[SUPPLY] Nenhuma proposta recebida. Falha de supply.")
            if self.env:
                self.env.metrics["supply_requests_failed"] += 1
            return False

        # Escolher supplier com menor custo
        proposals.sort(key=lambda x: x[1])
        winner_jid, winner_cost, winner_data = proposals[0]

        await self.log(
            f"[SUPPLY] Supplier vencedor={winner_jid} com cost={winner_cost:.2f}. "
            "Enviando ACCEPT-PROPOSAL."
        )

        # REJECT aos restantes
        for sup_jid, _, _ in proposals[1:]:
            rej = Message(to=sup_jid)
            rej.set_metadata("protocol", "supply-cnp")
            rej.set_metadata("performative", "reject-proposal")
            rej.set_metadata("thread", thread_id)
            rej.body = json.dumps({"reason": "not_selected"})
            await beh.send(rej)

        # ACCEPT ao vencedor
        acc = Message(to=winner_jid)
        acc.set_metadata("protocol", "supply-cnp")
        acc.set_metadata("performative", "accept-proposal")
        acc.set_metadata("thread", thread_id)
        acc.body = json.dumps({
            "materials": materials_needed,
            "machine_pos": machine_pos,
        })
        await beh.send(acc)

        # Esperar INFORM final
        deadline_inf = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline_inf:
            rep = await beh.receive(timeout=0.5)
            if not rep:
                continue
            if rep.metadata.get("protocol") != "supply-cnp":
                continue
            if rep.metadata.get("thread") != thread_id:
                continue
            if rep.metadata.get("performative") != "inform":
                continue

            try:
                data = json.loads(rep.body)
            except Exception:
                data = {}

            status = data.get("status")
            if status == "delivered":
                await self.log(
                    f"[SUPPLY] Materiais entregues pelo supplier {winner_jid}: "
                    f"{data.get('materials')}"
                )
                if self.env:
                    self.env.metrics["supply_requests_ok"] += 1
                job["materials_ok"] = True
                return True

        await self.log("[SUPPLY] Timeout à espera de INFORM do supplier.")
        if self.env:
            self.env.metrics["supply_requests_failed"] += 1
        return False

    # ==========================================================
    # EXECUTOR DO JOB (pipeline + falhas)
    # ==========================================================
    class JobExecutor(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            # Se está falhada, contar downtime e não fazer nada
            if agent.is_failed:
                if agent.env:
                    agent.env.metrics["downtime_ticks"] += 1
                await asyncio.sleep(1)
                return

            # Se não há job atual, tenta ir buscar da fila
            if agent.current_job is None and agent.job_queue:
                agent.current_job = agent.job_queue.pop(0)
                job = agent.current_job

                job.setdefault("pipeline", agent.pipeline_template.copy())
                if "current_step_idx" not in job:
                    job["current_step_idx"] = 0
                if "materials_ok" not in job:
                    # se o job já vem de um step>0 (transferência), assumimos que já tem materiais
                    job["materials_ok"] = job["current_step_idx"] > 0

                pipeline = job["pipeline"]
                idx = job["current_step_idx"]
                current_step = pipeline[idx]

                # se veio de transferência, pode trazer remaining_ticks
                if "remaining_ticks" in job:
                    agent.current_job_ticks = job.pop("remaining_ticks")
                else:
                    agent.current_job_ticks = agent.step_durations[current_step]

                await agent.log(
                    f"[JOB] Início/retoma do job {job['id']} na etapa {current_step} "
                    f"(ticks_step={agent.current_job_ticks})"
                )

            job = agent.current_job
            if job is None:
                await asyncio.sleep(0.5)
                return

            pipeline = job["pipeline"]
            idx = job["current_step_idx"]
            current_step = pipeline[idx]

            # STEP0: garantir materiais via CNP-Recursos
            if current_step == "step0":
                if not job.get("materials_ok", False):
                    ok = await agent.request_materials_via_cnp(self, job)
                    if not ok:
                        await agent.log(
                            f"[JOB] Job {job['id']} cancelado por falta de materiais."
                        )
                        if agent.env:
                            agent.env.metrics["jobs_lost"] += 1
                        agent.current_job = None
                        agent.current_job_ticks = 0
                        await asyncio.sleep(0.5)
                        return

                    await agent.log(
                        f"[JOB] Job {job['id']} concluiu step0 (materiais consumidos)."
                    )

                    # Passa logo para o step1
                    if idx < len(pipeline) - 1:
                        job["current_step_idx"] += 1
                        next_step = pipeline[job["current_step_idx"]]
                        agent.current_job_ticks = agent.step_durations[next_step]
                        await agent.log(
                            f"[JOB] Job {job['id']} transita para {next_step} "
                            f"(ticks_step={agent.current_job_ticks})"
                        )
                        await asyncio.sleep(0.5)
                        return
                    else:
                        # Caso extremo: só step0
                        await agent.log(
                            f"[JOB] Job {job['id']} concluído (após step0)."
                        )
                        if agent.env:
                            agent.env.metrics["jobs_completed"] += 1
                        agent.current_job = None
                        agent.current_job_ticks = 0
                        await asyncio.sleep(0.5)
                        return

                # se já tinha materials_ok=True, deixa seguir para lógica normal

            # *** A PARTIR DAQUI, current_step NUNCA É step0 ***
            # Falha só pode acontecer em step1/2/3
            pipeline = job["pipeline"]
            idx = job["current_step_idx"]
            current_step = pipeline[idx]

            if current_step != "step0":
                # probabilidade de falhar neste tick
                if random.random() < agent.failure_rate:
                    await agent.handle_failure(job)
                    return

            # STEP1, STEP2, STEP3 → contagem de ticks
            agent.current_job_ticks -= 1
            await agent.log(
                f"[JOB] job_id={job['id']} etapa={current_step}, "
                f"ticks_rest={agent.current_job_ticks}"
            )

            # etapa concluída?
            if agent.current_job_ticks <= 0:
                if idx < len(pipeline) - 1:
                    job["current_step_idx"] += 1
                    next_step = pipeline[job["current_step_idx"]]
                    agent.current_job_ticks = agent.step_durations[next_step]

                    await agent.log(
                        f"[JOB] Job {job['id']} entrou na etapa {next_step} "
                        f"(ticks_step={agent.current_job_ticks})"
                    )
                else:
                    await agent.log(
                        f"[JOB] Job {job['id']} concluído (pipeline completo)."
                    )
                    if agent.env:
                        agent.env.metrics["jobs_completed"] += 1
                    agent.current_job = None
                    agent.current_job_ticks = 0

            await asyncio.sleep(1)

    # ==========================================================
    # CNP de MANUTENÇÃO (Machine → MaintenanceCrew)
    # ==========================================================
    class MaintenanceRequester(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            # só atua se máquina falhada e ainda não pediu manutenção
            if not agent.is_failed or agent.maintenance_requested:
                await asyncio.sleep(0.5)
                return

            agent.maintenance_requested = True

            if not agent.maintenance_crews:
                await agent.log("[MAINT-REQ] Nenhuma maintenance crew configurada.")
                return

            thread_id = f"maint-{agent.agent_name}-{agent.env.time}"
            payload = {
                "machine_jid": str(agent.jid),
                "machine_pos": list(agent.position),
            }

            # enviar CFP a todas as crews
            for crew_jid in agent.maintenance_crews:
                m = Message(to=crew_jid)
                m.set_metadata("protocol", "maintenance-cnp")
                m.set_metadata("performative", "cfp")
                m.set_metadata("thread", thread_id)
                m.body = json.dumps(payload)
                await self.send(m)

            await agent.log(
                f"[MAINT-REQ] CFP enviado às crews {agent.maintenance_crews} "
                f"(thread={thread_id})"
            )

            # recolher propostas
            proposals = []
            deadline = asyncio.get_event_loop().time() + 3

            while asyncio.get_event_loop().time() < deadline:
                rep = await self.receive(timeout=0.5)
                if not rep:
                    continue
                if rep.metadata.get("protocol") != "maintenance-cnp":
                    continue
                if rep.metadata.get("thread") != thread_id:
                    continue

                pf = rep.metadata.get("performative")
                try:
                    data = json.loads(rep.body) if rep.body else {}
                except Exception:
                    data = {}

                if pf == "propose":
                    cost = float(data.get("cost", 9999))
                    repair_time = int(data.get("repair_time", 5))
                    proposals.append((str(rep.sender), cost, repair_time))
                elif pf == "refuse":
                    await agent.log(
                        f"[MAINT-REQ] REFUSE de {rep.sender} "
                        f"(motivo={data.get('reason')})"
                    )

            if not proposals:
                await agent.log("[MAINT-REQ] Nenhuma crew disponível. Tentará depois.")
                # vai voltar a tentar, porque maintenance_requested já está True
                # se quiseres voltar a False para repetir o CFP constantemente, muda aqui.
                return

            # escolhe crew com menor custo
            proposals.sort(key=lambda x: x[1])
            winner_jid, winner_cost, winner_rt = proposals[0]

            await agent.log(
                f"[MAINT-REQ] Crew escolhida: {winner_jid} (cost={winner_cost:.2f}, "
                f"repair_time={winner_rt})."
            )

            # REJECT aos outros
            for crew_jid, _, _ in proposals[1:]:
                rej = Message(to=crew_jid)
                rej.set_metadata("protocol", "maintenance-cnp")
                rej.set_metadata("performative", "reject-proposal")
                rej.set_metadata("thread", thread_id)
                rej.body = json.dumps({"reason": "not_selected"})
                await self.send(rej)

            # ACCEPT ao vencedor
            acc = Message(to=winner_jid)
            acc.set_metadata("protocol", "maintenance-cnp")
            acc.set_metadata("performative", "accept-proposal")
            acc.set_metadata("thread", thread_id)
            acc.body = json.dumps({
                "machine_jid": str(agent.jid),
                "machine_pos": list(agent.position),
                "repair_time": winner_rt,
            })
            await self.send(acc)

            agent.repair_in_progress = True
            if agent.env:
                agent.env.metrics["repairs_started"] += 1

    class MaintenanceResponseHandler(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            if msg.metadata.get("protocol") != "maintenance-cnp":
                return

            pf = msg.metadata.get("performative")

            try:
                data = json.loads(msg.body) if msg.body else {}
            except Exception:
                data = {}

            status = data.get("status")

            if pf == "inform":
                if status == "repair_started":
                    await agent.log("[MAINT] Reparação iniciada.")
                elif status == "repair_completed":
                    agent.is_failed = False
                    agent.repair_in_progress = False
                    agent.maintenance_requested = False

                    await agent.log("[MAINT] Reparação concluída. Máquina operacional.")

       # ==========================================================
    # CNP de TRANSFERÊNCIA DE JOB (Machine → outras Machines via Robots)
    # ==========================================================
    class JobTransferInitiator(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            # Só faz sentido se:
            #  - máquina está falhada
            #  - tem um job atual
            #  - ainda não tentou/fez transferência
            if (
                not agent.is_failed
                or agent.current_job is None
                or agent.job_transfer_done
            ):
                await asyncio.sleep(0.5)
                return

            # Não há ambiente → impossível descobrir outras máquinas
            if not agent.env:
                await asyncio.sleep(0.5)
                return

            # Candidatos = outras máquinas registadas no ambiente
            candidates = [
                a for a in agent.env.agents
                if getattr(a, "is_machine", False) and a.jid != agent.jid
            ]

            if not candidates:
                await agent.log("[TRANSFER] Nenhuma outra máquina disponível.")
                agent.job_transfer_done = True
                await asyncio.sleep(0.5)
                return

            job = agent.current_job

            # Empacotar estado do job
            job_data = {
                "id": job["id"],
                "pipeline": job["pipeline"],
                "current_step_idx": job["current_step_idx"],
                "materials_ok": job.get("materials_ok", False),
                "remaining_ticks": agent.current_job_ticks,
            }

            payload = {
                "job": job_data,
                "origin_jid": str(agent.jid),
            }

            thread_id = f"transfer-{job['id']}-{agent.env.time}"

            # Enviar CFP para todas as outras máquinas
            for m in candidates:
                msg = Message(to=str(m.jid))
                msg.set_metadata("protocol", "transfer-cnp")
                msg.set_metadata("performative", "cfp")
                msg.set_metadata("thread", thread_id)
                msg.body = json.dumps(payload)
                await self.send(msg)

            await agent.log(
                f"[TRANSFER] CFP enviado a máquinas "
                f"{[str(m.jid) for m in candidates]} para job {job['id']} "
                f"(thread={thread_id})."
            )

            # Recolher PROPOSE / REFUSE
            proposals = []
            deadline = asyncio.get_event_loop().time() + 3

            while asyncio.get_event_loop().time() < deadline:
                rep = await self.receive(timeout=0.5)
                if not rep:
                    continue
                if rep.metadata.get("protocol") != "transfer-cnp":
                    continue
                if rep.metadata.get("thread") != thread_id:
                    continue

                pf = rep.metadata.get("performative")
                try:
                    data = json.loads(rep.body) if rep.body else {}
                except Exception:
                    data = {}

                if pf == "propose":
                    cost = int(data.get("cost", 999))
                    proposals.append((str(rep.sender), cost))
                elif pf == "refuse":
                    await agent.log(
                        f"[TRANSFER] REFUSE de {rep.sender} "
                        f"(motivo={data.get('reason')})."
                    )

            if not proposals:
                await agent.log("[TRANSFER] Nenhuma máquina aceitou receber o job.")
                agent.job_transfer_done = True
                await asyncio.sleep(0.5)
                return

            # Escolher máquina com menor custo (fila mais pequena, por ex.)
            proposals.sort(key=lambda x: x[1])
            winner_jid, winner_cost = proposals[0]

            await agent.log(
                f"[TRANSFER] Máquina escolhida para receber job {job['id']}: "
                f"{winner_jid} (cost={winner_cost}, candidatos={proposals})"
            )

            # Enviar REJECT aos restantes
            for jid, _ in proposals[1:]:
                rej = Message(to=jid)
                rej.set_metadata("protocol", "transfer-cnp")
                rej.set_metadata("performative", "reject-proposal")
                rej.set_metadata("thread", thread_id)
                rej.body = json.dumps({"reason": "not_selected"})
                await self.send(rej)

            # Enviar ACCEPT ao vencedor (apenas para este “saber” que foi escolhido)
            acc = Message(to=winner_jid)
            acc.set_metadata("protocol", "transfer-cnp")
            acc.set_metadata("performative", "accept-proposal")
            acc.set_metadata("thread", thread_id)
            acc.body = json.dumps({"job_id": job["id"]})
            await self.send(acc)

            # Agora entra a parte nova: transporte do job via robots
            ok = await agent.transfer_job_via_robots(job_data, winner_jid, self)

            if ok:
                # Job foi fisicamente transferido; esta máquina liberta o job
                agent.current_job = None
                agent.current_job_ticks = 0
                agent.job_transfer_done = True
                await agent.log(
                    f"[TRANSFER] Job {job['id']} transferido com sucesso para {winner_jid} via robots."
                )
            else:
                await agent.log(
                    f"[TRANSFER] Falha na transferência via robots para {winner_jid}."
                )
                agent.job_transfer_done = True  # evita loops infinitos

    class JobTransferParticipant(CyclicBehaviour):
        async def run(self):
            agent = self.agent

            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            if msg.metadata.get("protocol") != "transfer-cnp":
                return

            pf = msg.metadata.get("performative")
            thread_id = msg.metadata.get("thread")

            try:
                data = json.loads(msg.body) if msg.body else {}
            except Exception:
                data = {}

            # CFP → decidir se pode receber job
            if pf == "cfp":
                # Se falhada ou fila cheia → REFUSE
                if agent.is_failed:
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "transfer-cnp")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": "failed"})
                    await self.send(rep)
                    return

                if len(agent.job_queue) >= agent.max_queue:
                    rep = Message(to=str(msg.sender))
                    rep.set_metadata("protocol", "transfer-cnp")
                    rep.set_metadata("performative", "refuse")
                    rep.set_metadata("thread", thread_id)
                    rep.body = json.dumps({"reason": "queue_full"})
                    await self.send(rep)
                    return

                # custo simples = tamanho da fila (quanto menor melhor)
                cost = len(agent.job_queue)

                rep = Message(to=str(msg.sender))
                rep.set_metadata("protocol", "transfer-cnp")
                rep.set_metadata("performative", "propose")
                rep.set_metadata("thread", thread_id)
                rep.body = json.dumps({"cost": cost})
                await self.send(rep)

                await agent.log(
                    f"[TRANSFER] PROPOSE enviado para {msg.sender} (cost={cost})."
                )
                return

            # ACCEPT-PROPOSAL → opcionalmente só logar (job chegará via robot)
            if pf == "accept-proposal":
                job_id = data.get("job_id")
                await agent.log(
                    f"[TRANSFER] ACCEPT-PROPOSAL recebido para job {job_id}. "
                    "Aguardando entrega via robot..."
                )
                return

            # REJECT-PROPOSAL → só log
            if pf == "reject-proposal":
                await agent.log("[TRANSFER] REJECT recebido (não fui escolhido).")
                return

            

    class JobTransferReceiver(CyclicBehaviour):
        """
        Recebe jobs entregues pelos robots (protocol 'job-transfer')
        e coloca-os na fila da máquina destino.
        """
        async def run(self):
            agent = self.agent
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            if msg.metadata.get("protocol") != "job-transfer":
                return

            try:
                data = json.loads(msg.body) if msg.body else {}
            except Exception:
                data = {}

            if data.get("status") != "job_delivered":
                return

            job = data.get("job")
            if not isinstance(job, dict):
                await agent.log("[JOB-TRANSFER] ERRO: job inválido recebido.")
                return

            agent.job_queue.append(job)
            await agent.log(
                f"[JOB-TRANSFER] Job {job.get('id')} entregue por robot e "
                f"adicionado à fila (tam={len(agent.job_queue)})."
            )

            

