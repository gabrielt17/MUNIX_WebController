(() => {

  const joystickEl = document.getElementById('joystick');
  let manager = null;

  let currentL = 0;
  let currentR = 0;

  const MAX_PWM = 1023;

  function createJoystick(sizePx) {

    if (manager) {
      manager.destroy();
      manager = null;
    }

    manager = nipplejs.create({
      zone: joystickEl,
      mode: 'static',
      position: { left: '50%', top: '50%' },
      color: 'white',
      size: sizePx,
      multitouch: false
    });

    manager.on('move', (ev, data) => {

      if (!data || !data.distance || !data.angle) return;

      const angle = data.angle.degree;
      const dist = Math.min(data.distance / (sizePx / 2), 1);

      const rad = angle * Math.PI / 180;
      const x = Math.cos(rad) * dist;
      const y = Math.sin(rad) * dist;

      const L = (y + x);
      const R = (y - x);

      currentL = Math.round(Math.max(-1, Math.min(1, L)) * MAX_PWM);
      currentR = Math.round(Math.max(-1, Math.min(1, R)) * MAX_PWM);

      console.log("Joystick ->", currentL, currentR);
    });

    manager.on('end', () => {
      currentL = 0;
      currentR = 0;
      sendPWM();
    });
  }

  function sendPWM() {

    if (!window.pwmChannel) {
      console.log("No pwmChannel yet");
      return;
    }

    if (window.pwmChannel.readyState !== "open") {
      console.log("Channel not open:", window.pwmChannel.readyState);
      return;
    }

    window.pwmChannel.send(JSON.stringify({
      cmd: "setPWM",
      Lval: currentL,
      Rval: currentR
    }));

    console.log("Sent @10Hz -> L:", currentL, "R:", currentR);
  }

  function startSending() {
    setInterval(sendPWM, 100); // 10 Hz
  }

  function resizeJoystick() {
    const size = Math.min(window.innerWidth * 0.3, 180);
    joystickEl.style.width = size + "px";
    joystickEl.style.height = size + "px";
    createJoystick(size);
  }

  window.addEventListener('load', () => {
    resizeJoystick();
    window.addEventListener('resize', resizeJoystick);
    startSending();
  });

})();