/**
 * OSDP firmware flashing from the browser over Web Serial (Chrome and Edge).
 *
 * Mirrors the original Python OSDP library CALIGA used to flash from the server: plain-text OSDP
 * with CRC, sequence numbers 0 -> 1 -> 2 -> 3 -> 1, a 0xFF line-mark byte before each command,
 * reader discovery across baud rates and addresses 0-7, COMSET to 115200, an osdp_ID + osdp_POLL
 * health check, then osdp_FILETRANSFER fragments until the reader reports it's rebooting.
 *
 * Works with any transport that has write(bytes), read(n, timeoutMs), setBaud(baud),
 * flushInput() and close(). WebSerialTransport is the browser one. Tests use a fake.
 */

export const SOM = 0x53;
export const LINE_MARK = 0xFF;
export const HIGH_SPEED_BAUD = 115200;
export const STANDARD_BAUDS = [9600, 19200, 38400, 57600, 115200];
export const DEFAULT_FRAGMENT_SIZE = 1024;

export const CMD = { POLL: 0x60, ID: 0x61, COMSET: 0x6E, FILETRANSFER: 0x7C };
export const REPLY = { ACK: 0x40, NAK: 0x41, PDID: 0x45, LSTATR: 0x48, COM: 0x54, FTSTAT: 0x7A };
export const FT_STATUS = { OK: 0, FILE_PROCESSED: 1, REBOOTING: 2, FINISHING: 3 };
const FT_STATUS_NAMES = { '-1': 'ABORT_FILE_TRANSFER', '-2': 'UNRECOGNIZED_FILE_CONTENTS', '-3': 'FILE_DATA_UNACCEPTABLE' };

const CRC_FLAG = 0x04;
const SCB_FLAG = 0x08;
const REPLY_TIMEOUT_MS = 2000;
const DISCOVERY_TIMEOUT_MS = 300;
const MAX_FT_DELAY_MS = 5000;
const MAX_FINISHING_POLLS = 60;

export class OsdpError extends Error {}
export class OsdpTimeoutError extends OsdpError {}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** CRC-16/CCITT (polynomial 0x1021) with initial value 0x1D0F, as OSDP specifies. */
export function crc16(bytes) {
    let crc = 0x1D0F;
    for (const byte of bytes) {
        crc ^= byte << 8;
        for (let i = 0; i < 8; i++) {
            crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
        }
    }
    return crc;
}

function le(value, size) {
    const out = [];
    for (let i = 0; i < size; i++) out.push((value >>> (8 * i)) & 0xFF);
    return out;
}

/** Builds a command frame (without the leading line-mark byte). */
export function encodeCommand(address, sequence, code, data = []) {
    const body = [SOM, address & 0x7F, 0, 0, (sequence & 0x03) | CRC_FLAG, code, ...data];
    const length = body.length + 2;
    body[2] = length & 0xFF;
    body[3] = (length >> 8) & 0xFF;
    const crc = crc16(body);
    return Uint8Array.from([...body, crc & 0xFF, (crc >> 8) & 0xFF]);
}

/** Parses a reply frame. Throws OsdpError on bad framing, CRC, or an unsupported secure channel reply. */
export function decodeReply(frame) {
    if (frame.length < 8 || frame[0] !== SOM) throw new OsdpError('Malformed OSDP reply.');
    const length = frame[2] | (frame[3] << 8);
    if (length !== frame.length) throw new OsdpError(`OSDP reply length mismatch (header ${length}, got ${frame.length}).`);

    const control = frame[4];
    if (!(control & CRC_FLAG)) throw new OsdpError('OSDP reply uses a checksum instead of CRC.');
    if (control & SCB_FLAG) throw new OsdpError('The reader replied in secure channel, which browser flashing does not support.');

    const received = frame[length - 2] | (frame[length - 1] << 8);
    const calculated = crc16(frame.subarray(0, length - 2));
    if (received !== calculated) throw new OsdpError('OSDP reply failed its CRC check.');

    return {
        isReply: (frame[1] & 0x80) !== 0,
        address: frame[1] & 0x7F,
        sequence: control & 0x03,
        code: frame[5],
        data: frame.subarray(6, length - 2),
    };
}

export function comsetData(address, baud) {
    return [address & 0x7F, ...le(baud, 4)];
}

export function fileTransferData(totalSize, offset, fragment) {
    return [0x01, ...le(totalSize, 4), ...le(offset, 4), ...le(fragment.length, 2), ...fragment];
}

export function parseFtstat(data) {
    if (data.length !== 7) throw new OsdpError('Malformed osdp_FTSTAT reply.');
    const rawStatus = data[3] | (data[4] << 8);
    return {
        action: data[0],
        delayMs: data[1] | (data[2] << 8),
        status: rawStatus & 0x8000 ? rawStatus - 0x10000 : rawStatus,
        updateMsgMax: data[5] | (data[6] << 8),
    };
}

