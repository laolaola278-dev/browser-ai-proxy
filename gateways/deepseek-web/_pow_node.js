const MASK = 0xffffffff;
const RC = [
  0x00000000,0x00000001, 0x00000000,0x00008082, 0x80000000,0x0000808A,
  0x80000000,0x80008000, 0x00000000,0x0000808B, 0x00000000,0x80000001,
  0x80000000,0x80008081, 0x80000000,0x00008009, 0x00000000,0x0000008A,
  0x00000000,0x00000088, 0x00000000,0x80008009, 0x00000000,0x8000000A,
  0x00000000,0x8000808B, 0x80000000,0x0000008B, 0x80000000,0x00008089,
  0x80000000,0x00008003, 0x80000000,0x00008002, 0x80000000,0x00000080,
  0x00000000,0x0000800A, 0x80000000,0x8000000A, 0x80000000,0x80008081,
  0x80000000,0x00008080, 0x00000000,0x80000001, 0x80000000,0x80008008,
];
const V = [10,7,11,17,18,3,5,16,8,21,24,4,15,23,19,13,12,2,20,14,22,9,6,1];
const W = [1,3,6,10,15,21,28,36,45,55,2,14,27,41,56,8,25,43,62,18,39,61,20,44];

function shl(x, k) { return (x << (k & 31)) >>> 0; }
function shru(x, k) { return (x >>> 0) >>> (k & 31); }
function not32(x) { return (~x) >>> 0; }

class Sponge {
  constructor() {
    this.rate = 136;
    this.outlen = 32;
    this.state = new Uint32Array(50);
    this.queue = new Uint8Array(this.rate);
    this.offset = 0;
  }
  absorbBlock(q, state) {
    for (let r = 0; r < q.length; r += 8) {
      const n = r / 4;
      const w0 = ((q[r+7] << 24) | (q[r+6] << 16) | (q[r+5] << 8) | q[r+4]) >>> 0;
      const w1 = ((q[r+3] << 24) | (q[r+2] << 16) | (q[r+1] << 8) | q[r]) >>> 0;
      state[n] = (state[n] ^ w0) >>> 0;
      state[n+1] = (state[n+1] ^ w1) >>> 0;
    }
  }
  permute(A) {
    const C = new Uint32Array(10), D = new Uint32Array(10), Wv = new Uint32Array(2);
    for (let i = 1; i < 24; i++) {
      for (let t = 0; t < 5; t++) {
        C[2*t] = (A[2*t] ^ A[2*(t+5)] ^ A[2*(t+10)] ^ A[2*(t+15)] ^ A[2*(t+20)]) >>> 0;
        C[2*t+1] = (A[2*t+1] ^ A[2*(t+5)+1] ^ A[2*(t+10)+1] ^ A[2*(t+15)+1] ^ A[2*(t+20)+1]) >>> 0;
      }
      for (let t = 0; t < 5; t++) {
        const src = (t+1)%5, o = C[2*src], f = C[2*src+1];
        Wv[0] = (shl(o,1) | shru(f,31)) >>> 0;
        Wv[1] = (shl(f,1) | shru(o,31)) >>> 0;
        const prev = (t+4)%5;
        D[2*t] = (C[2*prev] ^ Wv[0]) >>> 0;
        D[2*t+1] = (C[2*prev+1] ^ Wv[1]) >>> 0;
        for (let r = 0; r < 25; r += 5) {
          const idx = 2*(r+t);
          A[idx] = (A[idx] ^ D[2*t]) >>> 0;
          A[idx+1] = (A[idx+1] ^ D[2*t+1]) >>> 0;
        }
      }
      Wv[0] = A[2]; Wv[1] = A[3];
      for (let j = 0; j < 24; j++) {
        const t = V[j], a = W[j];
        const c0 = A[2*t], c1 = A[2*t+1];
        const o = Wv[0], f = Wv[1], u = 32-a;
        const s = a < 32 ? 0 : 1;
        Wv[s] = (shl(o,a) | shru(f,u)) >>> 0;
        Wv[(s+1)%2] = (shl(f,a) | shru(o,u)) >>> 0;
        A[2*t] = Wv[0]; A[2*t+1] = Wv[1];
        Wv[0] = c0; Wv[1] = c1;
      }
      for (let y = 0; y < 25; y += 5) {
        for (let x = 0; x < 5; x++) { C[2*x] = A[2*(y+x)]; C[2*x+1] = A[2*(y+x)+1]; }
        for (let x = 0; x < 5; x++) {
          const i = 2*(y+x), o = 2*((x+1)%5), f = 2*((x+2)%5);
          A[i] = (A[i] ^ (not32(C[o]) & C[f])) >>> 0;
          A[i+1] = (A[i+1] ^ (not32(C[o+1]) & C[f+1])) >>> 0;
        }
      }
      A[0] = (A[0] ^ RC[2*i]) >>> 0;
      A[1] = (A[1] ^ RC[2*i+1]) >>> 0;
    }
  }
  absorb(bytes) {
    for (const b of bytes) {
      this.queue[this.offset++] = b;
      if (this.offset >= this.rate) { this.absorbBlock(this.queue, this.state); this.permute(this.state); this.offset = 0; }
    }
    return this;
  }
  copy() {
    const o = new Sponge();
    o.state.set(this.state); o.queue.set(this.queue); o.offset = this.offset;
    return o;
  }
  squeeze(padding = 6) {
    const q = this.queue.slice();
    q[this.offset] |= padding;
    q[this.rate-1] |= 0x80;
    const st = this.state.slice();
    this.absorbBlock(q, st);
    const out = new Uint8Array(this.outlen);
    for (let t = 0; t < this.outlen; t += this.rate) {
      this.permute(st);
      for (let r = 0; r < Math.min(this.rate, this.outlen-t); r += 8) {
        const n = r/4, w1 = st[n+1], w0 = st[n];
        out[t+r] = w1 & 255; out[t+r+1] = (w1>>>8)&255; out[t+r+2] = (w1>>>16)&255; out[t+r+3] = (w1>>>24)&255;
        out[t+r+4] = w0 & 255; out[t+r+5] = (w0>>>8)&255; out[t+r+6] = (w0>>>16)&255; out[t+r+7] = (w0>>>24)&255;
      }
    }
    return Buffer.from(out).toString("hex");
  }
}

