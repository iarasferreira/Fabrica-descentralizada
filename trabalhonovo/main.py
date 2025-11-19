# main.py
import asyncio
from environment import FactoryEnvironment
from agents.supply_cnp_agent import SupplyCNPAgent
from agents.machine_cnp_agent import MachineCNPAgent
from agents.supervisor_agent import SupervisorAgent
from agents.maintenance_agent import MaintenanceAgent
from agents.robot_agent import RobotAgent
from agents.order_agent import OrderAgent

DOMAIN = "localhost"
PWD = "12345"

async def main():
    print("\nMulti-Machine Coordination iniciada.\n")

    # === Environment ===
    env = FactoryEnvironment()

    env.robots = []

    robot1 = RobotAgent(f"robot1@{DOMAIN}", PWD, env=env, name="R1", location=(5,5))
    robot2 = RobotAgent(f"robot2@{DOMAIN}", PWD, env=env, name="R2", location=(6,5))

    env.robots.append(robot1.jid)
    env.robots.append(robot2.jid)

    await robot1.start(auto_register=True)
    await robot2.start(auto_register=True)

    # === Suppliers ===
    supplierA = SupplyCNPAgent(
        f"supplierA@{DOMAIN}", PWD, env=env,
        name="A",
        stock_init={"flour": 60, "sugar": 40, "butter": 30},
        capacity={"flour": 50, "sugar": 30, "butter": 20},
        location=(0,10)
    )
    supplierB = SupplyCNPAgent(
        f"supplierB@{DOMAIN}", PWD, env=env,
        name="B",
        stock_init={"flour": 45, "sugar": 50, "butter": 25},
        capacity={"flour": 50, "sugar": 30, "butter": 20},
        location=(10,10)
    )
    await supplierA.start(auto_register=True)
    await supplierB.start(auto_register=True)

    # === Maintenance Crews (Contract Net Pattern) ===
    # Múltiplas maintenance crews que licitar para reparações
    maintenance_crew1 = MaintenanceAgent(
        f"maintenance1@{DOMAIN}", PWD, env=env,
        name="Crew-A",
        crew_status="available",
        repair_time_range=(3, 8),
        location=(0, 0)
    )
    maintenance_crew2 = MaintenanceAgent(
        f"maintenance2@{DOMAIN}", PWD, env=env,
        name="Crew-B",
        crew_status="available",
        repair_time_range=(4, 7),
        location=(10, 0),
    )
    
    await maintenance_crew1.start(auto_register=True)
    await maintenance_crew2.start(auto_register=True)
    
    # Registar maintenance crews no environment
    env.maintenance_crews = [maintenance_crew1, maintenance_crew2]

    # === Machines ===
    suppliers = [f"supplierA@{DOMAIN}", f"supplierB@{DOMAIN}"]

    machine1 = MachineCNPAgent(
        f"machine1@{DOMAIN}", PWD, env=env,
        suppliers=suppliers,
        batch={"flour": 10, "sugar": 5, "butter": 3},
        name="M1",
        failure_rate=0.05,
        capabilities=["cutting", "mixing", "baking"],
        location=(3,3)
    )
    machine2 = MachineCNPAgent(
        f"machine2@{DOMAIN}", PWD, env=env,
        suppliers=suppliers,
        batch={"flour": 8, "sugar": 4, "butter": 2},
        name="M2",
        failure_rate=0.04,
        capabilities=["mixing", "baking", "packaging"],
        location=(7,3)
    )

    await machine1.start(auto_register=True)
    await machine2.start(auto_register=True)

    # Lista de máquinas para o scheduler
    machines = [machine1, machine2]

    # === Order Agent (Novo Scheduler Descentralizado) === <--- ADICIONADO
    order_agent = OrderAgent(
        f"order@{DOMAIN}", PWD, env=env,
        job_dispatch_every=5,  # dispara novo job a cada 5 ticks
        machines=machines,     # passa referência às máquinas (para CNP Aberto)
        location=(5, 10)       # Localização do Order Agent
    )
    await order_agent.start(auto_register=True)

    # === Supervisor ===
    supervisor = SupervisorAgent(
        f"supervisor@{DOMAIN}", PWD, env=env,
        supply_refill_every=10,
        refill_amount={"flour": 30, "sugar": 20, "butter": 10},
        supply_agent_ref=supplierA,
        location=(5,0)
    )
    await supervisor.start(auto_register=True)


    # === Simulation Loop ===
    MAX_TICKS = 500
    idle_ticks = 0

    while env.time < MAX_TICKS:
        # avança o tempo global
        await env.tick()

        # há algum job ainda a ser processado ou em fila?
        active_jobs = any(
            (m.current_job is not None) or (len(m.job_queue) > 0)
            for m in machines
        )

        # critério de "inatividade":
        # - não há CNP pendentes (cnp_cfp == cnp_accepts)
        # - E não há jobs em processamento
        if env.metrics["cnp_cfp"] == env.metrics["cnp_accepts"] and not active_jobs:
            idle_ticks += 1
        else:
            idle_ticks = 0

        if idle_ticks >= 10:
            print("Nenhum novo contrato nem jobs ativos nos últimos 10 ticks. Terminando simulação.")
            break

        await asyncio.sleep(0.1)

    print("Execução terminada (Multi-Machine CNP + Pipeline + Manutenção).")

    # === Mostrar métricas finais (útil para relatório) ===
    print("\n=== MÉTRICAS FINAIS ===")
    for k, v in env.metrics.items():
        print(f"{k}: {v}")

    # === Stop Agents ===
    await robot1.stop()
    await robot2.stop()
    await machine1.stop()
    await machine2.stop()
    await supplierA.stop()
    await supplierB.stop()
    await maintenance_crew1.stop()
    await maintenance_crew2.stop()
    await supervisor.stop()
    await order_agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
