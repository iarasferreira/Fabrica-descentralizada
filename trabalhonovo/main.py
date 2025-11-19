# main.py
# -*- coding: utf-8 -*-

import asyncio
import spade


from environment import FactoryEnvironment
from agents.machine_agent import MachineAgent
from agents.supplier_agent import SupplierAgent
from agents.supervisor_agent import SupervisorAgent
from agents.robot_agent import RobotAgent
from agents.maintenance_agent import MaintenanceAgent


DOMAIN = "localhost"
PWD = "12345"


async def main():
    env = FactoryEnvironment()

    # === Robots ===
    robot1 = RobotAgent(
        f"robot1@{DOMAIN}",
        PWD,
        env=env,
        name="R1",
        position=(0, 0),
        speed=0.5,
    )
    robot2 = RobotAgent(
        f"robot2@{DOMAIN}",
        PWD,
        env=env,
        name="R2",
        position=(5, 0),
        speed=0.5,
    )

    await robot1.start(auto_register=True)
    await robot2.start(auto_register=True)

    robots_jids = [str(robot1.jid), str(robot2.jid)]

    # === Maintenance Crews ===
    maint1 = MaintenanceAgent(
        f"maint1@{DOMAIN}",
        PWD,
        env=env,
        name="Crew-A",
        position=(0, 5),
        repair_time_range=(3, 7),
    )
    maint2 = MaintenanceAgent(
        f"maint2@{DOMAIN}",
        PWD,
        env=env,
        name="Crew-B",
        position=(10, 5),
        repair_time_range=(4, 8),
    )

    await maint1.start(auto_register=True)
    await maint2.start(auto_register=True)

    maintenance_jids = [str(maint1.jid), str(maint2.jid)]

    # === Suppliers ===
    supplierA = SupplierAgent(
        f"supplierA@{DOMAIN}",
        PWD,
        env=env,
        name="A",
        stock_init={"material_1": 40, "material_2": 40},
        position=(0, 0),
        robots=robots_jids,
    )
    supplierB = SupplierAgent(
        f"supplierB@{DOMAIN}",
        PWD,
        env=env,
        name="B",
        stock_init={"material_1": 40, "material_2": 40},
        position=(10, 0),
        robots=robots_jids,
    )

    await supplierA.start(auto_register=True)
    await supplierB.start(auto_register=True)

    suppliers_jids = [str(supplierA.jid), str(supplierB.jid)]

    # === Machines ===
    machine1 = MachineAgent(
        f"machine1@{DOMAIN}",
        PWD,
        env=env,
        name="M1",
        position=(3, 0),
        suppliers=suppliers_jids,
        material_requirements={"material_1": 10, "material_2": 5},
        failure_rate=0.05,
        maintenance_crews=maintenance_jids,
        robots=robots_jids,
    )
    machine2 = MachineAgent(
        f"machine2@{DOMAIN}",
        PWD,
        env=env,
        name="M2",
        position=(7, 0),
        suppliers=suppliers_jids,
        material_requirements={"material_1": 8, "material_2": 4},
        failure_rate=0.04,
        maintenance_crews=maintenance_jids,
        robots=robots_jids,
    )

    await machine1.start(auto_register=True)
    await machine2.start(auto_register=True)

    machines_jids = [str(machine1.jid), str(machine2.jid)]

    # === Supervisor ===
    supervisor = SupervisorAgent(
        f"supervisor@{DOMAIN}",
        PWD,
        env=env,
        machines=machines_jids,
        job_dispatch_every=5,
    )
    await supervisor.start(auto_register=True)

    # === Loop de simulação ===
    MAX_TICKS = 500
    while env.time < MAX_TICKS:
        await env.tick()
        await asyncio.sleep(0.1)

    print("\n=== MÉTRICAS FINAIS ===")
    for k, v in env.metrics.items():
        print(f"{k}: {v}")

    # parar agentes
    await supervisor.stop()
    await machine1.stop()
    await machine2.stop()
    await supplierA.stop()
    await supplierB.stop()
    await robot1.stop()
    await robot2.stop()
    await maint1.stop()
    await maint2.stop()


if __name__ == "__main__":
    spade.run(main(), embedded_xmpp_server=True)

