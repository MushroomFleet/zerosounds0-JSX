# ZeroSounds0-JSX

**Deterministic Sega Genesis-style (1988) FM sound-effect synthesis for games and applications.**

ZeroSounds0 is the follow-up to [ZeroSounds-JSX](https://github.com/MushroomFleet/ZeroSounds-JSX). Where v1 chased the SNES sound, ZeroSounds0 goes a hardware generation further back in character: 4-operator FM, a square-wave PSG, an 8-bit DAC crunch layer and 60 Hz driver stepping — the gritty, metallic palette of the 16-bit FM era.

It is built on the same ZeroByes methodology — *position-is-seed, zero bytes store infinity*. Every FM patch is derived O(1) from a hash of `(soundName, worldSeed)`. No `Math.random()`. No stored presets. No stored state.

New in this version: the audio itself is deterministic, not just the parameters. The React/JS engine and the Python engine render **sample-identical PCM**, proven by matching 32-bit audio fingerprints.

Repository: https://github.com/MushroomFleet/zerosounds0-JSX

---

## What's in the repo

```
zerosounds0-JSX/
├── ZeroSounds0-grounding.md   # The technique: hardware background, derivation rules, render algorithm
├── ZeroSounds0.py             # Reference implementation + CLI (pure standard library)
├── ZeroSounds0.jsx            # React component + engine (hash core, patch derivation, renderer, playback)
├── ZeroSounds0-demo.html      # Self-contained interactive demo (the JSX, inlined)
└── README.md
```

| File | Read it when you want to… |
|---|---|
| `ZeroSounds0-grounding.md` | Understand how and why it works, or port the engine to another language |
| `ZeroSounds0.py` | Bake WAV files offline, inspect patches, or verify determinism from a terminal |
| `ZeroSounds0.jsx` | Drop the sound board into a React app, or import the engine into a game |
| `ZeroSounds0-demo.html` | Hear it and explore the patch space in a browser |

---

## How it works

**1. A sound is an anchor, not a preset.** Each of the 34 named sounds is three fields: category, style (tonal / noisy / arpeggio) and an index.

**2. A hash chain turns the anchor into an FM patch.**

```
worldSeed → categorySeed → soundSeed → slot[k] → operator[j] → sample[n]
```

Each slot of the chain yields one patch field: the FM algorithm (one of 8 operator routings), operator-1 feedback, base pitch and sweep, and for each of the four operators a frequency multiplier, level and four-stage envelope. Further slots decide whether a PSG square/noise voice, a DAC noise burst or a driver-style echo is layered on. Category bounds keep explosions sounding like explosions at any seed.

**3. A chip-style renderer turns the patch into PCM** at the FM chip's native 53,267 Hz: 10-bit sine and attenuation tables, phase modulation between operators, a 9-bit channel output with optional DAC "ladder" distortion, a PSG voice with 2 dB volume steps, pitch and volume updated at 60 Hz like a period sound driver, and an optional Model 1-style low-pass.

**4. Everything is reproducible.** The renderer uses only integer tables and exactly specified arithmetic — no `sin`, `pow` or `exp` at run time, and hash-of-position noise instead of a shift register. One 32-bit integer therefore identifies not just a set of patches but the exact samples.

Full detail, including every slot and formula, is in [`ZeroSounds0-grounding.md`](ZeroSounds0-grounding.md).

### The Five ZeroByes Laws (FM edition)

| Law | Meaning here |
|---|---|
| **O(1) Access** | Any patch field, arpeggio note or noise sample is computed directly from its coordinates |
| **Parallelism** | Every slot and every operator hashes from its own sub-seed |
| **Coherence** | Sounds in a category share a seed and bounds, so they stay in character |
| **Hierarchy** | `world → category → sound → slot → operator → sample` |
| **Determinism** | Same `(soundName, worldSeed)` → same PCM fingerprint, in JavaScript and Python |

### ZeroSounds vs ZeroSounds0

| | ZeroSounds-JSX (v1) | ZeroSounds0-JSX |
|---|---|---|
| Aesthetic | SNES-style | Genesis-style |
| Synthesis | Oscillator → filter → bit-crush | 4-operator FM, 8 algorithms, feedback |
| Extra voices | Noise layer, sine bass, echo | PSG square/noise, 8-bit DAC burst, single-tap driver echo |
| Modulation | Smooth Web Audio ramps | 60 Hz stepped pitch, vibrato and PSG volume |
| Engine | Live Web Audio node graph | Pre-rendered PCM buffer at 53,267 Hz |
| Determinism | Parameters | Parameters **and** audio samples |
| Python twin | — | Yes, sample-identical |
| Hash core, hierarchy, sound names | — | Unchanged, so game code keeps its sound names |

---

## Using the demo — `ZeroSounds0-demo.html`

Open the file in any modern browser (double-click, or serve the folder with `python3 -m http.server`). There is no build step. It loads React and Babel from the unpkg CDN, so it needs an internet connection the first time.

- **Sound grid** — click any sound to play it. Each card shows its derived algorithm, feedback, pitch and which chips it uses.
- **World Seed** — drag the slider or type a hex value; the whole library re-voices together.
- **Next Patch** — steps through unnamed patches: a new complete FM voice from a counter.
- **Patch Inspector** — the rendered waveform, the audio fingerprint, the operator table, and **Export WAV**.
- **Output Stage** — toggle the DAC ladder distortion and the low-pass to hear what each contributes.
- **ZB Compliance Test** — renders every sound twice and compares fingerprints; at seed `0x1337BEEF` it also checks them against the Python reference values.

The demo page is `ZeroSounds0.jsx` inlined verbatim. After editing the component, regenerate it with `python ZeroSounds0.py build-demo`. The engine is also exposed on `window.ZeroSounds0Engine` for console experiments.

---

## Using the component — `ZeroSounds0.jsx`

### As a React component

```bash
cp ZeroSounds0.jsx src/components/     # no dependencies beyond React
```

```jsx
import ZeroSounds0 from './components/ZeroSounds0';

export default function App() {
  return <ZeroSounds0 initialSeed={0x1337BEEF} />;
}
```

### As a game audio engine

```js
import { ZBEngine, zbDeriveParams, zbRandomize, zbHash } from './ZeroSounds0';

const engine = new ZBEngine();
engine.init();                       // call after a user gesture (creates the AudioContext)

// On player jump:
engine.play(zbDeriveParams('jump', game.worldSeed));

// Reproducible variety — every client derives the same patch from the item id:
engine.play(zbRandomize(item.id, game.worldSeed));

// Sounds change character as the player descends — no state stored:
engine.play(zbDeriveParams('footstep', zbHash(player.depth, game.worldSeed)));
```

`engine.play()` renders on first use and caches the buffer, so repeated sounds cost nothing. To avoid a first-play hitch, warm the cache at load time with `engine.render(patch)`.

### API

```js
// Hash core
zbHash(key, seed)                  // → u32
zbFloat(hash)                      // → [0,1)
zbRange(hash, min, max)            // → float
zbInt(hash, min, max)              // → integer, inclusive

// Patch derivation
zbDeriveParams(soundName, worldSeed)
// → { soundSeed, category, style, algorithm, feedback, baseFreq, freqSweep,
//     duration, gateTicks, vibrato, operators[4], arpeggio, psg, dac, echo }
zbRandomize(numericSeed, worldSeed)        // same shape — an unnamed sound at that position
zbBuildPatch(seed, categoryName, style)    // the builder both of the above call

// Rendering
zbRender(patch, { ladder: true, lowpass: true })   // → Int16Array, mono, 53267 Hz
zbFingerprint(pcm)                                 // → u32
zbEncodeWav(pcm)                                   // → ArrayBuffer (16-bit WAV)

// Verification
zbVerifyAll(worldSeed)
// → { soundName: { ok, fingerprint, paramsOk, audioOk, tablesOk, golden } }

// Playback
const engine = new ZBEngine();
engine.init();  engine.resume();  engine.setVolume(0.0–1.0);
engine.render(patch, opts);        // → { pcm, fingerprint }, cached, no AudioContext needed
engine.play(patch, opts);          // renders if needed, plays, returns { pcm, fingerprint }
```

### Adding a sound

```js
// In ZB_SOUNDS — one line, no parameters to write.
stab: { category: 'combat', style: 0, styleIndex: 5, label: '🗡 Stab' },
```

Style `0` = tonal, `1` = noisy, `2` = arpeggio. Use a `styleIndex` not already taken in that category. Add the same line to `ZB_SOUNDS` in `ZeroSounds0.py` if you use both.

---

## Using the Python script — `ZeroSounds0.py`

Python 3.8+, standard library only.

```bash
python ZeroSounds0.py list                              # the named sounds
python ZeroSounds0.py params explosion --seed 0xDEADBEEF   # derived patch as JSON
python ZeroSounds0.py render jump -o jump.wav           # one WAV
python ZeroSounds0.py render-all --outdir sfx/          # the whole library for a seed
python ZeroSounds0.py randomize 42 -o patch42.wav       # unnamed patch at position 42
python ZeroSounds0.py fingerprints                      # PCM fingerprints to compare with the browser
python ZeroSounds0.py verify                            # determinism + golden check
python ZeroSounds0.py build-demo                        # regenerate ZeroSounds0-demo.html
```

`render`, `render-all` and `randomize` accept `--seed`, `--no-ladder` and `--no-lowpass`.

As a module:

```python
import ZeroSounds0 as zs

patch = zs.zb_derive_params("coin", 0x1337BEEF)
pcm   = zs.zb_render(patch)                 # list of int16 samples at 53267 Hz
zs.zb_write_wav("coin.wav", pcm)
print(zs.zb_hex(zs.zb_fingerprint(pcm)))    # 0x73FD5AAF — the browser shows the same value
```

It is a pure-Python per-sample renderer, so expect several sounds per second rather than real time; it is meant for baking assets and as the reference for ports.

---

## Verifying a build

```js
import { zbVerifyAll } from './ZeroSounds0';
console.table(zbVerifyAll(0x1337BEEF));     // every row should be ok: true, golden: true
```

```bash
python ZeroSounds0.py verify                # ALL 34 SOUNDS DETERMINISTIC
```

Both engines embed the same golden fingerprints for world seed `0x1337BEEF`. If a platform, minifier or port changes a single sample, the fingerprint changes and the check fails.

---

## Sound library

34 named sounds across 8 categories (the same names as ZeroSounds v1).

| Category | Sounds |
|---|---|
| **movement** | jump, doubleJump, dash, land |
| **pickup** | coin, gem, heart, key |
| **combat** | laser, blaster, sword, punch, magicSpell |
| **explosion** | explosion, smallExplosion, boom |
| **ui** | menuSelect, menuConfirm, menuCancel, pause, textBlip |
| **power** | powerUp, levelUp, oneUp, heal |
| **damage** | hurt, death, enemyHit, warning |
| **environment** | doorOpen, chest, splash, teleport, footstep |

---

## Scope and accuracy

ZeroSounds0 is a style model, not a chip emulator. It keeps what you can hear — operator structure, algorithms, feedback, envelope resolution, 9-bit output, PSG voices, 60 Hz stepping — and simplifies rate tables, detune, and features such as SSG-EG and the hardware LFO. The grounding document lists every simplification. It contains no code, samples or data from any console, game or emulator.

"Sega", "Genesis" and "Mega Drive" are trademarks of their owner; this project is independent and not affiliated with or endorsed by Sega.

---

## 📚 Citation

### Academic Citation

If you use this codebase in your research or project, please cite:

```bibtex
@software{zerosounds0_jsx,
  title  = {ZeroSounds0-JSX: ZeroByes-Aligned Deterministic Genesis-Style FM Audio Synthesis},
  author = {Drift Johnson},
  year   = {2026},
  url    = {https://github.com/MushroomFleet/zerosounds0-JSX},
  version = {1.0.0}
}
```

### Donate

[![Ko-Fi](https://cdn.ko-fi.com/cdn/kofi3.png?v=3)](https://ko-fi.com/driftjohnson)
