#!/usr/bin/env python3
"""
ZeroSounds0.py — ZeroByes-aligned Sega Genesis-style (1988) SFX synthesis.

Reference implementation of the engine described in ZeroSounds0-grounding.md.
Pure standard library (no numpy). Mirrors ZeroSounds0.jsx operation-for-
operation, so both produce sample-identical PCM and equal fingerprints.

Chip model: YM2612-style 4-operator FM + SN76489-style PSG + 8-bit DAC burst.
Every patch is derived O(1) from (soundName, worldSeed). No random module,
no clock, no math.sin/pow/exp anywhere in synthesis, no LFSR state.

Usage:
  python ZeroSounds0.py list
  python ZeroSounds0.py params explosion --seed 0xDEADBEEF
  python ZeroSounds0.py render jump -o jump.wav
  python ZeroSounds0.py render-all --outdir sfx/ --seed 0x1337BEEF
  python ZeroSounds0.py randomize 42 -o patch42.wav
  python ZeroSounds0.py fingerprints
  python ZeroSounds0.py verify
  python ZeroSounds0.py build-demo          # ZeroSounds0.jsx -> ZeroSounds0-demo.html
"""
import argparse
import json
import math
import os
import re
import struct
import sys
import wave

M32 = 0xFFFFFFFF

# ============================================================================
# SECTION 1: ZB HASH CORE (bit-identical to the JavaScript zbHash)
# ============================================================================
def zb_hash(key, seed=0):
    h = ((seed & M32) ^ 0x9E3779B9) & M32
    if isinstance(key, (int, float)):
        h ^= int(key) & M32
        h = (h * 0xCC9E2D51) & M32
        h = ((h << 15) | (h >> 17)) & M32
        h = (h * 0x1B873593) & M32
        h ^= h >> 16
        h = (h * 0x85EBCA6B) & M32
        h ^= h >> 13
        h = (h * 0xC2B2AE35) & M32
        h ^= h >> 16
        return h
    for ch in key:
        h ^= ord(ch)
        h = (h * 0x5BD1E995) & M32
        h ^= h >> 15
    return h


def zb_float(h):
    return (h & 0xFFFFFF) / 0x1000000


def zb_range(h, lo, hi):
    return lo + zb_float(h) * (hi - lo)


def zb_int(h, lo, hi):
    return lo + (h % (hi - lo + 1))


def zb_hex(v):
    return "0x%08X" % (v & M32)


# ============================================================================
# SECTION 2: CHIP CONSTANTS + LOOKUP TABLES (arithmetic-only construction)
# ============================================================================
ZB_SR = 53267             # YM2612 native rate: 7670453 Hz / 144
ZB_PSG_CLOCK = 3579545    # SN76489 master clock (NTSC)
ZB_PI = 3.141592653589793
ZB_ENV_STEP = 0.9892646717677409  # amplitude ratio of one 0.09375 dB step
ZB_PSG_VOL = [255, 203, 161, 128, 102, 81, 64, 51, 40, 32, 26, 20, 16, 13, 10, 0]
ZB_JI = [1, 9 / 8, 5 / 4, 4 / 3, 3 / 2, 5 / 3, 15 / 8]
ZB_CARRIERS = [[3], [3], [3], [3], [1, 3], [1, 2, 3], [1, 2, 3], [0, 1, 2, 3]]


def _sin_poly(x):
    x2 = x * x
    return x * (1 - x2 / 6 * (1 - x2 / 20 * (1 - x2 / 42 * (1 - x2 / 72 *
           (1 - x2 / 110 * (1 - x2 / 156 * (1 - x2 / 210)))))))


def _build_tables():
    sin_t, amp_t = [0] * 1024, [0] * 1024
    for i in range(1024):
        q, quad = i & 255, i >> 8
        x = (((255 - q) if (quad & 1) else q) + 0.5) * ZB_PI / 512
        v = math.floor(_sin_poly(x) * 4095 + 0.5)
        sin_t[i] = -v if quad >= 2 else v
    a = 4095.0
    for i in range(1024):
        amp_t[i] = math.floor(a + 0.5)
        a *= ZB_ENV_STEP
    return sin_t, amp_t


ZB_SIN, ZB_AMP = _build_tables()
ZB_TABLE_FP = 0x3B054F67


