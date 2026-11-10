// A JavaScript implementation of Python's random.Random (MT19937) and
// random.sample, so the ported bots make the same Monte-Carlo draws as the
// Python model when given the same seed and deck. This is what lets the
// JavaScript engine be verified hand-for-hand against the real Python code.

const N = 624;
const M = 397;
const MATRIX_A = 0x9908b0df;
const UPPER_MASK = 0x80000000;
const LOWER_MASK = 0x7fffffff;

export class PythonRandom {
  constructor(seed = 0) {
    this.mt = new Uint32Array(N);
    this.index = N;
    this.seed(seed);
  }

  seed(value) {
    const s = Number(value) >>> 0;
    // Python's random.seed(int) uses init_by_array with the absolute value
    // split into 32-bit words. For a non-negative 32-bit seed that is one word.
    const key = [];
    let n = s;
    if (n === 0) {
      key.push(0);
    } else {
      while (n > 0) {
        key.push(n >>> 0);
        n = Math.floor(n / 4294967296);
      }
    }
    this.init_by_array(key);
  }

  init_genrand(s) {
    this.mt[0] = s >>> 0;
    for (let i = 1; i < N; i += 1) {
      this.mt[i] = (Math.imul(this.mt[i - 1] ^ (this.mt[i - 1] >>> 30), 1812433253) + i) >>> 0;
    }
    this.index = N;
  }

  init_by_array(initKey) {
    this.init_genrand(19650218);
    const keyLength = initKey.length;
    let i = 1;
    let j = 0;
    let k = Math.max(N, keyLength);
    for (; k > 0; k -= 1) {
      this.mt[i] =
        ((this.mt[i] ^ Math.imul(this.mt[i - 1] ^ (this.mt[i - 1] >>> 30), 1664525)) +
          initKey[j] +
          j) >>>
        0;
      i += 1;
      j += 1;
      if (i >= N) {
        this.mt[0] = this.mt[N - 1];
        i = 1;
      }
      if (j >= keyLength) j = 0;
    }
    for (k = N - 1; k > 0; k -= 1) {
      this.mt[i] =
        ((this.mt[i] ^ Math.imul(this.mt[i - 1] ^ (this.mt[i - 1] >>> 30), 1566083941)) - i) >>>
        0;
      i += 1;
      if (i >= N) {
        this.mt[0] = this.mt[N - 1];
        i = 1;
      }
    }
    this.mt[0] = 0x80000000;
    this.index = N;
  }

  genrandUint32() {
    if (this.index >= N) {
      for (let i = 0; i < N; i += 1) {
        const y =
          (this.mt[i] & UPPER_MASK) | (this.mt[(i + 1) % N] & LOWER_MASK);
        this.mt[i] = this.mt[(i + M) % N] ^ (y >>> 1);
        if (y & 1) this.mt[i] ^= MATRIX_A;
      }
      this.index = 0;
    }
    let y = this.mt[this.index];
    this.index += 1;
    y ^= y >>> 11;
    y = (y ^ ((y << 7) & 0x9d2c5680)) >>> 0;
    y = (y ^ ((y << 15) & 0xefc60000)) >>> 0;
    y = (y ^ (y >>> 18)) >>> 0;
    return y >>> 0;
  }

  getrandbits(k) {
    if (k <= 0) return 0;
    if (k <= 32) return this.genrandUint32() >>> (32 - k);
    const words = Math.ceil(k / 32);
    let result = 0n;
    for (let i = 0; i < words; i += 1) {
      result = (result << 32n) | BigInt(this.genrandUint32());
    }
    return Number(result & ((1n << BigInt(k)) - 1n));
  }

  _randbelow(n) {
    if (n <= 0) return 0;
    const k = Math.floor(Math.log2(n)) + 1; // bit_length
    let r = this.getrandbits(k);
    while (r >= n) r = this.getrandbits(k);
    return r;
  }

  // Python's random.shuffle for a list.
  shuffle(x) {
    for (let i = x.length - 1; i >= 1; i -= 1) {
      const j = this._randbelow(i + 1);
      const tmp = x[i];
      x[i] = x[j];
      x[j] = tmp;
    }
    return x;
  }

  // Python's random.sample for a sequence population.
  sample(population, k) {
    const n = population.length;
    const result = new Array(k);
    let setsize = 21;
    if (k > 5) {
      setsize += Math.pow(4, Math.ceil(Math.log(k * 3) / Math.log(4)));
    }
    if (n <= setsize) {
      const pool = population.slice();
      for (let i = 0; i < k; i += 1) {
        const j = this._randbelow(n - i);
        result[i] = pool[j];
        pool[j] = pool[n - i - 1];
      }
    } else {
      const selected = new Set();
      for (let i = 0; i < k; i += 1) {
        let j = this._randbelow(n);
        while (selected.has(j)) j = this._randbelow(n);
        selected.add(j);
        result[i] = population[j];
      }
    }
    return result;
  }
}
