// Node.js 22+；VNC_PASSWORD 必须与构建时一致。无需 npm install。
const assert = require('node:assert/strict');
const { createCipheriv } = require('node:crypto');
const { on, once } = require('node:events');

const cdpURL = process.env.CDP_URL || 'http://127.0.0.1:9222';
const novncURL = process.env.NOVNC_URL || 'http://127.0.0.1:6080/';
const password = process.env.VNC_PASSWORD || '';

async function checkVnc(candidate, expectedStatus = 0) {
  const url = new URL('websockify', novncURL);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(url, 'binary');
  socket.binaryType = 'arraybuffer';
  const options = { signal: AbortSignal.timeout(10000) };
  const messages = on(socket, 'message', options);
  let buffer = Buffer.alloc(0);

  async function read(size) {
    while (buffer.length < size) {
      const { value, done } = await messages.next();
      assert.ok(!done, 'VNC connection closed early');
      buffer = Buffer.concat([buffer, Buffer.from(value[0].data)]);
    }
    const result = buffer.subarray(0, size);
    buffer = buffer.subarray(size);
    return result;
  }

  try {
    await once(socket, 'open', options);
    assert.equal((await read(12)).toString(), 'RFB 003.008\n');
    socket.send(Buffer.from('RFB 003.008\n'));
    const count = (await read(1))[0];
    const securityTypes = [...await read(count)];
    assert.deepEqual(securityTypes, [candidate ? 2 : 1], 'Unexpected VNC authentication mode');
    socket.send(Uint8Array.of(candidate ? 2 : 1));

    if (candidate) {
      const challenge = await read(16);
      const key = Buffer.alloc(8);
      key.write(candidate, 'ascii');
      // VNC 的 DES 密钥逐字节反转位序；相同的三个 3DES 密钥等价于 DES。
      for (let i = 0; i < key.length; i++) {
        key[i] = parseInt(key[i].toString(2).padStart(8, '0').split('').reverse().join(''), 2);
      }
      const cipher = createCipheriv('des-ede3', Buffer.concat([key, key, key]), null);
      cipher.setAutoPadding(false);
      socket.send(Buffer.concat([cipher.update(challenge), cipher.final()]));
    }

    assert.equal((await read(4)).readUInt32BE(), expectedStatus, 'Unexpected VNC authentication result');
    if (expectedStatus === 0) {
      socket.send(Uint8Array.of(1)); // 共享连接，不踢掉已有 noVNC 会话。
      const init = await read(24);
      assert.ok(init.readUInt16BE(0) > 0 && init.readUInt16BE(2) > 0, 'Missing VNC desktop');
    }
  } finally {
    await messages.return();
    socket.close();
  }
}

async function main() {
  assert.match(password, /^[\x20-\x7e]{0,8}$/, 'VNC_PASSWORD must be empty or 1-8 printable ASCII characters');
  const response = await fetch(new URL('/json/version', cdpURL), { signal: AbortSignal.timeout(10000) });
  assert.ok(response.ok, `CDP HTTP ${response.status}`);
  const version = await response.json();
  assert.ok(version.Browser.startsWith('Chrome/'));

  const socket = new WebSocket(version.webSocketDebuggerUrl);
  const options = { signal: AbortSignal.timeout(10000) };
  try {
    await once(socket, 'open', options);
    const reply = once(socket, 'message', options);
    socket.send(JSON.stringify({ id: 1, method: 'Browser.getVersion' }));
    const result = JSON.parse((await reply)[0].data);
    assert.equal(result.id, 1);
    assert.equal(result.result.product, version.Browser);
  } finally {
    socket.close();
  }
  console.log('PASS CDP HTTP + WebSocket through proxy');

  const page = await fetch(new URL('vnc.html', novncURL), { signal: AbortSignal.timeout(10000) });
  assert.ok(page.ok, `noVNC HTTP ${page.status}`);
  await checkVnc(password);
  if (password) {
    await checkVnc((password[0] === 'X' ? 'Y' : 'X') + password.slice(1), 1);
    console.log('PASS noVNC accepts correct password and rejects wrong password');
  } else {
    console.log('PASS noVNC without password');
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
