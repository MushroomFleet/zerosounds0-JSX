# Zero Sounds 0 JSX (Sega Genesis Style SFX synthesis) — Grounding

**Purpose.** This document is the single source of truth for ZeroSounds0: what the 1988 Sega Mega Drive / Genesis sound hardware did, how the ZeroByes *position-is-seed* method maps onto it, and the exact algorithm that `ZeroSounds0.py` (reference) and `ZeroSounds0.jsx` (browser) both implement. Anything in the code that disagrees with this document is a bug.

**Relationship to ZeroSounds-JSX (v1).** v1 modelled SNES-style audio with Web Audio nodes: oscillator → filter → bit-crusher → gain envelope. ZeroSounds0 goes one hardware generation back in character: frequency modulation instead of filtered waveforms, a square-wave PSG instead of echo/filter polish, stepped 60 Hz driver updates instead of smooth ramps. The hash core, the seed hierarchy and the sound anchor table are carried over unchanged.

---

## 1. The target hardware

The Mega Drive launched in Japan in October 1988 (Genesis in North America, 1989). Its sound comes from three sources mixed together in analog.

### 1.1 YM2612 (OPN2) — FM synthesis

| Property | Value | How ZeroSounds0 uses it |
|---|---|---|
| Channels | 6, each with 4 sine operators | One channel per sound effect |
| Routing | 8 fixed "algorithms" wiring the 4 operators as modulators and carriers | Derived per sound (slot 0) |
| Feedback | Operator 1 can modulate itself, 8 levels | Derived (slot 1); high values give noise-like timbres |
| Clock / sample rate | 7,670,453 Hz (NTSC) ÷ 144 = **53,267 Hz** | Native render rate |
| Phase | 20-bit accumulator, top 10 bits index a sine table | Same |
| Envelope | 10-bit attenuation, 0.09375 dB per step (≈96 dB range); four-stage rate envelope (attack, decay 1 to a sustain level, decay 2, release) | Same structure, with rates expressed in real units |
| Total Level (TL) | 7 bits, 0.75 dB per step (= 8 envelope steps) | Same |
| Sustain Level (SL) | 4 bits, 3 dB per step (= 32 envelope steps) | Same |
| Multiplier (MUL) | ×0.5, ×1 … ×15 per operator | Same |
| Output | Operator output 14-bit; channel sum truncated to a **9-bit DAC** | 13-bit operators, 9-bit channel output |
| DAC quirk | Crossover non-linearity around zero, known as the "ladder effect" | Optional, approximated |
| Channel 6 | Can be switched to raw 8-bit PCM (drums, voices) | Modelled as an 8-bit noise burst |

Why FM matters for the sound: a modulator's *level* sets how bright the carrier is, and the modulator has its own envelope. A modulator that decays quickly gives the characteristic "twang" — bright at the onset, mellowing in milliseconds — that defines Genesis basses, bells, lasers and zaps. Integer multipliers give harmonic tones; odd or high multipliers with strong feedback give metallic clangs and growls.

### 1.2 SN76489-compatible PSG

Integrated into the video chip. Three square-wave channels and one noise channel.