def zb_table_fingerprint():
    h = 0x7AB1E5
    for v in ZB_SIN:
        h = zb_hash(v & 0xFFFF, h)
    for v in ZB_AMP:
        h = zb_hash(v & 0xFFFF, h)
    return h


# ============================================================================
# SECTION 3: SOUND TAXONOMY (anchor data only — no patches stored)
# ============================================================================
ZB_CATEGORIES = {
    "movement":    dict(index=0, freqLow=80,  freqHigh=400,  durLow=0.06, durHigh=0.25, sweep=(1.3, 3.5),  attackMax=0.03),
    "pickup":      dict(index=1, freqLow=500, freqHigh=1800, durLow=0.12, durHigh=0.5,  sweep=(0.9, 1.6),  attackMax=0.03),
    "combat":      dict(index=2, freqLow=100, freqHigh=1200, durLow=0.06, durHigh=0.5,  sweep=(0.15, 0.7), attackMax=0.008),
    "explosion":   dict(index=3, freqLow=40,  freqHigh=300,  durLow=0.15, durHigh=0.9,  sweep=(0.2, 0.6),  attackMax=0.03),
    "ui":          dict(index=4, freqLow=300, freqHigh=1000, durLow=0.03, durHigh=0.2,  sweep=(0.85, 1.4), attackMax=0.008),
    "power":       dict(index=5, freqLow=200, freqHigh=1200, durLow=0.3,  durHigh=1.0,  sweep=(1.0, 2.0),  attackMax=0.03),
    "damage":      dict(index=6, freqLow=100, freqHigh=600,  durLow=0.08, durHigh=0.8,  sweep=(0.3, 0.8),  attackMax=0.03),
    "environment": dict(index=7, freqLow=80,  freqHigh=600,  durLow=0.06, durHigh=0.5,  sweep=(0.5, 2.5),  attackMax=0.03),
}
ZB_CATEGORY_ORDER = ["movement", "pickup", "combat", "explosion", "ui", "power", "damage", "environment"]

# name: (category, style, styleIndex)   style: 0=tonal, 1=noisy, 2=arpeggio
ZB_SOUNDS = {
    "jump": ("movement", 0, 0), "doubleJump": ("movement", 2, 1), "dash": ("movement", 1, 2), "land": ("movement", 1, 3),
    "coin": ("pickup", 2, 0), "gem": ("pickup", 2, 1), "heart": ("pickup", 2, 2), "key": ("pickup", 0, 3),
    "laser": ("combat", 0, 0), "blaster": ("combat", 1, 1), "sword": ("combat", 1, 2), "punch": ("combat", 1, 3),
    "magicSpell": ("combat", 2, 4),
    "explosion": ("explosion", 1, 0), "smallExplosion": ("explosion", 1, 1), "boom": ("explosion", 1, 2),
    "menuSelect": ("ui", 0, 0), "menuConfirm": ("ui", 2, 1), "menuCancel": ("ui", 0, 2), "pause": ("ui", 0, 3),
    "textBlip": ("ui", 0, 4),
    "powerUp": ("power", 2, 0), "levelUp": ("power", 2, 1), "oneUp": ("power", 2, 2), "heal": ("power", 2, 3),
    "hurt": ("damage", 1, 0), "death": ("damage", 2, 1), "enemyHit": ("damage", 1, 2), "warning": ("damage", 2, 3),
    "doorOpen": ("environment", 0, 0), "chest": ("environment", 2, 1), "splash": ("environment", 1, 2),
    "teleport": ("environment", 0, 3), "footstep": ("environment", 1, 4),
}

ZB_ALG_POOL = [[4, 2, 3, 5, 6, 7, 4, 0], [0, 1, 2, 3, 0, 3], [4, 5, 6, 7, 4, 5]]
ZB_MUL_CAR = [1, 2, 1, 3, 2, 4, 0.5, 1]
ZB_MUL_MOD = [[1, 2, 3, 1, 4, 2, 0.5, 5], [0.5, 3, 7, 11, 13, 15, 1, 9], [1, 2, 3, 4, 2, 1, 7, 5]]
ZB_MOD_TL = [(22, 48), (4, 26), (26, 50)]
ZB_CAR_PENALTY = [0, 0, 8, 13, 16]
ZB_PSG_CHANCE = [0.45, 0.8, 0.6]
ZB_NOISE_DIV = [512, 1024, 2048]


