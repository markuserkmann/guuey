import type { LiveServerMessage, Session } from "@google/genai";
import { apiFetch } from "./chatApi";

type Callbacks = {
  onReady: () => void;
  onStopping: () => void;
  onTranscript: (text: string) => void;
  onEnd: (error?: string) => void;
};

type VoiceSession = {
  token: string;
  model: string;
  apiVersion: string;
  responseModalities: ("TEXT" | "AUDIO")[];
};

const TARGET_RATE = 16000;
const FINISH_TIMEOUT_MS = 4000;
const FINISH_GRACE_MS = 400;
const MAX_PENDING_AUDIO = (TARGET_RATE * 2 * 30 * 4) / 3;

// Ported from Junction-Valio-Aimo/frontend/src/lib/voice.ts.
export function toPcm16Base64(
  samples: Float32Array,
  inputRate: number,
): string {
  const ratio = inputRate / TARGET_RATE;
  const length = Math.floor(samples.length / ratio);
  const pcm = new DataView(new ArrayBuffer(length * 2));
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.max(start + 1, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end && j < samples.length; j++) sum += samples[j];
    const value = Math.max(-1, Math.min(1, sum / (end - start)));
    pcm.setInt16(i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
  }
  let binary = "";
  const bytes = new Uint8Array(pcm.buffer);
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export class VoiceTranscription {
  private session?: Session;
  private context?: AudioContext;
  private stream?: MediaStream;
  private source?: MediaStreamAudioSourceNode;
  private capture?: AudioWorkletNode;
  private request = new AbortController();
  private timer?: number;
  private durationTimer?: number;
  private graceTimer?: number;
  private ended = false;
  private stopping = false;
  private pending: string[] = [];
  private pendingSize = 0;
  private transcript = "";
  private interim = "";

  constructor(
    private token: string,
    private callbacks: Callbacks,
  ) {}

  async start() {
    if (
      !window.isSecureContext ||
      !navigator.mediaDevices?.getUserMedia ||
      !window.AudioContext ||
      !window.AudioWorkletNode
    ) {
      this.finish(
        "Voice input needs a browser with microphone support on HTTPS or localhost.",
      );
      return;
    }
    this.timer = window.setTimeout(
      () => this.finish("Microphone connection timed out. Please try again."),
      30000,
    );
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (this.ended) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      stream.getAudioTracks().forEach((track) => {
        track.onended = () =>
          this.finish("The microphone disconnected. Your draft is still here.");
      });
      this.context = new AudioContext();
      await this.context.resume();
      if (this.ended) return;
      await this.context.audioWorklet.addModule(
        new URL("./audioCapture.worklet.js", import.meta.url),
      );
      if (this.ended) return;
      this.source = this.context.createMediaStreamSource(stream);
      this.capture = new AudioWorkletNode(this.context, "microphone-capture");
      this.capture.onprocessorerror = () =>
        this.finish("Microphone audio processing failed. Please try again.");
      this.capture.port.onmessage = (
        event: MessageEvent<Float32Array | string>,
      ) => {
        if (this.ended) return;
        try {
          if (event.data === "stopped") {
            this.releaseMicrophone();
            this.session?.sendRealtimeInput({ activityEnd: {} });
          } else if (event.data instanceof Float32Array) {
            const data = toPcm16Base64(event.data, this.context!.sampleRate);
            if (this.session) this.sendAudio(data);
            else {
              this.pending.push(data);
              this.pendingSize += data.length;
              if (this.pendingSize > MAX_PENDING_AUDIO)
                this.finish(
                  "The connection is too slow for voice input. Please try again.",
                );
            }
          }
        } catch {
          this.finish(
            "Voice input disconnected. Please review the draft and try again.",
          );
        }
      };
      this.source.connect(this.capture);
      // The worklet outputs silence; this keeps capture running without microphone playback.
      this.capture.connect(this.context.destination);
      this.durationTimer = window.setTimeout(() => this.stop(), 300000);
      const [response, { GoogleGenAI, Modality }] = await Promise.all([
        apiFetch("/api/voice/token", this.token, {
          method: "POST",
          body: "{}",
          signal: this.request.signal,
        }),
        import("@google/genai"),
      ]);
      const { session: voice }: { session: VoiceSession } =
        await response.json();
      if (this.ended) return;
      const ai = new GoogleGenAI({
        apiKey: voice.token,
        httpOptions: { apiVersion: voice.apiVersion },
      });
      const session = await ai.live.connect({
        model: voice.model,
        config: {
          responseModalities: voice.responseModalities.map((value) =>
            value === "TEXT" ? Modality.TEXT : Modality.AUDIO,
          ),
        },
        callbacks: {
          onmessage: (message) => this.receiveTranscript(message),
          onerror: () =>
            this.finish("The voice connection failed. Please try again."),
          onclose: () => {
            if (!this.ended)
              this.finish(
                "Voice input disconnected. Please review the draft and try again.",
              );
          },
        },
      });
      if (this.ended) {
        session.close();
        return;
      }
      this.session = session;
      session.sendRealtimeInput({ activityStart: {} });
      for (const data of this.pending.splice(0)) this.sendAudio(data);
      this.pendingSize = 0;
      window.clearTimeout(this.timer);
      this.callbacks.onReady();
    } catch (cause) {
      if (this.ended) return;
      const name = cause instanceof Error ? cause.name : "";
      this.finish(
        name === "NotAllowedError"
          ? "Microphone access was denied. Allow it in your browser settings and try again."
          : name === "NotFoundError"
            ? "No microphone was found. Connect one and try again."
            : "Couldn't start voice input. Please try again.",
      );
    }
  }

  private sendAudio(data: string) {
    this.session?.sendRealtimeInput({
      audio: { data, mimeType: `audio/pcm;rate=${TARGET_RATE}` },
    });
  }

  private receiveTranscript(message: LiveServerMessage) {
    if (this.ended) return;
    const content = message.serverContent;
    const text = content?.inputTranscription?.text;
    const partial = content?.interimInputTranscription?.text;
    if (text) {
      this.transcript += text;
      this.interim = "";
    } else if (partial !== undefined) {
      this.interim = partial;
    }
    if (text || partial !== undefined)
      this.callbacks.onTranscript(
        `${this.transcript} ${this.interim}`.replace(/\s+/g, " ").trim(),
      );
    if (
      this.stopping &&
      (content?.modelTurn ||
        content?.turnComplete ||
        content?.generationComplete ||
        content?.inputTranscription?.finished)
    ) {
      window.clearTimeout(this.graceTimer);
      this.graceTimer = window.setTimeout(() => this.finish(), FINISH_GRACE_MS);
    }
    if (message.goAway)
      this.finish("The voice session ended. Start again to continue.");
  }

  stop() {
    if (this.ended || this.stopping) return;
    if (!this.session) {
      this.finish();
      return;
    }
    this.stopping = true;
    this.stopTracks();
    this.callbacks.onStopping();
    window.clearTimeout(this.durationTimer);
    this.capture?.port.postMessage("stop");
    this.timer = window.setTimeout(
      () =>
        this.finish(
          this.interim
            ? "The last words could not be finalized. Please review the draft."
            : undefined,
        ),
      FINISH_TIMEOUT_MS,
    );
  }

  cancel() {
    this.finish();
  }

  private stopTracks() {
    this.stream?.getTracks().forEach((track) => {
      track.onended = null;
      track.stop();
    });
  }

  private releaseMicrophone() {
    this.stopTracks();
    this.source?.disconnect();
    this.capture?.disconnect();
    this.capture?.port.close();
    if (this.context && this.context.state !== "closed")
      void this.context.close().catch(() => undefined);
  }

  private finish(error?: string) {
    if (this.ended) return;
    this.ended = true;
    this.request.abort();
    this.pending = [];
    this.pendingSize = 0;
    window.clearTimeout(this.timer);
    window.clearTimeout(this.durationTimer);
    window.clearTimeout(this.graceTimer);
    this.releaseMicrophone();
    this.session?.close();
    this.callbacks.onEnd(error);
  }
}
