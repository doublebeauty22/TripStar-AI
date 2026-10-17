// Test-only runtime guard and observations. The signing functions stay real.
const Module = require('node:module');
const crypto = require('node:crypto');
if (!process.permission || process.permission.has('net') ||
    process.permission.has('child') || process.permission.has('worker') ||
    process.permission.has('ffi')) throw new Error('offline Node permissions required');
const load = Module._load;
Module._load = function(name, ...args) {
    if (/^(?:node:)?(?:net|https?|http2|tls|dns|dgram|undici|child_process|cluster|worker_threads)(?:\/|$)/.test(name)) {
        throw new Error('offline capability denied');
    }
    return load.call(this, name, ...args);
};
for (const name of ['fetch', 'WebSocket', 'EventSource']) {
    Object.defineProperty(globalThis, name, {
        value: function() { throw new Error('offline network denied'); }, configurable: false,
    });
}
let seed = 0x12345678;
function random() {
    seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
    return (seed >>> 0) / 4294967296;
}
Math.random = random;
const RealDate = Date;
function FixedDate(...args) {
    if (new.target) return Reflect.construct(RealDate, args.length ? args : [1700000000000], new.target);
    return new RealDate(1700000000000).toString();
}
Object.setPrototypeOf(FixedDate, RealDate);
FixedDate.prototype = RealDate.prototype;
FixedDate.now = () => 1700000000000;
globalThis.Date = FixedDate;
crypto.randomBytes = size => Buffer.from(Array.from({length: size}, () => Math.floor(random() * 256)));
crypto.randomFillSync = function(buffer, offset = 0, size = buffer.byteLength - offset) {
    const view = new Uint8Array(buffer.buffer || buffer, (buffer.byteOffset || 0) + offset, size);
    for (let i = 0; i < view.length; i++) view[i] = Math.floor(random() * 256);
    return buffer;
};
Object.defineProperty(performance, 'now', {value: () => 1234.5, configurable: true});

const exec = RegExp.prototype.exec;
let expected, expectedText, cookieTarget, report, failCommon;
globalThis.__xhsProbe = {
    begin(input, shouldFail) {
        seed = 0x12345678;
        expected = input;
        expectedText = Object.entries(input).map(([key, value]) => key + '=' + value).join('; ');
        failCommon = shouldFail;
        report = {initialInputMatches: false, cookieReadsMatch: true, coreReads: 0,
            coreExtractedInputA1: false, commonMatches: false, authCleared: false};
        Object.defineProperty(Object.prototype, 'cookie', {
            configurable: true,
            set(value) {
                report.initialInputMatches = value === expectedText;
                // On the unfixed version, discard its embedded sample before any read.
                const syntheticOnly = report.initialInputMatches ? value : 'a1=' + 'c'.repeat(52);
                Object.defineProperty(this, 'cookie', {
                    value: syntheticOnly, writable: true, enumerable: true, configurable: true,
                });
                cookieTarget = this;
                delete Object.prototype.cookie;
            },
        });
    },
    read(prop, value) {
        if (prop === 'cookie') {
            report.cookieReadsMatch &&= value === expectedText;
            if (this.inCore) report.coreReads++;
        }
    },
    core() {
        this.inCore = true;
        // Install after VM initialization: replacing this native before it can
        // affect the environment checks. The real regex result is unchanged.
        RegExp.prototype.exec = function(input) {
            const result = exec.call(this, input);
            if (input === expectedText && result && Array.from(result).some(x => x === expected.a1)) {
                report.coreExtractedInputA1 = true;
            }
            return result;
        };
    },
    common(a1) {
        this.inCore = false;
        report.commonMatches = a1 === expected.a1;
        if (failCommon) throw new Error('offline synthetic signing failure');
    },
    finish() {
        RegExp.prototype.exec = exec;
        delete Object.prototype.cookie;
        report.authCleared = !!cookieTarget && cookieTarget.cookie === '';
        process.stderr.write('XHS_SIGN_PROBE:' + JSON.stringify(report) + '\n');
        expected = expectedText = cookieTarget = undefined;
        this.inCore = false;
    },
};
