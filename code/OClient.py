import socket
import subprocess  
import time        
import cv2
import numpy as np

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

def decode_frames(buffer):
    """
    Decodes JPEG frames from the buffer using FRAME delimiters.
    """
    FRAME_START = b'\xff\xd8'  # JPEG start marker
    FRAME_END = b'\xff\xd9'    # JPEG end marker

    start_idx = buffer.find(FRAME_START)
    end_idx = buffer.find(FRAME_END, start_idx)

    if start_idx != -1 and end_idx != -1:
        try:
            frame_data = buffer[start_idx:end_idx + len(FRAME_END)]
            frame = cv2.imdecode(np.frombuffer(frame_data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                remaining_buffer = buffer[end_idx + len(FRAME_END):]
                return frame, remaining_buffer
        except Exception as e:
            print(f"Error decoding frame: {e}")
    
    # If no complete frame is found, return None and the buffer unchanged
    return None, buffer


def receive_video(client_ip, client_port):
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_socket.bind((client_ip, client_port))  # Escuta na porta correta
    print(f"Listening for video on {client_ip}:{client_port}")

    cv2.namedWindow("Video Stream", cv2.WINDOW_NORMAL)
    video_data_buffer = b""

    try:
        while True:
            data, addr = client_socket.recvfrom(4096)
            print(f"Received {len(data)} bytes from {addr}")
            video_data_buffer += data

            # Decodifica frames
            frame, video_data_buffer = decode_frames(video_data_buffer)
            if frame is not None:
                cv2.imshow("Video Stream", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):  # Saída com tecla 'q'
                    print("Exiting video stream...")
                    break
            else:
                print("No complete frame yet. Waiting for more data...")

    except Exception as e:
        print(f"Error receiving video: {e}")
    finally:
        cv2.destroyAllWindows()
        client_socket.close()
        print("Socket closed.")

def connect_to_tracker(tracker_ip, tracker_port, client_ip, client_port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client_socket:
        # Envia uma solicitação de "request" para o Tracker
        client_socket.sendto(b"Client request", (tracker_ip, tracker_port))

        # Recebe o papel e o nome do nó do Tracker
        data, _ = client_socket.recvfrom(1024)
        role, name = data.decode().split()
        print(f"Node role assigned by Tracker: {role}")

        # Recebe a lista de PoPs
        data, _ = client_socket.recvfrom(1024)
        pop_list = data.decode().strip().split(', ')
        print(f"Received Points of Presence: {pop_list}")

        # Recebe a lista de vídeos disponíveis
        data, _ = client_socket.recvfrom(1024)
        video_list = data.decode().strip().split(', ')

        print("Videos available:")
        for i, video in enumerate(video_list, 1):
            print(f"{i} - {video}")

        # Espera utilizador escolher um vídeo
        while True:
            try:
                choice = int(input(f"Choose a video to watch (1-{len(video_list)}): "))
                if 1 <= choice <= len(video_list):
                    selected_video = video_list[choice - 1]
                    print(f"You selected: {selected_video}")
                    break
                else:
                    print(f"Invalid choice. Please choose a number between 1 and {len(video_list)}.")
            except ValueError:
                print("Invalid input. Please enter a number.")

    	# Definir a porta com base no vídeo escolhido
        video_port = 6001 if selected_video == "video_1_avc.mp4" else 6002

        # Função para fazer o ping para cada PoP e obter o RTT
        def ping_pop(ip):
            try:
                start = time.time()
                subprocess.run(["ping", "-c", "1", "-W", "1", ip], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                rtt = (time.time() - start) * 1000  # Converte para milissegundos
                return rtt
            except subprocess.CalledProcessError:
                print(f"Failed to ping {ip}")
                return float('inf')  # Retorna infinito se o ping falhar

        # Envia ping para os PoPs e coleta os tempos de resposta
        ping_times = {pop: ping_pop(pop) for pop in pop_list}
        best_pop = min(ping_times, key=ping_times.get)
        print(f"PoP with the fastest connection: {best_pop} ({ping_times[best_pop]:.2f} ms)")

        client_ip = get_ip_by_name('nodes.txt', name)

        # Envia a solicitação para o PoP 
        message = f"send video {selected_video} to {best_pop} via {client_ip}"
        pop_port = 6000 # Porta inicial para comunicação com PoP
        client_socket.sendto(message.encode(), (best_pop, pop_port))  

        receive_video(client_ip, video_port)

if __name__ == "__main__":
    tracker_ip = '10.0.6.2'  # IP do Tracker
    tracker_port = 5000
    client_ip = ''  # inicia a variável
    client_port = 6000  # Porta para mensagens de controlo

    connect_to_tracker(tracker_ip, tracker_port, client_ip, client_port)
