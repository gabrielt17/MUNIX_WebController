#!/usr/bin/env python3
import asyncio
import json
import signal
import sys
import websockets
import gi
import os
import socket
import re


gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")

from gi.repository import Gst, GstWebRTC, GstSdp, GLib

Gst.init(None)

# Configs (mude se necessário)
SIGNALING_SERVER = os.environ.get("SIGNALING_SERVER", "ws://localhost:8000/tv_box")
DESTINY = os.environ.get("DESTINY", "phone")
VIDEO_DEVICE = os.environ.get("VIDEO_DEVICE", "/dev/video0")  # ajuste se for /dev/video0

# ESP32 Setup
ESP32_IP = "192.168.2.141"   # ajuste
ESP32_PORT = 4210


class WebRTCCam:
    def __init__(self):
        # cria e registra um event loop explícito (evita DeprecationWarning)
        self.loop = asyncio.new_event_loop()
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        asyncio.set_event_loop(self.loop)

        self.pipeline = None
        self.webrtc = None
        self.ws = None
        self.running = True

    # ---------------- PIPELINE ----------------
    def create_pipeline(self):
        
        pipeline_desc = (
            f"v4l2src device={VIDEO_DEVICE} do-timestamp=true ! "
            "image/jpeg,width=640,height=480,framerate=30/1 ! "
            "jpegdec ! videoconvert ! queue leaky=downstream max-size-buffers=30 ! "
            "vp8enc deadline=1 cpu-used=8 keyframe-max-dist=30 ! "
            "rtpvp8pay name=payloader pt=96 picture-id-mode=2 ! "
            "application/x-rtp,media=video,encoding-name=VP8,clock-rate=90000 ! "
            "queue ! webrtcbin name=webrtc bundle-policy=max-bundle"
        )

        print("Alocando elementos da pipeline na memória...")
        self.pipeline = Gst.parse_launch(pipeline_desc)
        
        self.webrtc = self.pipeline.get_by_name("webrtc")
        self.payloader = self.pipeline.get_by_name("payloader")
        
        if not self.webrtc or not self.payloader:
            raise RuntimeError("Falha ao obter webrtcbin ou payloader")

        self.webrtc.connect("on-ice-candidate", self.on_ice_candidate)
        self.webrtc.connect("on-data-channel", self.on_data_channel)
        
        print("Pipeline criada com sucesso (Estado: NULL)")

    # ---------------- SIGNALING ----------------
    async def connect(self):
        print("Conectando ao servidor de sinalização:", SIGNALING_SERVER)
        try:
            self.ws = await websockets.connect(SIGNALING_SERVER)
        except Exception as e:
            print("Falha ao conectar ao servidor de sinalização:", e)
            return

        print("Conectado ao servidor de sinalização")

        # CORREÇÃO: Criar a pipeline em estado NULL na memória antes de ouvir o WebSocket
        self.create_pipeline()

        # loop principal de recebimento
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                except Exception:
                    print("Mensagem inválida recebida:", message)
                    continue
                await self.handle_message(data)
        except websockets.ConnectionClosed:
            print("WebSocket fechado")
        except Exception as e:
            print("Erro no loop do websocket:", e)
        finally:
            await self.shutdown()

    async def handle_message(self, data):
        typ = data.get("type")
        if typ == "offer":
            await self.handle_offer(data)
        elif typ == "ice-candidate":
            # candidato vem no campo "candidate" (dict)
            cand = data.get("candidate")
            if cand:
                self.handle_remote_ice(cand)
        else:
            # outros tipos podem ser logados
            print("Mensagem de signaling desconhecida:", data)

    # ---------------- OFFER HANDLING ----------------
    
    def on_data_channel(self, element, channel):
        print("DataChannel criado")

        channel.connect("on-message-string", self.on_data_message)
    
    def on_data_message(self, channel, message):

        print("Mensagem recebida:", message)
        try:
            # Normaliza para string se for bytes
            if isinstance(message, (bytes, bytearray)):
                text = message.decode('utf-8')
            else:
                text = str(message)

            # Parse do JSON (levanta exceção se inválido)
            data = json.loads(text)

            # Envia o JSON completo ao ESP32 como string UTF-8
            udp_payload = json.dumps(data)
            self.udp_socket.sendto(udp_payload.encode('utf-8'), (ESP32_IP, ESP32_PORT))

            print("Enviado UDP para ESP32:", udp_payload)

        except Exception as e:
            print("Erro ao processar mensagem:", e)
    
    async def handle_offer(self, data):
        print("Offer recebida")

        sdp = data.get("sdp", "")
        if not sdp:
            print("Offer sem SDP")
            return
        
        res, sdpmsg = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(bytes(sdp.encode()), sdpmsg)

        offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, sdpmsg
        )
        
        match = re.search(r'a=rtpmap:(\d+)\s+VP8/90000', offer.sdp.as_text())
        vp8_pt = int(match.group(1)) if match else 96
        
        print(f"Ajustando dinamicamente o Payload Type para: {vp8_pt}")
        self.payloader.set_property("pt", vp8_pt)

        # CORREÇÃO: A pipeline DEVE estar rodando antes de processar o SDP
        print("Iniciando a câmera e a pipeline (PLAYING)...")
        clock = Gst.SystemClock.obtain()
        self.pipeline.use_clock(clock)
        self.pipeline.set_start_time(Gst.CLOCK_TIME_NONE)
        self.pipeline.set_state(Gst.State.PLAYING)

        # CORREÇÃO: Callback para garantir que create-answer só rode APÓS o set-remote terminar
        def on_remote_set(promise, _):
            promise.wait()
            print("Remote description aplicada com sucesso. Gerando answer...")
            reply_promise = Gst.Promise.new_with_change_func(self.on_answer_created, None)
            self.webrtc.emit("create-answer", None, reply_promise)

        # Emite a oferta remota e aguarda o callback acima
        promise = Gst.Promise.new_with_change_func(on_remote_set, None)
        self.webrtc.emit("set-remote-description", offer, promise)

    def on_answer_created(self, promise, _):
        try:
            promise.wait()
            reply = promise.get_reply()
            answer = reply.get_value("answer")
            if not answer:
                print("create-answer retornou None")
                return

            # set local
            p = Gst.Promise.new()
            self.webrtc.emit("set-local-description", answer, p)
            p.interrupt()

            text = answer.sdp.as_text()

            # envia answer (envia para DESTINY)
            fut = asyncio.run_coroutine_threadsafe(
                self.ws.send(json.dumps({
                    "id": DESTINY,
                    "type": "answer",
                    "sdp": text
                })), self.loop)
            # opcional: aguardar confirmação da future ou logar exceção
            try:
                fut.result(timeout=5)
            except Exception:
                pass

            print("Answer enviada")
            print(text)
        except Exception as e:
            print("Erro em on_answer_created:", e)

    # ---------------- ICE ----------------
    def on_ice_candidate(self, element, mlineindex, candidate):
        # candidate é string. Envia pro signaling
        try:
            payload = {
                "id": DESTINY,
                "type": "ice-candidate",
                "candidate": {
                    "candidate": candidate,
                    "sdpMLineIndex": mlineindex
                }
            }
            asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(payload)), self.loop)
        except Exception as e:
            print("Erro enviando ICE local:", e)

    def handle_remote_ice(self, candidate):
        """
        candidate: dict com keys 'candidate', 'sdpMLineIndex', ...
        Regras:
         - ignora candidatos .local (mDNS) — o navegador já deveria filtrá-los
         - ignora candidates vazios (final-of-candidates)
        """
        try:
            cand_str = candidate.get("candidate", "")
            if not cand_str:
                # candidato vazio (fim da coleta)
                # opcionalmente você pode sinalizar ou ignorar
                print("Candidate recebido vazio — ignorando")
                return

            print("Candidate recebido:", cand_str)

            # # Ignora candidates mDNS (.local)
            # if ".local" in cand_str:
            #     print("Ignorando candidate mDNS")
            #     return

            # adiciona ao webrtcbin
            try:
                self.webrtc.emit("add-ice-candidate", candidate["sdpMLineIndex"], cand_str)
            except Exception as e:
                print("Falha ao adicionar ICE candidate:", e)
        except Exception as e:
            print("Erro em handle_remote_ice:", e)

    # ---------------- SHUTDOWN ----------------
    async def shutdown(self):
        print("Shutting down...")
        self.running = False
        try:
            if self.ws and not self.ws.closed:
                await self.ws.close()
        except Exception:
            pass
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        print("Stopped")

def main():
    cam = WebRTCCam()

    # trap Ctrl-C para shutdown ordenado
    loop = cam.loop
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(cam.shutdown()))

    try:
        loop.run_until_complete(cam.connect())
    finally:
        # garante cleanup
        loop.run_until_complete(cam.shutdown())
        loop.close()

if __name__ == "__main__":
    main()