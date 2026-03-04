# signaling.py

import sys
import json
import asyncio
import logging
import websockets


# Inicia o logger para entender os erros.
logger = logging.getLogger("websockets")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))


# Dicionário na qual os clientes vão ser guardados.
clients = {}

# Callback para lidar com conexões de novos clientes.
async def handle_websocket(websocket):
    client_id = None
    try:
        splitted = websocket.path.strip("/").split("/")
        if not splitted:
            raise Exception("Missing client ID in path.")
        client_id = splitted[0]
        print(f"Client {client_id} connected.")

        clients[client_id] = websocket
        async for data in websocket:
            print(f"Client {client_id} has sent {data}.")
            message = json.loads(data)
            destination_id = message["id"]
            destination_ws = clients.get(destination_id)
            if destination_ws: 
                message["id"] = client_id
                data = json.dumps(message)
                if destination_ws.open:
                    await destination_ws.send(data)
                    print(f"Client {destination_id} was directed {data}.")
                else:
                    del clients[destination_id]
                    print(f"Removed dead client {destination_id}.")
                
            else:
                print(f"Client {destination_id} not found.")

    except Exception as e:
        print("Connection error:", e, ".")

    finally:
        if client_id and client_id in clients:
            del clients[client_id]
            print(f"Client {client_id} disconnected.")

async def main():
    endpoint = "0.0.0.0"
    port = 8000
    # print(f"Listening on {endpoint}:{port}")
    server = await websockets.serve(handle_websocket, endpoint, port)
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())