function digest(data) { return new Sponge().absorb(Buffer.from(data, "utf8")).squeeze(6); }

const args = process.argv.slice(2);
if (args[0] === "selftest") {
  const expected = "508526b4c1f8138ca3221fd99448aa2d00ba5ec37e603a1d2dc389e56279b281";
  const got = digest("salt_123_0");
  console.log("NODE_SELFTEST=" + (got === expected ? "OK" : "FAIL"));
  console.log("NODE_DIGEST=" + got);
  process.exit(got === expected ? 0 : 1);
}
if (args[0] === "bench") {
  const prefix = args[1];
  const n = parseInt(args[2] || "2000", 10);
  const base = new Sponge().absorb(Buffer.from(prefix, "utf8"));
  const t0 = Date.now();
  for (let i = 0; i < n; i++) base.copy().absorb(Buffer.from(String(i))).squeeze(6);
  const ms = Date.now() - t0;
  console.log("NODE_BENCH_ITERS=" + n + " MS=" + ms + " PER_ITER_US=" + (ms/n*1000).toFixed(1));
  process.exit(0);
}
if (args[0] === "solve") {
  const prefix = args[1], challenge = args[2], start = parseInt(args[3] || "0", 10), count = parseInt(args[4] || "100000", 10);
  const base = new Sponge().absorb(Buffer.from(prefix, "utf8"));
  for (let i = start; i < start + count; i++) {
    if (base.copy().absorb(Buffer.from(String(i))).squeeze(6) === challenge) {
      console.log("ANSWER=" + i);
      process.exit(0);
    }
  }
  console.log("ANSWER=none");
  process.exit(1);
}
console.log("NODE_USAGE=selftest|bench|solve");
process.exit(2);
