import socket
import os

def load_nodes_config(filename="nodes.txt"):
    nodes_roles = {}
    pop_list = []
    with open(filename, "r") as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#"):
                ip_role_pairs = line.split()
                ip = ip_role_pairs[0]
                role = ip_role_pairs[1]
                name = ip_role_pairs[2]
                nodes_roles[ip] = (role, name)
                if role == 'PoP':
                    pop_list.append(ip)
    return nodes_roles, pop_list

def get_video_list(directory="videos"):
    try:
        video_files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
        return video_files
    except FileNotFoundError:
        return []

def main():
    nodes_roles, pop_list = load_nodes_config()
    video_list = get_video_list()
    
    tracker_host = '0.0.0.0'
    tracker_port = 5000

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind((tracker_host, tracker_port))
    print(f"Tracker listening on UDP {tracker_host}:{tracker_port}")

    while True:
        data, addr = server_socket.recvfrom(1024)
        ip_address = addr[0]
        print(f"Received request from {ip_address}")

        role, name = nodes_roles.get(ip_address, ("Unknown", "unknown"))
        
        # Envia o papel e o nome do nó
        role_message = f"{role} {name}"
        server_socket.sendto(role_message.encode(), addr)

        # Envia lista de PoPs e vídeos se o nó for desconhecido
        if role == "Client":
            pop_message = ', '.join(pop_list)
            server_socket.sendto(pop_message.encode(), addr)

            video_message = ', '.join(video_list)
            server_socket.sendto(video_message.encode(), addr)

if __name__ == "__main__":
    main()
