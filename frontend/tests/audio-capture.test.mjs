import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";
import { loadSource } from "./load-source.mjs";

const { toPcm16Base64 } = await loadSource(["./src/voiceTranscription.ts"]);

const source = await readFile(
  new URL("../src/audioCapture.worklet.js", import.meta.url),
  "utf8",
);

function capture(rate, samples) {
  let Processor;
  const messages = [];
  vm.runInNewContext(source, {
    sampleRate: rate,
    AudioWorkletProcessor: class {
      port = { postMessage: (message) => messages.push(message) };
    },
    registerProcessor: (_, implementation) => (Processor = implementation),
  });
  const processor = new Processor();
  for (let offset = 0; offset < samples.length; offset += 128) {
    processor.process([[samples.subarray(offset, offset + 128)]]);
  }
  processor.port.onmessage({ data: "stop" });
  assert.equal(messages.pop(), "stopped");
  return Buffer.concat(
    messages.map((message) =>
      Buffer.from(toPcm16Base64(message, rate), "base64"),
    ),
  );
}

for (const frequency of [400, 3000, 6500]) {
  const rate = 16000;
  test(`PCM preserves ${frequency} Hz speech-band audio across blocks`, () => {
    const samples = Float32Array.from(
      { length: rate * 2 },
      (_, index) => 0.5 * Math.sin((2 * Math.PI * frequency * index) / rate),
    );
    const pcm = capture(rate, samples);
    assert.equal(pcm.length, 16000 * 2 * 2);
    let crossings = 0;
    let energy = 0;
    for (let offset = 2; offset < pcm.length; offset += 2) {
      const sample = pcm.readInt16LE(offset);
      if (pcm.readInt16LE(offset - 2) < 0 && sample >= 0) crossings++;
      energy += (sample / 32768) ** 2;
    }
    assert.ok(Math.abs(crossings - frequency * 2) <= 1);
    const rms = Math.sqrt(energy / (pcm.length / 2));
    assert.ok(rms > 0.34 && rms < 0.36);
  });
}

for (const rate of [44100, 48000]) {
  test(`native ${rate} Hz capture produces 16 kHz PCM without losing whole blocks`, () => {
    const samples = Float32Array.from(
      { length: rate },
      (_, i) => 0.5 * Math.sin((2 * Math.PI * 400 * i) / rate),
    );
    const pcm = capture(rate, samples);
    assert.ok(Math.abs(pcm.length / 32000 - 1) < 0.002);
    assert.ok(pcm.some((byte) => byte !== 0));
  });
}

test("PCM is little-endian, clipped correctly, and flushes the final short frame", () => {
  const pcm = capture(16000, Float32Array.from([-2, -1, -0.5, 0, 0.5, 1, 2]));
  assert.deepEqual(
    Array.from({ length: 7 }, (_, index) => pcm.readInt16LE(index * 2)),
    [-32768, -32768, -16384, 0, 16383, 32767, 32767],
  );
});