# ============================================================================
# SECTION 4: ZEROBYES PATCH DERIVATION (O(1))
# worldSeed -> categorySeed -> soundSeed -> slot[k] -> operator[j] -> sample[n]
# ============================================================================
def zb_build_patch(seed, cat_name, style):
    cat = ZB_CATEGORIES[cat_name]
    h = lambda k: zb_hash(k, seed)

    pool = ZB_ALG_POOL[style]
    algorithm = pool[zb_int(h(0), 0, len(pool) - 1)]
    feedback = zb_int(h(1), 5, 7) if style == 1 else zb_int(h(1), 0, 4)
    base_freq = zb_range(h(2), cat["freqLow"], cat["freqHigh"])
    sw = (0.1, 0.5) if style == 1 else cat["sweep"]
    freq_sweep = 1 if style == 2 else zb_range(h(3), sw[0], sw[1])
    duration = zb_range(h(4), cat["durLow"], cat["durHigh"])
    vibrato = None
    if style == 0 and zb_float(h(5)) < 0.35:
        vibrato = {"depth": zb_range(h(6), 0.01, 0.06), "rate": zb_range(h(7), 5, 14)}

    carriers = ZB_CARRIERS[algorithm]
    operators = []
    for j in range(4):
        op_seed = zb_hash(100 + j, seed)
        oh = lambda k, s=op_seed: zb_hash(k, s)
        is_car = j in carriers
        if is_car:
            mul = 1 if j == 3 else ZB_MUL_CAR[zb_int(oh(0), 0, 7)]
            tl = zb_int(oh(2), 0, 10) + ZB_CAR_PENALTY[len(carriers)]
        else:
            mul = ZB_MUL_MOD[style][zb_int(oh(0), 0, 7)]
            tl = zb_int(oh(2), ZB_MOD_TL[style][0], ZB_MOD_TL[style][1])
        operators.append({
            "carrier": is_car,
            "mul": mul,
            "detune": zb_int(oh(1), 0, 6) - 3,
            "tl": tl,
            "ar": zb_range(oh(3), 0.001, cat["attackMax"]) if is_car else zb_range(oh(3), 0.001, 0.01),
            "d1r": zb_range(oh(4), 300, 4000) if is_car else zb_range(oh(4), 500, 9000),
            "sl": (zb_int(oh(5), 0, 7) if is_car else zb_int(oh(5), 2, 13)) * 32,
            "d2r": zb_range(oh(6), 0, 1500) if is_car else zb_range(oh(6), 0, 3000),
            "rr": zb_range(oh(7), 5200, 16000),
        })

    arpeggio = None
    if style == 2:
        count = zb_int(h(10), 2, 6)
        speed_ticks = zb_int(h(11), 2, 6)
        stride = zb_int(h(12), 1, 3)
        down = cat_name == "damage"
        note_seed = h(13)
        notes = []
        for i in range(count):
            d = 0 if i == 0 else i * stride + zb_int(zb_hash(i, note_seed), 0, 1)
            ratio = ZB_JI[d % 7] * (1 << (d // 7))
            notes.append(base_freq * 2 / ratio if down else base_freq * ratio)
        arpeggio = {"notes": notes, "speedTicks": speed_ticks}
        duration = count * speed_ticks / 60
    gate_ticks = (len(arpeggio["notes"]) * arpeggio["speedTicks"]) if arpeggio else max(1, math.ceil(duration * 60))

    psg = None
    if zb_float(h(20)) < ZB_PSG_CHANCE[style]:
        psg = {
            "mode": ("noise" if zb_float(h(21)) < 0.8 else "periodic") if style == 1 else "tone",
            "noiseRate": ZB_PSG_CLOCK / ZB_NOISE_DIV[zb_int(h(22), 1, 2) if cat_name == "explosion" else zb_int(h(22), 0, 2)],
            "toneMul": [2, 1, 2, 4][zb_int(h(23), 0, 3)],
            "atten": zb_int(h(24), 2, 6),
            "stepTicks": zb_int(h(25), 1, 6),
        }

    dac_gate = 0.4 if cat_name == "explosion" else (0.7 if style == 1 else 2)
    dac = None
    if zb_float(h(30)) > dac_gate:
        dac = {
            "rate": zb_range(h(31), 3000, 11000),
            "decayTicks": max(2, math.floor(duration * 60 * zb_range(h(32), 0.4, 1.0))),
            "level": zb_range(h(33), 0.4, 0.9),
        }

    echo_chance = 0.6 if cat_name in ("environment", "power", "pickup") else 0.2
    echo = None
    if zb_float(h(40)) < echo_chance:
        echo = {"delayTicks": zb_int(h(41), 3, 12), "shift": zb_int(h(42), 1, 2)}

    return {
        "soundSeed": seed, "category": cat_name, "style": style,
        "algorithm": algorithm, "feedback": feedback, "baseFreq": base_freq,
        "freqSweep": freq_sweep, "duration": duration, "gateTicks": gate_ticks,
        "vibrato": vibrato, "operators": operators, "arpeggio": arpeggio,
        "psg": psg, "dac": dac, "echo": echo,
    }


def zb_derive_params(sound_name, world_seed=0x1337BEEF):
    if sound_name not in ZB_SOUNDS:
        raise KeyError("Unknown sound: %s" % sound_name)
    cat_name, style, style_index = ZB_SOUNDS[sound_name]
    cat_seed = zb_hash(ZB_CATEGORIES[cat_name]["index"], world_seed)
    sound_seed = zb_hash(style_index, cat_seed)
    return zb_build_patch(sound_seed, cat_name, style)


def zb_randomize(numeric_seed, world_seed=0x1337BEEF):
    v_seed = zb_hash(numeric_seed, world_seed)
    cat_name = ZB_CATEGORY_ORDER[zb_int(zb_hash(900, v_seed), 0, 7)]
    style = zb_int(zb_hash(901, v_seed), 0, 2)
    return zb_build_patch(v_seed, cat_name, style)


# ============================================================================
# SECTION 5: CHIP RENDERER  patch -> list of int16 samples @ 53267 Hz
# ============================================================================
def zb_render(p, ladder=True, lowpass=True):
    SR = ZB_SR
    SIN, AMP, floor = ZB_SIN, ZB_AMP, math.floor
    gate_ticks = p["gateTicks"]
    gate_samples = floor(gate_ticks * SR / 60)
    echo_samples = floor(p["echo"]["delayTicks"] * SR / 60) if p["echo"] else 0
    n = gate_samples + floor(SR / 4) + echo_samples
    buf = [0.0] * n

    ops = p["operators"]
    phase, inc, att, stage = [0] * 4, [0] * 4, [1023.0] * 4, [0] * 4
    ar_k = [min(1, 6.93 / (o["ar"] * SR)) for o in ops]
    d1 = [o["d1r"] / SR for o in ops]
    d2 = [o["d2r"] / SR for o in ops]
    rr = [o["rr"] / SR for o in ops]
    sl = [o["sl"] for o in ops]
    tl_att = [o["tl"] * 8 for o in ops]
    alg, fb = p["algorithm"], p["feedback"]
    fb_shift = 9 - fb
    arp, psg, dac, vib = p["arpeggio"], p["psg"], p["dac"], p["vibrato"]
    f0, f1 = p["baseFreq"], p["baseFreq"] * p["freqSweep"]
    n_seed = (p["soundSeed"] ^ 0x5E5E5E5E) & M32
    d_seed = (p["soundSeed"] ^ 0xDAC0DAC0) & M32
    psg_mode = psg["mode"] if psg else None

    fb1 = fb2 = 0
    last_tick, keyed_off, note_idx, last_note = -1, False, 0, -1
    psg_phase, psg_inc, psg_vol, n_idx, n_val = 0.0, 0.0, 0, -1, 0
    dac_amp, d_idx, d_val = 0.0, -1, 0.0

    def op(j, mod):
        a = att[j]
        st = stage[j]
        if st == 0:
            a -= a * ar_k[j]
            if a < 1:
                a = 0.0
                stage[j] = 1
        elif st == 1:
            a += d1[j]
            if a >= sl[j]:
                a = sl[j]
                stage[j] = 2
        elif st == 2:
            a += d2[j]
            if a > 1023:
                a = 1023.0
        else:
            a += rr[j]
            if a > 1023:
                a = 1023.0
        att[j] = a
        t = int(a) + tl_att[j]
        if t > 1023:
            t = 1023
        ph = (phase[j] + inc[j]) & 0xFFFFF
        phase[j] = ph
        return (SIN[((ph >> 10) + mod) & 1023] * AMP[t]) >> 12

    for i in range(n):
        # ── driver tick (60 Hz) ──
        tick = (i * 60) // SR
        if tick != last_tick:
            last_tick = tick
            if arp:
                note_idx = min(tick // arp["speedTicks"], len(arp["notes"]) - 1)
                freq = arp["notes"][note_idx]
                if note_idx != last_note:
                    last_note = note_idx
                    stage[0] = stage[1] = stage[2] = stage[3] = 0
            else:
                gt = tick if tick < gate_ticks else gate_ticks
                freq = f0 + (f1 - f0) * gt / gate_ticks
            if vib:
                x = tick * vib["rate"] / 60
                fr = x - floor(x)
                freq *= 1 + vib["depth"] * (4 * fr - 1 if fr < 0.5 else 3 - 4 * fr)
            for j in range(4):
                v = floor(freq * ops[j]["mul"] * 1048576 / SR) + ops[j]["detune"] * 4
                inc[j] = v if v > 0 else 0
            if not keyed_off and tick >= gate_ticks:
                keyed_off = True
                stage[0] = stage[1] = stage[2] = stage[3] = 3
            if psg:
                pt = tick - note_idx * arp["speedTicks"] if arp else tick
                a = psg["atten"] + pt // psg["stepTicks"]
                if tick >= gate_ticks:
                    a += (tick - gate_ticks + 1) * 4
                psg_vol = ZB_PSG_VOL[15 if a > 15 else a]
                if psg_mode == "tone":
                    N = floor(ZB_PSG_CLOCK / (32 * freq * psg["toneMul"]) + 0.5)
                    if N < 9:
                        N = 9
                    elif N > 1023:
                        N = 1023
                    psg_inc = ZB_PSG_CLOCK / (32 * N) / SR
            if dac:
                dac_amp = dac["level"] * (1 - tick / dac["decayTicks"]) if tick < dac["decayTicks"] else 0.0

        # ── YM2612 channel ──
        o1 = op(0, ((fb1 + fb2) >> fb_shift) if fb else 0)
        fb2 = fb1
        fb1 = o1
        if alg == 0:
            o2 = op(1, o1); o3 = op(2, o2); out = op(3, o3)
        elif alg == 1:
            o2 = op(1, 0); o3 = op(2, o1 + o2); out = op(3, o3)
        elif alg == 2:
            o2 = op(1, 0); o3 = op(2, o2); out = op(3, o1 + o3)
        elif alg == 3:
            o2 = op(1, o1); o3 = op(2, 0); out = op(3, o2 + o3)
        elif alg == 4:
            o2 = op(1, o1); o3 = op(2, 0); out = o2 + op(3, o3)
        elif alg == 5:
            o2 = op(1, o1); o3 = op(2, o1); out = o2 + o3 + op(3, o1)
        elif alg == 6:
            o2 = op(1, o1); o3 = op(2, 0); out = o2 + o3 + op(3, 0)
        else:
            o2 = op(1, 0); o3 = op(2, 0); out = o1 + o2 + o3 + op(3, 0)
        if out > 4095:
            out = 4095
        elif out < -4095:
            out = -4095
        fm = out >> 4
        if ladder:
            fm = fm + 3 if fm > 0 else (fm - 3 if fm < 0 else 0)
        s = fm / 256 * 0.7

        # ── SN76489 voice ──
        if psg:
            if psg_mode == "tone":
                psg_phase += psg_inc
                if psg_phase >= 1:
                    psg_phase -= 1
                sq = 1 if psg_phase < 0.5 else -1
            else:
                k = floor(i * psg["noiseRate"] / SR)
                if k != n_idx:
                    n_idx = k
                    if psg_mode == "noise":
                        n_val = 1 if (zb_hash(k, n_seed) & 1) else -1
                    else:
                        n_val = 1 if (k & 15) == 0 else -1 / 15
                sq = n_val
            s += sq * psg_vol / 255 * 0.2

        # ── 8-bit DAC burst ──
        if dac and dac_amp > 0:
            k = floor(i * dac["rate"] / SR)
            if k != d_idx:
                d_idx = k
                d_val = ((zb_hash(k, d_seed) & 0xFF) - 128) / 128
            s += d_val * dac_amp * 0.45
        buf[i] = s

    if p["echo"]:
        g = 0.5 if p["echo"]["shift"] == 1 else 0.25
        for i in range(n - 1, echo_samples - 1, -1):
            buf[i] += buf[i - echo_samples] * g

    pcm = [0] * n
    w = 2 * ZB_PI * 3390 / SR
    a = w / (1 + w)
    y = 0.0
    for i in range(n):
        if lowpass:
            y += a * (buf[i] - y)
        else:
            y = buf[i]
        v = floor(y * 1.25 * 32767 + 0.5)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        pcm[i] = v
    return pcm


def zb_fingerprint(pcm):
    h = zb_hash(len(pcm), 0x5E6A1988)
    for v in pcm:
        h = zb_hash(v & 0xFFFF, h)
    return h


def zb_write_wav(path, pcm, sample_rate=ZB_SR):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<%dh" % len(pcm), *pcm))


# ============================================================================
# SECTION 6: VERIFICATION
# Golden fingerprints: worldSeed 0x1337BEEF, default options. The same table
# is embedded in ZeroSounds0.jsx; both engines must reproduce it.
# ============================================================================
ZB_GOLDEN = {
    "jump": 0xCEA9CA69, "doubleJump": 0xE53C4215, "dash": 0x4610CF0C,
    "land": 0x96C8E8FB, "coin": 0x73FD5AAF, "gem": 0x120B18A0,
    "heart": 0x0DD9C8C0, "key": 0xF0141C9C, "laser": 0xC450C779,
    "blaster": 0x14567ED7, "sword": 0xD5DE5693, "punch": 0xE513B237,
    "magicSpell": 0x59157DB4, "explosion": 0x8E5253A1, "smallExplosion": 0x19E03E92,
    "boom": 0x050A8B1A, "menuSelect": 0x3138A307, "menuConfirm": 0xFD0924A9,
    "menuCancel": 0x197F0BBD, "pause": 0x0F0153F7, "textBlip": 0xCC8D4755,
    "powerUp": 0xF302B4FC, "levelUp": 0x00A934EF, "oneUp": 0xA76983C8,
    "heal": 0x356051DD, "hurt": 0x6D30E5C5, "death": 0x8A5E25A6,
    "enemyHit": 0x8BECF53E, "warning": 0x984CFC2C, "doorOpen": 0x8E3DCC18,
    "chest": 0x3076232F, "splash": 0x6E21844D, "teleport": 0xE2CD9D45,
    "footstep": 0xB4F3BB6B,
}


def zb_verify_all(world_seed=0x1337BEEF):
    results = {}
    tables_ok = zb_table_fingerprint() == ZB_TABLE_FP
    for name in ZB_SOUNDS:
        a, b = zb_derive_params(name, world_seed), zb_derive_params(name, world_seed)
        fa, fb = zb_fingerprint(zb_render(a)), zb_fingerprint(zb_render(b))
        golden = (ZB_GOLDEN.get(name) == fa) if (world_seed & M32) == 0x1337BEEF else None
        results[name] = {
            "ok": a == b and fa == fb and tables_ok and golden is not False,
            "fingerprint": zb_hex(fa), "golden": golden,
        }
    return results


# ============================================================================
# SECTION 7: DEMO BUILDER — inlines ZeroSounds0.jsx verbatim into one HTML file
# ============================================================================
DEMO_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ZeroSounds0 — ZeroByes Deterministic Genesis-Style FM Audio</title>
<style>html,body{margin:0;background:#08090c}#root:empty::after{content:'Loading ZeroSounds0…';display:block;padding:40px;color:#6b7285;font-family:monospace}</style>
<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone@7/babel.min.js"></script>
</head>
<body>
<div id="root"></div>
<!-- Generated by `python ZeroSounds0.py build-demo`. Everything below is ZeroSounds0.jsx,
     verbatim, with its import/export lines swapped for browser globals. -->
<script type="text/babel" data-presets="react">
const { useState, useRef, useCallback, useEffect, useMemo } = React;
/*__JSX__*/
// Expose the engine for console experiments: ZeroSounds0Engine.zbDeriveParams('jump', 0x1337BEEF)
window.ZeroSounds0Engine = { zbHash, zbFloat, zbRange, zbInt, zbHex, zbBuildPatch, zbDeriveParams,
  zbRandomize, zbRender, zbFingerprint, zbTableFingerprint, zbEncodeWav, zbVerifyAll, ZBEngine,
  ZB_SOUNDS, ZB_CATEGORIES, ZB_GOLDEN, ZB_SR };
ReactDOM.createRoot(document.getElementById('root')).render(<ZeroSounds0 />);
</script>
</body>
</html>
"""


def build_demo(jsx_path, out_path):
    with open(jsx_path, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r"^import React.*\n", "", src, count=1, flags=re.M)
    src = src.replace("export default function", "function")
    src = re.sub(r"\nexport \{.*?\};\n", "\n", src, flags=re.S)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(DEMO_TEMPLATE.replace("/*__JSX__*/", src))


# ============================================================================
# CLI
# ============================================================================
def _seed(s):
    return int(s, 0) & M32


def main(argv=None):
    ap = argparse.ArgumentParser(description="ZeroSounds0 — deterministic Genesis-style SFX synthesis")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp, out=False):
        sp.add_argument("--seed", type=_seed, default=0x1337BEEF, help="world seed (default 0x1337BEEF)")
        if out:
            sp.add_argument("--no-ladder", action="store_true", help="disable DAC ladder distortion")
            sp.add_argument("--no-lowpass", action="store_true", help="disable Model 1 low-pass")

    sub.add_parser("list", help="list the named sounds")
    sp = sub.add_parser("params", help="print the derived patch as JSON"); sp.add_argument("sound"); common(sp)
    sp = sub.add_parser("render", help="render one sound to WAV"); sp.add_argument("sound")
    sp.add_argument("-o", "--out"); common(sp, True)
    sp = sub.add_parser("render-all", help="render every named sound"); sp.add_argument("--outdir", default="sfx"); common(sp, True)
    sp = sub.add_parser("randomize", help="render the unnamed patch at position N"); sp.add_argument("n", type=int)
    sp.add_argument("-o", "--out"); common(sp, True)
    sp = sub.add_parser("fingerprints", help="print PCM fingerprints (compare with the JSX engine)"); common(sp)
    sp = sub.add_parser("verify", help="determinism + golden-fingerprint check"); common(sp)
    sp = sub.add_parser("build-demo", help="generate ZeroSounds0-demo.html from ZeroSounds0.jsx")
    sp.add_argument("--jsx", default="ZeroSounds0.jsx"); sp.add_argument("--out", default="ZeroSounds0-demo.html")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for name, (cat, style, _) in ZB_SOUNDS.items():
            print("%-15s %-12s %s" % (name, cat, ("tonal", "noisy", "arpeggio")[style]))
    elif a.cmd == "params":
        print(json.dumps(zb_derive_params(a.sound, a.seed), indent=2))
    elif a.cmd in ("render", "randomize"):
        if a.cmd == "render":
            patch, stem = zb_derive_params(a.sound, a.seed), a.sound
        else:
            patch, stem = zb_randomize(a.n, a.seed), "patch-%d" % a.n
        pcm = zb_render(patch, ladder=not a.no_ladder, lowpass=not a.no_lowpass)
        out = a.out or "%s_%s.wav" % (stem, zb_hex(a.seed))
        zb_write_wav(out, pcm)
        print("%s  %d samples  %.3fs  fingerprint %s" % (out, len(pcm), len(pcm) / ZB_SR, zb_hex(zb_fingerprint(pcm))))
    elif a.cmd == "render-all":
        os.makedirs(a.outdir, exist_ok=True)
        for name in ZB_SOUNDS:
            pcm = zb_render(zb_derive_params(name, a.seed), ladder=not a.no_ladder, lowpass=not a.no_lowpass)
            zb_write_wav(os.path.join(a.outdir, name + ".wav"), pcm)
            print("%-15s %s" % (name, zb_hex(zb_fingerprint(pcm))))
    elif a.cmd == "fingerprints":
        print("tables          %s" % zb_hex(zb_table_fingerprint()))
        for name in ZB_SOUNDS:
            print("%-15s %s" % (name, zb_hex(zb_fingerprint(zb_render(zb_derive_params(name, a.seed))))))
    elif a.cmd == "verify":
        res = zb_verify_all(a.seed)
        for name, r in res.items():
            g = "" if r["golden"] is None else ("  golden ✓" if r["golden"] else "  golden ✗")
            print("%-15s %s  %s%s" % (name, r["fingerprint"], "✓" if r["ok"] else "✗", g))
        ok = all(r["ok"] for r in res.values())
        print("ALL %d SOUNDS DETERMINISTIC" % len(res) if ok else "VIOLATIONS FOUND")
        return 0 if ok else 1
    elif a.cmd == "build-demo":
        build_demo(a.jsx, a.out)
        print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
