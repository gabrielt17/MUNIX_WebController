// main.js

let pc;
let dataChannel;
window.pwmChannel = null;
const destiny = 'tv_box'

// Create a websocket connection with the TV Box server
const ws = new WebSocket('ws://192.168.2.177:8000/phone');

// Debug messages for websocket connection
ws.onopen = () => {
    console.log('WebSocket connection established');
};
ws.onclose = () => {
    console.log('WebSocket connection closed');
};

document.getElementById('connectBtn').onclick = async () => {
    pc = new RTCPeerConnection();
	
    pc.addTransceiver("video", { direction: "recvonly" });

    // PWM datachannel
    window.pwmChannel = pc.createDataChannel('pwm');

    window.pwmChannel.onopen = () => {
        console.log('PWM channel opened, state:', window.pwmChannel.readyState);
    };

    window.pwmChannel.onclose = () => {
        console.log('PWM channel closed');
    };

    window.pwmChannel.onerror = (e) => {
        console.error('PWM channel error', e);
    };

    window.pwmChannel.onmessage = (evt) => {
        console.log('Received from TV Box:', evt.data);
    };

    // Video track
    pc.ontrack = (evt) => {
        const video = document.getElementById('remoteVideo');
        video.srcObject = evt.streams[0];
    }

    // SDP offer
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    console.log("Offer SDP:\n", offer.sdp);
    // Send offer to TV Box server via websocket
    ws.send(JSON.stringify({
        id: destiny,
        type: 'offer', 
        sdp: offer.sdp
    }));

    // Creates ICE Candidates
    pc.onicecandidate = (event) => {
    if (!event.candidate) return;

    const cand = event.candidate.candidate;

    console.log("ICE gerado:", cand);

    // Ignora candidatos mDNS (.local)
    if (cand.includes(".local")) {
        console.log("Ignorando candidate mDNS:", cand);
        return;
    }

    ws.send(JSON.stringify({
        id: destiny,
        type: "ice-candidate",
        candidate: {
            candidate: event.candidate.candidate,
            sdpMLineIndex: event.candidate.sdpMLineIndex
        }
    }));
};
}

// Adds SDP answers and ICE Candidates received from the TV Box server
ws.onmessage = async (msg) => {
    console.log('WS onmessage raw:', msg.data);
    const data = JSON.parse(msg.data);
    console.log('WS parsed:', data);

    if (data.type === 'answer') {
        const remoteDesc = {
            type: 'answer',
            sdp: data.sdp
        };
        await pc.setRemoteDescription(remoteDesc);
    } else if (data.type === 'ice-candidate') {
        pc.addIceCandidate(new RTCIceCandidate(data.candidate))
    }
}
