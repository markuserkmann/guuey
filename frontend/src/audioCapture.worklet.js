class MicrophoneCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = [];
    this.size = 0;
    this.stopped = false;
    this.port.onmessage = (event) => {
      if (event.data === "stop") {
        this.stopped = true;
        this.flush();
        this.port.postMessage("stopped");
      }
    };
  }

  flush() {
    if (!this.size) return;
    const block = new Float32Array(this.size);
    let offset = 0;
    for (const part of this.buffer) {
      block.set(part, offset);
      offset += part.length;
    }
    this.port.postMessage(block, [block.buffer]);
    this.buffer = [];
    this.size = 0;
  }

  process(inputs) {
    if (this.stopped) return false;
    const channel = inputs[0]?.[0];
    if (channel) {
      this.buffer.push(channel.slice());
      this.size += channel.length;
      if (this.size >= 2048) this.flush();
    }
    return true;
  }
}

registerProcessor("microphone-capture", MicrophoneCapture);
