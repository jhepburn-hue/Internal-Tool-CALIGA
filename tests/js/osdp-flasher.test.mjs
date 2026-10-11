import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const [, , modulePath, refPath] = process.argv;
const m = await import(pathToFileURL(modulePath).href);
const ref = JSON.parse(readFileSync(refPath, 'utf8'));
const hex = b => Buffer.from(b).toString('hex');
const unhex = h => Uint8Array.from(Buffer.from(h, 'hex'));
let pass = 0, fail = 0;
const check = (name, cond, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : '  ' + detail}`); };

// 1. CRC and command encoding match osdp_library byte for byte
for (const c of ref.crc) check(`crc ${c.data.slice(0, 12) || '(empty)'}`, m.crc16(unhex(c.data)) === c.crc, `${m.crc16(unhex(c.data))} vs ${c.crc}`);
for (const c of ref.commands) {
    let data = c.data !== undefined ? [...unhex(c.data)] : [];
    let code = c.code;
    if (c.comset) { code = m.CMD.COMSET; data = m.comsetData(...c.comset); }
    if (c.ft) { code = m.CMD.FILETRANSFER; data = m.fileTransferData(c.ft[0], c.ft[1], unhex(c.ft[2])); }
    const got = hex(m.encodeCommand(c.address, c.seq, code, data));
    check(`encode ${c.name}`, got === c.hex, `\n   js ${got}\n   py ${c.hex}`);
}
// 2. Reply decoding of osdp_library-built replies
for (const r of ref.replies) {
    const d = m.decodeReply(unhex(r.hex));
    check(`decode ${r.name}`, d.isReply && d.address === r.address && d.sequence === r.seq && d.code === r.code);
}
const ok = m.parseFtstat(m.decodeReply(unhex(ref.replies[1].hex)).data);
check('ftstat ok fields', ok.status === 0 && ok.updateMsgMax === 256 && ok.action === 1, JSON.stringify(ok));
check('ftstat abort = -1', m.parseFtstat(m.decodeReply(unhex(ref.replies[2].hex)).data).status === -1);
const bad = unhex(ref.replies[0].hex); bad[bad.length - 1] ^= 0xFF;
let crcRejected = false; try { m.decodeReply(bad); } catch (e) { crcRejected = e instanceof m.OsdpError; }
check('bad CRC rejected', crcRejected);

// 3. Full flash against a fake reader
function replyFrame(address, seq, code, data = []) {
    const body = [m.SOM, address | 0x80, 0, 0, (seq & 3) | 0x04, code, ...data];
    const len = body.length + 2; body[2] = len & 0xFF; body[3] = len >> 8;
    const crc = m.crc16(body); return [...body, crc & 0xFF, crc >> 8];
}
class FakeReader {
    constructor({ baud = 115200, address = 1, maxMsg = 0, finishAfter = 'rebooting', nakAt = -1, silentAddresses = [] }) {
        Object.assign(this, { readerBaud: baud, address, maxMsg, finishAfter, nakAt, silentAddresses });
        this.portBaud = null; this.rx = []; this.received = []; this.fragments = []; this.bauds = []; this.ftCount = 0;
    }
    async setBaud(b) { this.portBaud = b; this.bauds.push(b); }
    async flushInput() { this.rx = []; }
    async close() {}
    async read(n) { return Uint8Array.from(this.rx.splice(0, n)); }
    async write(bytes) {
        if (bytes[0] !== 0xFF) throw new Error('missing line mark');
        const f = bytes.subarray(1);
        if (this.portBaud !== this.readerBaud) return;               // wrong baud: garbage, no reply
        const addr = f[1], seq = f[4] & 3, code = f[5], data = f.subarray(6, f.length - 2);
        if (addr !== this.address) return;                          // not us
        let reply;
        if (code === m.CMD.POLL) reply = replyFrame(addr, seq, m.REPLY.ACK);
        else if (code === m.CMD.ID) reply = replyFrame(addr, seq, m.REPLY.PDID, new Array(12).fill(0));
        else if (code === m.CMD.COMSET) { reply = replyFrame(addr, seq, m.REPLY.COM, [...data]); this.pendingBaud = data[1] | data[2] << 8 | data[3] << 16 | data[4] << 24; }
        else if (code === m.CMD.FILETRANSFER) {
            this.ftCount++;
            if (this.ftCount === this.nakAt) { this.rx.push(...replyFrame(addr, seq, m.REPLY.NAK, [0x01])); return; }
            const total = data[1] | data[2] << 8 | data[3] << 16 | data[4] << 24;
            const offset = data[5] | data[6] << 8 | data[7] << 16 | data[8] << 24;
            const size = data[9] | data[10] << 8;
            const frag = data.subarray(11);
            if (frag.length !== size) throw new Error('fragment size mismatch');
            if (size) { if (offset !== this.received.length) throw new Error(`bad offset ${offset}`); this.received.push(...frag); this.fragments.push(size); }
            let status = 0;
            if (this.received.length >= total) {
                if (this.finishAfter === 'silent') return;             // reboots without replying
                status = this.finishAfter === 'finishing' && !this.sawFinishing ? (this.sawFinishing = 1, 3) : 2;
            }
            const max = this.maxMsg;
            reply = replyFrame(addr, seq, m.REPLY.FTSTAT, [1, 0, 0, status & 0xFF, (status >> 8) & 0xFF, max & 0xFF, max >> 8]);
        }
        this.rx.push(...reply);
        if (this.pendingBaud) { this.readerBaud = this.pendingBaud; this.pendingBaud = 0; }
    }
}
const firmware = Uint8Array.from({ length: 5000 }, (_, i) => (i * 7 + 3) & 0xFF);
const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

let pd = new FakeReader({ baud: 115200, address: 1 });
let progress = [];
let res = await m.flashFirmware(pd, firmware, { onProgress: p => progress.push(p) });
check('flash @115200 addr1: file intact', same(pd.received, firmware));
check('flash: 1024-byte fragments', pd.fragments.slice(0, 4).every(s => s === 1024) && pd.fragments.at(-1) === 5000 - 4096);
check('flash: progress reaches 100', progress.at(-1) === 100, progress.join(','));
check('flash: found address 1 (address 0 silent)', res.address === 1 && res.baud === 115200);

pd = new FakeReader({ baud: 9600, address: 2, maxMsg: 512 });
res = await m.flashFirmware(pd, firmware);
check('flash @9600: COMSET to 115200, transfer, restore 9600', same(pd.received, firmware) && pd.bauds.includes(115200) && pd.readerBaud === 9600 && res.baud === 9600, `${pd.bauds} reader=${pd.readerBaud}`);
check('flash: reader-requested fragment size used', pd.fragments[0] === 1024 && pd.fragments.slice(1, -1).every(s => s === 512), pd.fragments.join(','));

pd = new FakeReader({ finishAfter: 'finishing' });
res = await m.flashFirmware(pd, firmware);
check('flash: FINISHING then REBOOTING handled', same(pd.received, firmware) && /rebooting/i.test(res.message));

pd = new FakeReader({ finishAfter: 'silent' });
res = await m.flashFirmware(pd, firmware);
check('flash: silent reboot after full file = success', same(pd.received, firmware) && /probably rebooting/.test(res.message));

pd = new FakeReader({ nakAt: 2 });
let nakErr = null; try { await m.flashFirmware(pd, firmware); } catch (e) { nakErr = e; }
check('flash: NAK mid-transfer fails clearly', nakErr instanceof m.OsdpError && /NAK/.test(nakErr.message));

pd = new FakeReader({ address: 9 });
let noReader = null; try { await m.flashFirmware(pd, firmware); } catch (e) { noReader = e; }
check('flash: no reader -> clear error', noReader instanceof m.OsdpError && /No reader answered/.test(noReader.message));

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
