# main.py (Versão Final com Simplificação de Cores)
# -*- coding: utf-8 -*-

import asyncio
import spade
import matplotlib.pyplot as plt
import numpy as np
import sys
import matplotlib.font_manager as fm 


# --- CONFIGURAÇÃO DE FONTES ---
plt.rcParams['font.sans-serif'] = ['Noto Color Emoji', 'Segoe UI Symbol', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

from environment import FactoryEnvironment
from agents.machine_agent import MachineAgent
from agents.supplier_agent import SupplierAgent
from agents.supervisor_agent import SupervisorAgent
from agents.robot_agent import RobotAgent
from agents.maintenance_agent import MaintenanceAgent


DOMAIN = "localhost"
PWD = "12345"

# --- Mapeamento de Emojis para Visualização ---
EMOJI_MAP = {
    'machine': '🏭',
    'robot': '🤖',
    'supplier': '📦',
    'maint': '🔧',
}

# --- PARÂMETROS GLOBAIS DE LAYOUT ---
COLUMNS = {
    'supplier': 1.0, 
    'robot': 3.0,     
    'machine': 5.0,   
    'maint': 7.0      
}

def get_coords(count, y_pos, x_min, x_max):
    """Gera N posições (x, y) uniformemente distribuídas entre x_min e x_max."""
    if count == 0: return []
    x_values = np.linspace(x_min, x_max, count + 2)[1:-1] 
    return [(round(x, 2), y_pos) for x in x_values]


def generate_agents_config(counts, domain, pwd, env):
    """
    Gera agentes baseados em contagens específicas e layout em colunas.
    """
    
    N_MACHINES = counts['machines']
    N_ROBOTS = counts['robots']
    N_MAINT = counts['maint']
    N_SUPPLIERS = counts['suppliers'] 
    
    robots = []
    robots_jids = []
    machines = []
    machines_jids = []
    maintenance_crews = []
    maintenance_jids = []
    suppliers = []
    suppliers_jids = []
    
    Y_START = 1.0 
    Y_STEP = 1.5
    
    # === 1. Inicializar Robôs e Crews ===
    robots_x = COLUMNS['robot']
    for i in range(N_ROBOTS):
        pos = (robots_x, Y_START + i * Y_STEP)
        name = f"R{i+1}"
        r = RobotAgent(f"robot{i+1}@{domain}", pwd, env=env, name=name, position=pos, speed=0.5)
        robots.append(r)
        robots_jids.append(str(r.jid))

    maint_x = COLUMNS['maint']
    for i in range(N_MAINT):
        pos = (maint_x, Y_START + i * Y_STEP)
        name = f"Crew-{i+1}"
        m = MaintenanceAgent(f"maint{i+1}@{domain}", pwd, env=env, name=name, position=pos, repair_time_range=(3, 7))
        maintenance_crews.append(m)
        maintenance_jids.append(str(m.jid))

    # === 2. Inicializar Suppliers (X fixo) ===
    suppliers_x = COLUMNS['supplier']
    if N_SUPPLIERS > 0:
        for i in range(N_SUPPLIERS):
            pos = (suppliers_x, Y_START + i * Y_STEP) 
            name = f"S{i+1}"
            
            s = SupplierAgent(f"supplier{i+1}@{domain}", pwd, env=env, name=name, 
                stock_init={"material_1": 40, "material_2": 40}, 
                position=pos, robots=robots_jids
            )
            suppliers.append(s)
            suppliers_jids.append(str(s.jid))

    # === 3. Inicializar Máquinas (X fixo) ===
    machines_x = COLUMNS['machine']
    avg_reqs = {"material_1": 9, "material_2": 4.5} 

    for i in range(N_MACHINES):
        pos = (machines_x, Y_START + i * Y_STEP)
        name = f"M{i+1}"
        m = MachineAgent(f"machine{i+1}@{domain}", pwd, env=env, name=name, position=pos, 
            suppliers=suppliers_jids, material_requirements=avg_reqs, failure_rate=0.05, 
            maintenance_crews=maintenance_jids, robots=robots_jids
        )
        machines.append(m)
        machines_jids.append(str(m.jid))
        
    all_agents = machines + robots + suppliers + maintenance_crews
    
    return all_agents, machines_jids


# --- FUNÇÃO DE DESENHO MATPLOTLIB ---
def draw_factory_state(env_data, ax_map, ax_metrics, current_counts):
    """Desenha e atualiza o mapa da fábrica e as métricas principais e legendas."""
    
    ax_map.clear()
    
    # Ajustar limites Y dinamicamente para o empilhamento vertical
    Y_MAX_DYNAMIC = max(COLUMNS.values()) + max(current_counts.values()) * 1.5 + 2.0
    
    ax_map.set_xlim(0, 10) 
    ax_map.set_ylim(-1, Y_MAX_DYNAMIC) 
    ax_map.set_title(f'Layout da Fábrica (Tick: {env_data["time"]})', fontsize=12)
    ax_map.set_xlabel('Coordenada X')
    ax_map.set_ylabel('Coordenada Y')
    ax_map.grid(True)
    ax_map.invert_yaxis() 
    
    agents = env_data['agents']
    
    for agent_type, agent_list in agents.items():
        if agent_type == 'supervisor': continue
            
        for a in agent_list:
            P_real = np.array(a['position'], dtype=float)
            P_draw = P_real.copy() 
            status_tag = 'IDLE'
            
            # --- Lógica de Status Simples ---
            if agent_type == 'machines':
                status_tag = 'FAIL' if a['is_failed'] else ('BUSY' if a['current_job_id'] else 'IDLE')
                
            elif agent_type == 'robots':
                # Simplificação: Ocupado (transportando) ou Livre (inclui recarga)
                status_tag = 'BUSY' if a['is_busy'] else 'IDLE' 
                
            elif agent_type == 'maintenance_crews':
                # Simplificação: Ocupado (a reparar) ou Livre
                status_tag = a['status'].upper() 
                
            elif agent_type == 'suppliers':
                 # Simplificação: RECHARGING (sem stock) é um problema
                 status_tag = 'RECHARGING_S' if a['recharging'] else 'IDLE' 


            # --- Lógica de Movimento e Offset (Visual) ---
            offset_dx, offset_dy = 0, 0
            
            if agent_type == 'machines':
                offset_dx, offset_dy = -0.3, 0 # Offset para a etiqueta
            elif agent_type == 'robots':
                offset_dx, offset_dy = 0.3, 0 # Offset para a etiqueta
            elif agent_type == 'maintenance_crews':
                if status_tag == 'BUSY' and a['repair_target']:
                    # Simulação de Viagem: Posição de desenho é a Máquina Alvo
                    target_jid = a['repair_target']
                    target_machine = next((m for m in agents['machines'] if m['jid'] == target_jid), None)
                    if target_machine:
                        P_draw = np.array(target_machine['position'], dtype=float) 
                        offset_dx, offset_dy = 0.5, 0.0 # Offset para ficar ao lado da Máquina
                else:
                    offset_dx, offset_dy = -0.5, 0 
            elif agent_type == 'suppliers':
                 offset_dx, offset_dy = 0, 0.5 

            
            P_draw += [offset_dx, offset_dy]

            emoji = EMOJI_MAP.get(agent_type[:-1], '?')
            label = a['name']
            
            # Lógica de Cor Centralizada
            if status_tag in ('FAIL', 'RECHARGING_S'):
                box_color = 'red' # Falha ou Recarga do Supplier (Problema)
            elif status_tag == 'BUSY':
                box_color = 'yellow' # Ocupado (Máquina, Robô, Crew)
            else:
                box_color = 'lightgray' # Livre/IDLE

            ax_map.annotate(
                emoji, 
                xy=P_draw, 
                xytext=(P_draw[0], P_draw[1]), 
                textcoords="data", 
                ha='center', va='center',
                fontsize=20,
                bbox=dict(boxstyle="square,pad=0.2", fc=box_color, alpha=0.9, ec="black" if status_tag == 'FAIL' else "none")
            )
            
            ax_map.annotate(
                label, 
                xy=P_draw, 
                xytext=(0, 25), 
                textcoords="offset points", 
                ha='center', fontsize=8, color='black'
            )


    # === Configurações das Métricas (Tabela principal) ===
    ax_metrics.clear()
    ax_metrics.set_title(f'Métricas Principais (Tempo Total: {env_data["time"]} ticks)', fontsize=12)
    
    metrics = env_data['metrics']
    
    key_metrics = {
        'Jobs Completados': metrics['jobs_completed'],
        'Jobs Criados': metrics['jobs_created'],
        'Falhas Máquina': metrics['machine_failures'],
        'Downtime (Ticks)': metrics['downtime_ticks'],
        'Abastecimento OK': metrics['supply_requests_ok'],
        'Jobs Transferidos': metrics['jobs_transferred'],
    }
    
    col_labels = ['Métrica', 'Valor']
    table_data = [[label, value] for label, value in key_metrics.items()]
    
    table_metrics = ax_metrics.table(cellText=table_data, colLabels=col_labels, 
                             loc='upper center', cellLoc='left', bbox=[0.1, 0.65, 0.8, 0.3])
    table_metrics.auto_set_font_size(False)
    table_metrics.set_fontsize(10)


    # === Tabela de Legendas ===
    
    legend_data = [
        [EMOJI_MAP['machine'], 'Máquina de Produção'],
        [EMOJI_MAP['robot'], 'Robot de Transporte'],
        [EMOJI_MAP['supplier'], 'Supplier de Materiais'],
        ['?', 'Maintenance Crew'],
        ['Cor Amarela', 'Agente Ocupado (Trabalhando)'],
        ['Cor Cinzenta', 'Agente Livre/Disponível'],
        ['Cor Vermelha', 'Agente em Falha/Sem Stock (Problema)'],
    ]

    table_legend = ax_metrics.table(cellText=legend_data, colLabels=['Símbolo', 'Significado'], 
                             loc='lower center', cellLoc='left', bbox=[0.1, 0.15, 0.8, 0.4])
    table_legend.auto_set_font_size(False)
    table_legend.set_fontsize(10)
    
    ax_metrics.axis('off') 
    
    
    plt.draw()
    plt.show() 


async def main():
    
    env = FactoryEnvironment()
    
    # 1. ENTRADA DO UTILIZADOR PARA O NÚMERO DE AGENTES
    print("--- Configuração Inicial da Fábrica ---")
    try:
        counts = {
            'machines': max(1, int(input("Insira o número de Máquinas (Mín. 1): "))),
            'robots': max(1, int(input("Insira o número de Robôs (Mín. 1): "))),
            'maint': max(1, int(input("Insira o número de Crews de Manutenção (Mín. 1): "))),
            'suppliers': max(1, int(input("Insira o número de Suppliers (Mín. 1): "))),
        }
    except ValueError:
        print("\nInput inválido. Usando a configuração padrão (M=2, R=2, C=2, S=2).")
        counts = {'machines': 2, 'robots': 2, 'maint': 2, 'suppliers': 2}
    
    # 2. GERAÇÃO DINÂMICA DE AGENTES
    all_agents, machines_jids = generate_agents_config(counts, DOMAIN, PWD, env)
    
    # 3. START AGENTS
    for agent in all_agents:
        await agent.start(auto_register=True)

    # 4. START SUPERVISOR
    supervisor = SupervisorAgent(f"supervisor@{DOMAIN}", PWD, env=env, machines=machines_jids, job_dispatch_every=5)
    await supervisor.start(auto_register=True)
    
    
    # === INICIALIZAÇÃO MATPLOTLIB ===
    plt.ion() 
    fig, (ax_map, ax_metrics) = plt.subplots(1, 2, figsize=(14, 7))
    fig.canvas.manager.set_window_title(f'Visualização da Fábrica (M:{counts["machines"]}, R:{counts["robots"]}, S:{counts["suppliers"]})')
    
    # === Loop de simulação ===
    MAX_TICKS = 500
    try:
        while env.time < MAX_TICKS:
            current_state = env.get_all_agents_status()
            
            draw_factory_state(current_state, ax_map, ax_metrics, counts)
            plt.pause(0.01) # Pausa mínima para atualizar a GUI
            
            await env.tick()
            await asyncio.sleep(0.1) # Pausa para simular o tempo entre ticks
            
    except Exception as e:
        print(f"\nErro durante a simulação: {e}")
    finally:
        plt.ioff() 
        plt.close(fig) 
        
        # STOP AGENTS
        print("\n=== SIMULAÇÃO CONCLUÍDA ===")
        for agent in all_agents:
            if getattr(agent, 'is_started', True):
                await agent.stop()
        if getattr(supervisor, 'is_started', True):
            await supervisor.stop()

        print("\n=== MÉTRICAS FINAIS ===")
        for k, v in env.metrics.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    spade.run(main())