- Master clock 3,579,545 Hz. Tone frequency = clock ÷ (32 × N), N a 10-bit period register.
- 4-bit volume per channel, 2 dB per step, 15 = silent.
- Noise from a shift register (16-bit in Sega's variant) clocked at clock ÷ 512, ÷ 1024 or ÷ 2048 (≈6991, 3496, 1748 Hz), in "white" or "periodic" mode. Periodic mode is a 1-in-16 pulse train that sounds like a buzzy tone.

Games used it for coin blips, hi-hats, and to double FM melodies.

### 1.3 Sound drivers and the output stage

- Sound was sequenced by driver code that updated chip registers **once per video frame (60 Hz NTSC)**. Pitch slides, vibrato, arpeggios and PSG volume envelopes therefore move in 16.7 ms steps. This stepping is a large part of the aesthetic.
- There is no hardware echo. Drivers faked echo by replaying a part on a spare channel, later and quieter.
- The Model 1 console's analog output is commonly modelled as a first-order low-pass near **3.39 kHz**, which is why the machine sounds warm and slightly muffled rather than crisp.

### 1.4 What "Genesis-style" means here

ZeroSounds0 is a **style model, not a cycle-accurate emulator**. It keeps the things you can hear (FM operator structure, algorithms, feedback, envelope resolution, 9-bit output, PSG squares and noise, 60 Hz stepping, output filter) and simplifies the things you can't easily hear or that would break ZeroByes rules (exact rate tables and key scaling, SSG-EG, the LFO, channel-3 special mode, the LFSR).

---

## 2. ZeroByes method applied to FM

*Position is seed; zero bytes store infinity.* Nothing about a sound is stored. Each value is a pure function of coordinates.

### 2.1 The five laws

| Law | Meaning in ZeroSounds0 |
|---|---|
| **O(1) access** | Any patch field is `hash(slot, soundSeed)`. Any arpeggio note is a function of its index. Any noise sample is `hash(n, seed)`. Nothing needs earlier values. |
| **Parallelism** | Every slot and every operator hashes from its own sub-seed; changing how one is consumed never shifts another. |
| **Coherence** | Sounds in a category share a category seed and category bounds, so they stay in character across world seeds. |
| **Hierarchy** | `worldSeed → categorySeed → soundSeed → slot[k] → operatorSeed[j] → sample[n]` |
| **Determinism** | Same `(soundName, worldSeed)` → identical patch → identical PCM samples, on every machine and in both languages. |

### 2.2 Hash core (unchanged from v1)

```
zbHash(key:number, seed:u32) -> u32
  h = seed ^ 0x9E3779B9
  h ^= key;  h *= 0xCC9E2D51;  h = rotl(h, 15);  h *= 0x1B873593
  h ^= h>>>16;  h *= 0x85EBCA6B;  h ^= h>>>13;  h *= 0xC2B2AE35;  h ^= h>>>16
zbFloat(h)        = (h & 0xFFFFFF) / 2^24          in [0,1)
zbRange(h, a, b)  = a + zbFloat(h) * (b - a)
zbInt(h, a, b)    = a + h mod (b - a + 1)          inclusive
```

All multiplications are 32-bit wrapping (`Math.imul` in JS, `& 0xFFFFFFFF` in Python).

### 2.3 Seed hierarchy

```
catSeed   = zbHash(category.index, worldSeed)
soundSeed = zbHash(sound.styleIndex, catSeed)
slot k    = zbHash(k, soundSeed)                    k = 0..42, sparse
opSeed j  = zbHash(100 + j, soundSeed)              j = 0..3
op slot k = zbHash(k, opSeed j)                     k = 0..7
note i    = zbHash(i, slot 13)
PSG noise bit n = zbHash(n, soundSeed ^ 0x5E5E5E5E) & 1
DAC sample n    = zbHash(n, soundSeed ^ 0xDAC0DAC0) & 0xFF
```

Unnamed patches (`zbRandomize(n, worldSeed)`): `vSeed = zbHash(n, worldSeed)`, category from slot 900, style from slot 901, then the same builder.

### 2.4 Determinism rules for the renderer

Hash-derived parameters are not enough; the *audio* must match too. Three rules make that possible:

1. **No transcendental functions at run time.** `sin`, `pow` and `exp` are not guaranteed bit-identical across math libraries. The sine table is built from an arithmetic-only Horner polynomial, the gain table by repeated multiplication with a literal constant, and both are quantised to integers.
2. **Per-sample work uses only integer table lookups, shifts, and IEEE-754 `+ − × ÷` and `floor`**, which are exactly specified.
3. **No sequential pseudo-random state.** The hardware LFSR is replaced by hash-of-position noise.

Every expression is evaluated in the same order in both implementations. The result is checked, not assumed: see §5.

---

## 3. Patch derivation

### 3.1 Anchors

A sound is a 3-field anchor: `{category, style, styleIndex}`. Styles: `0` tonal, `1` noisy, `2` arpeggio. The anchor table is identical to v1 (34 entries; v1's documentation says 33, but its table has 34).

Categories supply bounds:

| Category | Freq Hz | Duration s | Pitch sweep (end ÷ start) | Carrier attack max |
|---|---|---|---|---|
| movement | 80–400 | 0.06–0.25 | 1.3–3.5 (rising) | 30 ms |
| pickup | 500–1800 | 0.12–0.5 | 0.9–1.6 | 30 ms |
| combat | 100–1200 | 0.06–0.5 | 0.15–0.7 (falling "pew") | 8 ms |
| explosion | 40–300 | 0.15–0.9 | 0.2–0.6 | 30 ms |
| ui | 300–1000 | 0.03–0.2 | 0.85–1.4 | 8 ms |
| power | 200–1200 | 0.3–1.0 | 1.0–2.0 | 30 ms |
| damage | 100–600 | 0.08–0.8 | 0.3–0.8 | 30 ms |
| environment | 80–600 | 0.06–0.5 | 0.5–2.5 | 30 ms |

### 3.2 Global slots

| Slot | Field | Rule |
|---|---|---|
| 0 | `algorithm` | Picked from a style pool. Tonal `[4,2,3,5,6,7,4,0]`, noisy `[0,1,2,3,0,3]` (deep modulator stacks), arpeggio `[4,5,6,7,4,5]` (multi-carrier, bell/organ-like) |
| 1 | `feedback` | Noisy 5–7, otherwise 0–4 |
| 2 | `baseFreq` | Category range |
| 3 | `freqSweep` | Noisy 0.1–0.5; arpeggio fixed at 1; tonal uses the category range |
| 4 | `duration` | Category range (replaced by note count × speed for arpeggios) |
| 5–7 | `vibrato` | Tonal only, 35% chance: depth 1–6%, rate 5–14 Hz |
| 10–13 | `arpeggio` | 2–6 notes, 2–6 ticks per note, stride 1–3 scale degrees, per-note jitter 0–1 |
| 20–25 | `psg` | Chance 45/80/60% by style; tone (pitch ×1, ×2 or ×4 of FM) or noise/periodic; start attenuation 2–6; one 2 dB step every 1–6 ticks |
| 30–33 | `dac` | Explosions 60%, other noisy sounds 30%: hold rate 3–11 kHz, linear decay over 40–100% of the duration, level 0.4–0.9 |
| 40–42 | `echo` | 60% for environment/power/pickup, else 20%: delay 3–12 ticks, gain ½ or ¼ |

`gateTicks` (key-on length in driver ticks) = `ceil(duration × 60)`, or notes × speed for arpeggios.

**Arpeggio notes** use a just-intonation major scale `[1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]` — rational ratios, so no `pow`. Note *i* sits at degree `d = i × stride + jitter(i)` with ratio `JI[d mod 7] × 2^floor(d/7)`. Damage sounds descend (`2 × base ÷ ratio`), everything else ascends. Each note depends only on its own index.

### 3.3 Operator slots (per operator *j*)

Which operators are carriers follows from the algorithm:

| Alg | Routing | Carriers |
|---|---|---|
| 0 | 1→2→3→4 | 4 |
| 1 | (1+2)→3→4 | 4 |
| 2 | (1+(2→3))→4 | 4 |
| 3 | ((1→2)+3)→4 | 4 |
| 4 | (1→2)+(3→4) | 2, 4 |
| 5 | 1→(2+3+4) | 2, 3, 4 |
| 6 | (1→2)+3+4 | 2, 3, 4 |
| 7 | 1+2+3+4 | all |

| Slot | Field | Carrier | Modulator |
|---|---|---|---|
| 0 | `mul` | op 4 always ×1; others from `[1,2,1,3,2,4,0.5,1]` | tonal `[1,2,3,1,4,2,0.5,5]`, noisy `[0.5,3,7,11,13,15,1,9]`, arpeggio `[1,2,3,4,2,1,7,5]` |
| 1 | `detune` | −3…+3 | same |
| 2 | `tl` | 0–10, plus 0/8/13/16 for 1/2/3/4 carriers | tonal 22–48, noisy 4–26, arpeggio 26–50 |
| 3 | `ar` (seconds) | 1 ms … category max | 1–10 ms |
| 4 | `d1r` (steps/s) | 300–4000 | 500–9000 |
| 5 | `sl` (steps) | 0–7 × 32 | 2–13 × 32 |
| 6 | `d2r` (steps/s) | 0–1500 | 0–3000 |
| 7 | `rr` (steps/s) | 5200–16000 | same |

Modulator TL is the main timbre control: low attenuation (noisy style) means a large modulation index and a harsh spectrum; fast modulator `d1r` produces the pluck.

---

## 4. Rendering algorithm

Output: signed 16-bit mono PCM at 53,267 Hz. Length = gate + 0.25 s release tail + echo delay.

### 4.1 Tables (built once)

- `SIN[1024]`: `floor(sin((i + 0.5)·2π/1024) × 4095 + 0.5)`, with `sin` evaluated on the first quadrant by a 15th-order Horner polynomial and mirrored.
- `AMP[1024]`: `AMP[i] = floor(4095 × K^i + 0.5)`, `K = 0.9892646717677409` (one 0.09375 dB step), built by repeated multiplication.
- `PSG_VOL[16]`: `[255,203,161,128,102,81,64,51,40,32,26,20,16,13,10,0]` (2 dB steps, literal).

A fingerprint of both generated tables (`0x3B054F67`) is checked by the verifier.

### 4.2 Driver tick (whenever `floor(i × 60 / SR)` changes)

1. **Pitch.** Arpeggio: note index = `min(floor(tick / speed), count − 1)`; a new note re-keys all four envelopes. Otherwise linear sweep: `f = f0 + (f1 − f0) × min(tick, gate) / gate`.
2. **Vibrato.** Triangle LFO evaluated from the tick number: `f ×= 1 + depth × tri(tick × rate / 60)`.
3. **Phase increments.** `inc[j] = floor(f × mul[j] × 2^20 / SR) + detune[j] × 4`.
4. **Key-off** at `tick ≥ gateTicks`: all operators enter release.
5. **PSG.** Attenuation = start + `floor(noteTick / stepTicks)`, plus 4 steps per tick after key-off, clamped to 15. Tone period `N = round(clock / (32 × f × toneMul))` clamped to 9…1023 — pitch is quantised to the real register.
6. **DAC level.** `level × (1 − tick / decayTicks)`, zero afterwards.

### 4.3 Operator (every sample)

```
envelope (att in 0..1023, starts at 1023):
  attack : att -= att * (6.93 / (ar * SR));  when att < 1 -> att = 0, go to decay 1
  decay 1: att += d1r / SR;                  when att >= sl -> att = sl, go to decay 2
  decay 2: att += d2r / SR   (clamp 1023)
  release: att += rr  / SR   (clamp 1023)
total  = min(1023, floor(att) + tl * 8)
phase  = (phase + inc) & 0xFFFFF
output = (SIN[((phase >> 10) + mod) & 1023] * AMP[total]) >> 12        // ±4095
```

Attenuation that is linear in dB is exponential in amplitude, so there is no `exp` anywhere. A modulator's output is added directly to the 10-bit phase index, so full scale is ±4 cycles (8π), matching the chip. Operator 1's feedback input is `(prev1 + prev2) >> (9 − feedback)`: the average of its last two outputs, reaching 4π at level 7.

The channel output is the sum of carriers clamped to ±4095, shifted right 4 to **9 bits**. With the ladder option, non-zero values are pushed 3 steps away from zero, an approximation of the crossover distortion.

### 4.4 PSG and DAC (every sample)

- **Tone:** phase accumulator at the quantised frequency; +1 for the first half-cycle, −1 for the second.
- **Noise:** index `k = floor(i × noiseRate / SR)`; value `±1` from `zbHash(k, noiseSeed) & 1`. Periodic mode: `+1` when `k mod 16 = 0`, else `−1/15`.
- **DAC burst:** index `k = floor(i × rate / SR)`; value `((zbHash(k, dacSeed) & 0xFF) − 128) / 128`, sample-and-held, scaled by the tick-stepped level.

### 4.5 Mix and output stage

```
s = fm9/256 * 0.7  +  psg * PSG_VOL/255 * 0.2  +  dac * level * 0.45
echo    : buf[i] += buf[i - delay] * gain          (single tap, processed back to front)
low-pass: w = 2π·3390/SR;  a = w/(1+w);  y += a * (s - y)        (optional)
pcm     = clamp(floor(y * 1.25 * 32767 + 0.5), -32768, 32767)
```

---

## 5. Verification

- **Fingerprint.** `fp = zbHash(length, 0x5E6A1988)`, then `fp = zbHash(sample & 0xFFFF, fp)` over every sample. Equal fingerprints mean equal audio.
- **Self-consistency.** `zbVerifyAll(worldSeed)` derives and renders every sound twice and compares patches and fingerprints, and checks the table fingerprint.
- **Cross-language conformance.** Both files embed the same golden fingerprints for world seed `0x1337BEEF` (default options). At that seed the verifier also checks each sound against the golden table. During development the two engines were compared on all 34 named sounds at two seeds, with the output options on and off, plus 40 unnamed patches: every patch and every fingerprint matched.

---

## 6. Implementing it in Python

`ZeroSounds0.py` is the reference and needs only the standard library.

| Concern | Approach |
|---|---|
| 32-bit hash | Python integers are unbounded, so every multiply and rotate is masked with `& 0xFFFFFFFF` to reproduce `Math.imul` and `>>> 0` |
| Signed shifts | Python's `>>` and `&` on negative integers behave as two's complement arithmetic, the same as JavaScript's `>>` and `&` on int32 — the operator and feedback math ports directly |
| Rounding | `math.floor(x + 0.5)` everywhere; Python's `round()` uses banker's rounding and would diverge from JS |
| Integer division | `//` where JS uses `Math.floor(a / b)` on non-negative integers |
| Expression order | Float expressions are written in the same left-to-right order as the JS so intermediate rounding is identical |
| Output | `wave` + `struct` write 16-bit mono WAV at 53,267 Hz |
| Speed | A pure-Python per-sample loop renders roughly 5–10 sounds per second, which is fine for offline asset baking. The algorithm is integer-table based and ports directly to NumPy, C or a shader if speed matters |

Module layout: hash core → constants and tables → taxonomy → `zb_build_patch` / `zb_derive_params` / `zb_randomize` → `zb_render` → `zb_fingerprint` / `zb_write_wav` → `zb_verify_all` → CLI.

The browser engine uses the same renderer to fill an `AudioBuffer` created at 53,267 Hz (the browser resamples on playback), rather than a Web Audio node graph. Operator feedback needs sample-accurate recursion that node graphs cannot express, and pre-rendering is what makes the audio fingerprintable.

---

## 7. Known simplifications

- Envelope rates are in real units and ignore the chip's 0–31 rate tables and key scaling.
- Detune is a small fixed phase-increment offset, not the chip's key-code-dependent table.
- No SSG-EG, hardware LFO (AM/PM sensitivity), channel-3 special mode, or stereo panning.
- One FM channel per sound; real drivers sometimes layered several.
- The ladder effect and the output filter are approximations of analog behaviour.
- PAL timing (50 Hz ticks, slightly different clocks) is not modelled.

## 8. Extension points

The v1 extensions still apply because they only change the seed: `zbHash(player.depth, baseSeed)` for depth-reactive palettes, `zbHash(hour, zbHash('footstep', worldSeed))` for Zero-Temporal drift, `zbHash(environmentId, …)` for Zero-Quadratic environment pairing. New sounds are one anchor line; new categories are one bounds row.