/** Reads one OSDP packet: skips to SOM, then reads the length the header gives. */
export async function receivePacket(transport, timeoutMs) {
    for (;;) {
        const b = await transport.read(1, timeoutMs);
        if (b.length === 0) throw new OsdpTimeoutError('Timed out waiting for the reader to reply.');
        if (b[0] === SOM) break;
    }
    const header = await transport.read(3, timeoutMs);
    if (header.length < 3) throw new OsdpTimeoutError('Timed out reading the reply header.');
    const total = header[1] | (header[2] << 8);
    if (total < 4) throw new OsdpError('Invalid OSDP reply length.');

    const packet = new Uint8Array(total);
    packet[0] = SOM;
    packet.set(header, 1);
    let filled = 4;
    while (filled < total) {
        const chunk = await transport.read(total - filled, timeoutMs);
        if (chunk.length === 0) throw new OsdpTimeoutError('Timed out reading the reply.');
        packet.set(chunk, filled);
        filled += chunk.length;
    }
    return packet;
}

/** One OSDP conversation with one reader address, tracking sequence numbers like OsdpMessageHandler. */
export class OsdpSession {
    constructor(transport, address) {
        this.transport = transport;
        this.address = address;
        this.sequence = 0;
    }

    async transact(code, data = [], timeoutMs = REPLY_TIMEOUT_MS) {
        const frame = encodeCommand(this.address, this.sequence, code, data);
        await this.transport.write(Uint8Array.from([LINE_MARK, ...frame]));

        const reply = decodeReply(await receivePacket(this.transport, timeoutMs));
        if (!reply.isReply || reply.address !== this.address) {
            throw new OsdpError(`Unexpected reply from address ${reply.address}.`);
        }
        if (reply.sequence === 0) {
            this.sequence = 0; // The reader reset the sequence (e.g. after a reboot).
        } else if (reply.sequence !== this.sequence) {
            throw new OsdpError(`OSDP sequence mismatch (expected ${this.sequence}, got ${reply.sequence}).`);
        }
        this.sequence = this.sequence === 3 ? 1 : this.sequence + 1;
        return reply;
    }
}

/** Finds the reader's baud rate and address. Returns {baud, address} or null. */
export async function discoverReader(transport, { bauds = STANDARD_BAUDS, addresses = [0, 1, 2, 3, 4, 5, 6, 7] } = {}) {
    const ordered = [HIGH_SPEED_BAUD, ...bauds.filter(b => b !== HIGH_SPEED_BAUD)];
    for (const baud of ordered) {
        await transport.setBaud(baud);
        for (const address of addresses) {
            // An address that doesn't answer times out. Keep going so one silent address
            // doesn't skip the rest of the scan at this baud rate.
            try {
                await transport.flushInput();
                await new OsdpSession(transport, address).transact(CMD.POLL, [], DISCOVERY_TIMEOUT_MS);
                return { baud, address };
            } catch (err) {
                if (!(err instanceof OsdpError)) throw err;
            }
        }
    }
    return null;
}

function nakError(reply) {
    return new OsdpError(`The reader rejected the command (NAK code ${reply.data[0] ?? 'unknown'}).`);
}

/**
 * Flashes firmware (a Uint8Array) to the reader on the transport.
 * Returns {baud, address, message}. Throws OsdpError with a readable message on failure.
 */
