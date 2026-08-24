// AudioWorklet: captures mic audio off the main thread, resamples to 16 kHz,
// and posts 0.1 s Int16 PCM chunks to the page. Runs in the audio render thread
// so it doesn't glitch/drop under UI load (unlike the old ScriptProcessorNode).
class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; // global `sampleRate` = context rate
    this.pos = 0;
    this.acc = 0;
    this.accN = 0;
    this.out = [];
    this.target = 1600; // 0.1 s at 16 kHz
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        // average-decimation resample to 16 kHz (ratio ≈ 1 when ctx is 16 kHz)
        this.acc += ch[i];
        this.accN++;
        this.pos += 1;
        if (this.pos >= this.ratio) {
          this.pos -= this.ratio;
          let v = this.accN ? this.acc / this.accN : 0;
          this.acc = 0;
          this.accN = 0;
          v = Math.max(-1, Math.min(1, v));
          this.out.push(v < 0 ? v * 0x8000 : v * 0x7fff);
        }
      }
      while (this.out.length >= this.target) {
        const chunk = this.out.splice(0, this.target);
        const i16 = new Int16Array(chunk);
        this.port.postMessage(i16.buffer, [i16.buffer]);
      }
    }
    return true;
  }
}
registerProcessor("pcm-worklet", PCMWorklet);
