# environment.py
# -*- coding: utf-8 -*-

import asyncio
import json


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

    def get_all_agents_status(self):
        """Coleta o estado atual de todos os agentes para serialização em JSON."""
        data = {
            "metrics": self.metrics,
            "time": self.time,
            "agents": {
                "machines": [],
                "robots": [],
                "suppliers": [],
                "maintenance_crews": [],
                "supervisor": {}
            }
        }

        for agent in self.agents:
            # Usa getattr para aceder a atributos que podem não existir, dependendo da ordem de import
            agent_type = agent.__class__.__name__
            
            # --- MachineAgent ---
            if agent_type == "MachineAgent":
                current_job = getattr(agent, 'current_job', None)
                
                if current_job:
                    job_id = current_job.get('id')
                    job_step_idx = current_job.get('current_step_idx', 0)
                    pipeline = getattr(agent, 'pipeline_template', [])
                    # Acede ao nome do step com verificação de limites
                    current_step_name = pipeline[job_step_idx] if 0 <= job_step_idx < len(pipeline) else "UNKNOWN_STEP"
                    ticks_remaining = getattr(agent, 'current_job_ticks', 0)
                else:
                    job_id = None
                    current_step_name = None
                    ticks_remaining = 0
                
                state = {
                    "name": getattr(agent, 'agent_name', None),
                    "jid": str(agent.jid),
                    "position": getattr(agent, 'position', (0,0)),
                    "is_failed": getattr(agent, 'is_failed', False),
                    "queue_size": len(getattr(agent, 'job_queue', [])),
                    "max_queue": getattr(agent, 'max_queue', 15),
                    "current_job_id": job_id,
                    "current_step": current_step_name,
                    "ticks_remaining": ticks_remaining
                }
                data["agents"]["machines"].append(state)
            
            # --- RobotAgent ---
            elif agent_type == "RobotAgent":
                state = {
                    "name": getattr(agent, 'agent_name', None),
                    "jid": str(agent.jid),
                    "position": getattr(agent, 'position', (0,0)),
                    "energy": getattr(agent, 'energy', 0),
                    "max_energy": getattr(agent, 'max_energy', 100),
                    "is_busy": getattr(agent, 'busy', False)
                }
                data["agents"]["robots"].append(state)
                
            # --- SupplierAgent ---
            elif agent_type == "SupplierAgent":
                state = {
                    "name": getattr(agent, 'agent_name', None),
                    "jid": str(agent.jid),
                    "position": getattr(agent, 'position', (0,0)),
                    "stock": getattr(agent, 'stock', {}),
                    "recharging": getattr(agent, 'recharging', False),
                }
                data["agents"]["suppliers"].append(state)
            
            # --- MaintenanceAgent ---
            elif agent_type == "MaintenanceAgent":
                current_repair = getattr(agent, 'current_repair', None)
                
                state = {
                    "name": getattr(agent, 'agent_name', None),
                    "jid": str(agent.jid),
                    "position": getattr(agent, 'position', (0,0)),
                    "status": getattr(agent, 'crew_status', 'available'),
                    # Acesso seguro ao target e ticks
                    "repair_target": current_repair.get('machine_jid') if current_repair else None,
                    "ticks_remaining": getattr(agent, 'repair_ticks_remaining', 0) if current_repair else 0
                }
                data["agents"]["maintenance_crews"].append(state)
            
            # --- SupervisorAgent ---
            elif agent_type == "SupervisorAgent":
                data["agents"]["supervisor"] = {
                    "jid": str(agent.jid),
                    "last_job_id": getattr(agent, 'job_counter', 0),
                }

        return data

    async def tick(self):
        """Avança o tempo global 1 unidade."""
        self.time += 1
        await asyncio.sleep(0.1)