export async function flashFirmware(transport, firmware, { onStatus = () => {}, onProgress = () => {}, fragmentSize = DEFAULT_FRAGMENT_SIZE } = {}) {
    if (!firmware || firmware.length === 0) throw new OsdpError('The firmware file is empty.');

    onStatus('Searching for the reader (baud rates and addresses 0-7)...');
    const found = await discoverReader(transport);
    if (!found) {
        throw new OsdpError('No reader answered at any standard baud rate (addresses 0-7). Check wiring and power, then try again.');
    }
    const { baud: originalBaud, address } = found;
    await transport.setBaud(originalBaud);
    await transport.flushInput();
    const session = new OsdpSession(transport, address);

    if (originalBaud !== HIGH_SPEED_BAUD) {
        onStatus(`Reader found at ${originalBaud} baud, address ${address}. Raising baud rate to ${HIGH_SPEED_BAUD}...`);
        try {
            await session.transact(CMD.COMSET, comsetData(address, HIGH_SPEED_BAUD));
            await transport.setBaud(HIGH_SPEED_BAUD);
            await sleep(300);
        } catch (err) {
            if (!(err instanceof OsdpError)) throw err;
            onStatus(`Couldn't raise the baud rate (${err.message}). Continuing at ${originalBaud}...`);
        }
    } else {
        onStatus(`Reader found at ${originalBaud} baud, address ${address}.`);
    }

    // Health check before transferring, like the Python flasher: osdp_ID then osdp_POLL.
    const id = await session.transact(CMD.ID, [0x00]);
    if (id.code === REPLY.NAK) throw nakError(id);
    const poll = await session.transact(CMD.POLL);
    if (poll.code === REPLY.NAK) throw nakError(poll);

    const total = firmware.length;
    let sent = 0;
    let state = FT_STATUS.OK;
    let finishingPolls = 0;
    onStatus('Transferring firmware...');

    while (state >= FT_STATUS.OK && state !== FT_STATUS.REBOOTING) {
        let fragment;
        if (sent < total && (state === FT_STATUS.OK || state === FT_STATUS.FILE_PROCESSED)) {
            fragment = firmware.subarray(sent, sent + fragmentSize);
        } else {
            // The whole file is sent. Keep asking with empty fragments until the reader says it's rebooting.
            fragment = new Uint8Array(0);
            if (++finishingPolls > MAX_FINISHING_POLLS) {
                throw new OsdpError('The reader received the whole file but never reported that it was rebooting.');
            }
        }
        const offset = fragment.length ? sent : total;

        let reply;
        try {
            reply = await session.transact(CMD.FILETRANSFER, fileTransferData(total, offset, fragment));
        } catch (err) {
            // Once this fragment completes the file, the reader may reboot without a final reply.
            if (err instanceof OsdpTimeoutError && sent + fragment.length >= total) {
                return { baud: originalBaud, address, message: 'Firmware transferred. The reader stopped responding, so it is probably rebooting.' };
            }
            throw err;
        }

        if (reply.code === REPLY.NAK) throw nakError(reply);
        if (reply.code !== REPLY.FTSTAT) throw new OsdpError(`Unexpected reply 0x${reply.code.toString(16)} during file transfer.`);

        const ftstat = parseFtstat(reply.data);
        if (ftstat.status < 0) {
            throw new OsdpError(`The reader stopped the transfer: ${FT_STATUS_NAMES[ftstat.status] || ftstat.status}.`);
        }

        sent += fragment.length;
        state = ftstat.status;
        if (ftstat.updateMsgMax > 0) fragmentSize = ftstat.updateMsgMax;
        onProgress(Math.round((sent / total) * 1000) / 10);
        if (ftstat.delayMs > 0) await sleep(Math.min(ftstat.delayMs, MAX_FT_DELAY_MS));
    }

    if (originalBaud !== HIGH_SPEED_BAUD) {
        // Best effort, as in the Python flasher. The reader is usually already rebooting.
        try {
            await session.transact(CMD.COMSET, comsetData(address, originalBaud), 500);
        } catch (err) {
            if (!(err instanceof OsdpError)) throw err;
        }
    }

    return { baud: originalBaud, address, message: 'Firmware transferred. The reader is rebooting. Please wait until the reboot is complete.' };
}

/** Transport for a Web Serial SerialPort (navigator.serial). */
export class WebSerialTransport {
    constructor(port) {
        this.port = port;
        this.baud = null;
        this.buffer = [];
        this.waiter = null;
        this.reader = null;
        this.readLoop = null;
    }

    async setBaud(baud) {
        if (this.baud === baud) return;
        await this.close();
        await this.port.open({ baudRate: baud, dataBits: 8, stopBits: 1, parity: 'none', bufferSize: 8192 });
        this.baud = baud;
        this.buffer = [];
        this.readLoop = this._readForever();
    }

    async _readForever() {
        while (this.port.readable) {
            this.reader = this.port.readable.getReader();
            try {
                for (;;) {
                    const { value, done } = await this.reader.read();
                    if (done) return;
                    for (const byte of value) this.buffer.push(byte);
                    if (this.waiter) this.waiter();
                }
            } catch (err) {
                // A framing or parity error ends this reader. Start a new one unless the port is closing.
                if (this.baud === null) return;
            } finally {
                this.reader.releaseLock();
                this.reader = null;
            }
        }
    }

    async read(n, timeoutMs) {
        const deadline = Date.now() + timeoutMs;
        while (this.buffer.length < n) {
            const remaining = deadline - Date.now();
            if (remaining <= 0) break;
            await new Promise(resolve => {
                const timer = setTimeout(resolve, remaining);
                this.waiter = () => { clearTimeout(timer); resolve(); };
            });
            this.waiter = null;
        }
        return Uint8Array.from(this.buffer.splice(0, n));
    }

    async write(bytes) {
        const writer = this.port.writable.getWriter();
        try {
            await writer.write(bytes);
        } finally {
            writer.releaseLock();
        }
    }

    async flushInput() {
        this.buffer = [];
    }

    async close() {
        if (this.baud === null) return;
        this.baud = null;
        try {
            if (this.reader) await this.reader.cancel();
            await this.readLoop;
        } catch (err) { /* already closed */ }
        try {
            await this.port.close();
        } catch (err) { /* already closed */ }
    }
}
