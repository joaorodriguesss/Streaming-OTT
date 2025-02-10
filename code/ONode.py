import socket
import time
import subprocess
import os
import cv2
import numpy as np
import threading
import select

def load_nodes(filename):
    nodes = []
    with open(filename, 'r') as file:
        for line in file:
            if line.strip() and not line.startswith('#'):
                parts = line.split()
                ip = parts[0]
                role = parts[1]
                name = parts[2]
                nodes.append((ip, role, name))
    return nodes

def get_ip_by_name(filename, node_name):
    """Busca o IP correspondente ao nome do nó a partir do arquivo nodes.txt."""
    with open(filename, 'r') as file:
        for line in file:
            if line.strip() and not line.startswith('#'):
                parts = line.split()
                ip = parts[0]
                name = parts[2]
                if name == node_name:
                    return ip
    raise ValueError(f"IP not found for node name: {node_name}")

def ping_node(ip):
    try:
        result = subprocess.run(['ping', '-c', '1', ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in result.stdout.splitlines():
            if "time=" in line:
                time_ms = float(line.split("time=")[1].split(" ")[0])
                return time_ms
    except Exception as e:
        print(f"Failed to ping {ip}: {e}")
    return float('inf')  # Retorna um valor alto para pings falhados

def select_overlay_connections(nodes, role, max_connections, own_name):
    ping_times = []
    for ip, node_role, node_name in nodes:
        if node_name != own_name and node_role in ['PoP', 'Tree', 'ContentServer']:
            latency = ping_node(ip)
            ping_times.append((latency, ip, node_name))
    
    # Ordena por menor latência e seleciona o número de conexões permitido
    ping_times.sort()
    selected_connections = [(ip, name) for _, ip, name in ping_times[:max_connections]]
    return selected_connections

def load_overlay_connections(filename, own_name):
    """Carrega as conexões de overlay para o nó atual com base no arquivo nodes.txt."""
    connections = []
    with open(filename, 'r') as file:
        in_overlay_section = False
        for line in file:
            line = line.strip()
            if line.startswith('# Overlay Connections'):
                in_overlay_section = True
                continue
            if in_overlay_section and line.startswith('#'):
                break  # Fim da seção de overlay connections
            
            if in_overlay_section and '->' in line:
                source, targets = line.split('->')
                source = source.strip()
                targets = [t.strip() for t in targets.split()]
                if own_name == source:
                    connections = [(get_ip_by_name(filename, target), target) for target in targets]
                    break
    return connections

def connect_to_tracker(tracker_ip, tracker_port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client_socket:
        # Envia uma solicitação inicial para o tracker
        client_socket.sendto(b"Node request", (tracker_ip, tracker_port))

        # Função para receber mensagens delimitadas
        def receive_message(sock):
            data, _ = sock.recvfrom(1024)
            message = data.decode().strip()
            return message

        # Recebe o papel e o nome do nó do Tracker
        message = receive_message(client_socket)
        role, name = message.split()  
        print(f"Node role assigned by Tracker: {role}, name: {name}")
        return role, name

# Função para encaminhar a mensagem de solicitação de vídeo com o traceroute
def forward_message(client_socket, message, connections, current_ip):
    traceroute = message.split("via ")[-1]

    # Verifica se o nó atual já está no traceroute para evitar loops
    if current_ip in traceroute:
        # print(f"Loop detected: {current_ip} already in traceroute. Message not forwarded.")
        return

    updated_message = f"{message.split(' via ')[0]} via {current_ip} -> {traceroute}"

    for ip, _ in connections:
        client_socket.sendto(updated_message.encode(), (ip, 6000))
        print(f"Forwarded message to {ip}: {updated_message}")

def send_video(client_ip, client_socket, video_path, client_port):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("Error: Unable to open video.")
        return

    print(f"Sending video to {client_ip}...")

    chunk_size = 1024
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            _, encoded_frame = cv2.imencode('.jpg', frame)
            encoded_frame_bytes = encoded_frame.tobytes()
            frame_size = len(encoded_frame_bytes)

            # Dividir e enviar o frame em chunks
            for i in range(0, frame_size, chunk_size):
                packet = encoded_frame_bytes[i:i + chunk_size]
                client_socket.sendto(packet, (client_ip, client_port))

            time.sleep(0.03) 
    except Exception as e:
        print(f"Error during video transmission: {e}")
    finally:
        cap.release()

def handle_video_packet(client_socket, data, addr, routing_table, own_ip, socket_port):
    # Processa pacotes de vídeo e encaminha usando o routing_table
    for (client_ip, video_name) in routing_table:
        next_hop = routing_table[(client_ip, video_name)]
        if next_hop != own_ip:
            client_socket.sendto(data, (next_hop, socket_port))
            print(f"Forwarded video packet for {video_name} to {next_hop} to port {socket_port}")

def start_video_stream(video_path, client_ip, client_port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as video_socket:
        send_video(client_ip, video_socket, video_path, client_port)


def process_node(role, name, client_socket, connections, own_ip):
    if role in ['PoP', 'Tree']:
        # Definindo as portas para os vídeos
        video_ports = {
            "video_1_avc.mp4": 6001,  # Porta para o video_1_avc.mp4
            "video_2_avc.mp4": 6002   # Porta para o video_2_avc.mp4
        }

        # Cria o socket para mensagens de controlo na porta 6000
        control_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        control_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        control_socket.bind(('', 6000))  # Escutando na porta 6000
        print(f"{role} node listening on control port 6000")

        # Cria sockets para os vídeos nas portas específicas
        video_sockets = {}
        for video_name, port in video_ports.items():
            video_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            video_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            video_socket.bind(('', port))  # Escutando na porta do vídeo
            video_sockets[video_name] = video_socket
            print(f"{role} node listening for {video_name} on port {port}")

        # Tabela de roteamento
        routing_table = {}  # Armazena {video_name: next_hop}

        while True:
            # Espera pacotes nos sockets de controle e de vídeo
            ready_sockets, _, _ = select.select([control_socket] + list(video_sockets.values()), [], [])

            for sock in ready_sockets:
                if sock == control_socket:
                    # Processamento das mensagens de controlo
                    data, addr = sock.recvfrom(1024)
                    try:
                        message = data.decode()

                        if "send video" in message:
                            forward_message(control_socket, message, connections, own_ip)

                        elif message.startswith("route"):
                            # Processamento das mensagens de rota
                            _, client_ip, video_name, next_hop = message.split()
                            print(f"Received route update: client_ip={client_ip}, video_name={video_name}, next_hop={next_hop}")

                            # Atualiza a tabela de roteamento
                            if next_hop != own_ip:
                                routing_table[(client_ip, video_name)] = next_hop
                                print(f"Routing table updated: {routing_table}")
                            else:
                                print(f"Ignored invalid route update: {client_ip} {video_name} -> {next_hop}")

                    except UnicodeDecodeError:
                        # Caso não seja uma mensagem legível, deve ser um pacote de vídeo
                        threading.Thread(target=handle_video_packet, args=(sock, data, addr, routing_table, own_ip,  6001 if video_name == "video_1_avc.mp4" else 6002)).start()
                
                else:
                    # Aqui lidamos com pacotes de vídeo recebidos nas portas 6001 ou 6002
                    for video_name, video_socket in video_sockets.items():
                        if sock == video_socket:
                            data, addr = sock.recvfrom(1024)
                            # Chama a função para processar o pacote de vídeo
                            threading.Thread(target=handle_video_packet, args=(sock, data, addr, routing_table, own_ip,  6001 if video_name == "video_1_avc.mp4" else 6002)).start()

def process_content_server(role, name, client_socket, connections):
    if role == 'ContentServer':

        video_ports = {
            "video_1_avc.mp4": 6001,  # Porta para o video_1_avc.mp4
            "video_2_avc.mp4": 6002   # Porta para o video_2_avc.mp4
        }

        # Cria socket para mensagens de controlo na porta 6000
        control_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        control_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        control_socket.bind(('', 6000))  # Escutando na porta 6000
        print(f"{role} node listening on control port 6000")

        # Cria sockets para os vídeos nas portas específicas
        video_sockets = {}
        for video_name, port in video_ports.items():
            video_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            video_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            video_socket.bind(('', port))  # Escutando na porta do vídeo
            video_sockets[video_name] = video_socket
            print(f"{role} node listening for {video_name} on port {port}")

        # Dicionário para armazenar rotas no formato {(client_ip, video_name): best_route}
        video_routes = {}
        # Set para armazenar transmissões ativas
        active_transmissions = set()

        while True:

            # Espera pacotes nos sockets de controlo e de vídeo
            ready_sockets, _, _ = select.select([control_socket] + list(video_sockets.values()), [], [])

            for sock in ready_sockets:
                if sock == control_socket:
                    # Processamento das mensagens de controlo
                    data, addr = sock.recvfrom(1024)

                    message = data.decode()

                if "send video" in message:

                    print(f"Recebi um request de vídeo.")

                    video_name = message.split()[2]
                    video_path = f"./videos/{video_name}"

                    # Extrai a rota da mensagem
                    if "via" in message:
                        route_info = message.split("via ")[-1]
                        new_route = [ip.strip() for ip in route_info.split(" -> ")]
                        last_ip = new_route[-1]  # Último nó é o client
                        client_ip = last_ip
                    else:
                        print(f"Error: No valid route found in message from {addr[0]}.")
                        continue

                    # Verificar se há sobreposição com rotas existentes
                    if (client_ip, video_name) not in video_routes:
                        video_routes[(client_ip, video_name)] = new_route
                        print(f"Stored route for client {client_ip}, video {video_name}: {new_route}")

                        # Identificar nós que precisam de atualizações específicas
                        for i in range(len(new_route) - 1):
                            current_node_ip = new_route[i]
                            next_hop = new_route[i + 1]

                            # Checar se o nó já está a enviar para este destino
                            for (c_ip, v_name), route in video_routes.items():
                                if c_ip == client_ip and v_name == video_name:
                                    continue  # Ignorar a própria rota
                                if next_hop in route and current_node_ip in route:
                                    print(f"Skipping redundant update: {current_node_ip} -> {next_hop}")
                                    break
                            else:
                                # Enviar atualização de rota
                                route_message = f"route {client_ip} {video_name} {next_hop}"
                                client_socket.sendto(route_message.encode(), (current_node_ip, 6000))
                                print(f"Sent route info to {current_node_ip}: {route_message}")

                    # Iniciar transmissão do vídeo
                    if os.path.exists(video_path):
                        first_hop = video_routes[(client_ip, video_name)][0]

                        # Verificar se o server já está a enviar para o primeiro nó da rota
                        if (first_hop, video_name) in active_transmissions:
                            print(f"Duplicate video request detected for {first_hop}, video {video_name}. Skipping transmission.")
                            continue
                        else:
                            # Adicionar à lista de transmissões ativas
                            active_transmissions.add((first_hop, video_name))
                            
                            print(f"Starting video transmission for {video_name} to {first_hop}")
                            video_thread = threading.Thread(target=start_video_stream, args=(video_path, first_hop, 6001 if video_name == "video_1_avc.mp4" else 6002))
                            video_thread.start()
                    else:
                        print(f"Video file not found: {video_path}")

                else:
                    print(f"Received unknown message from {addr[0]}: {message}")

def main():
    tracker_ip = '10.0.6.2'
    tracker_port = 5000

    # Conectar ao tracker e obter o papel e nome do nó
    role, name = connect_to_tracker(tracker_ip, tracker_port)

    # Carregar os nós da rede a partir do arquivo
    nodes = load_nodes('nodes.txt')

    own_ip = get_ip_by_name('nodes.txt', name)

    # Carregar conexões de overlay estáticas
    overlay_connections = load_overlay_connections('nodes.txt', name)
    connection_names = [conn_name for _, conn_name in overlay_connections]
    print(f"Overlay connections for {name} ({role}): {', '.join(connection_names)}")

    # Criação do socket para o nó, utilizado pelos métodos de cada papel
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client_socket:
        if role == 'PoP':
            process_node(role, name, client_socket, overlay_connections, own_ip)
        elif role == 'Tree':
            process_node(role, name, client_socket, overlay_connections, own_ip)
        elif role == 'ContentServer':
            process_content_server(role, name, client_socket, overlay_connections)
        else:
            print(f"Role {role} not recognized.")
            while True:
                time.sleep(10)

if __name__ == "__main__":
    main()
