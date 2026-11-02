/** Workletの登録先です。ブラウザー外のテストでも同じPCM処理を利用します。 */
export type CaptureProcessorBase = new () => { port: { postMessage(message: { pcm: ArrayBuffer; capturedAt: number }, transfer: ArrayBuffer[]): void } };

/** 入力サンプルレートを24kHzへ変換し、100ms単位のPCM16LEだけを送ります。 */
export function createMicrophoneProcessor(Base: CaptureProcessorBase, inputRate: number, captureTime: () => number) {
  return class EgakumiMicrophoneProcessor extends Base {
    private buffer = new ArrayBuffer(4_800);
    private view = new DataView(this.buffer);
    private offset = 0;
    private sourceIndex = 0;
    private nextOutput = 0;
    private previous = 0;
    private ratio = inputRate / 24_000;

    /** 音声を保存せず、転送したフレームの領域を直ちに手放します。 */
    process(inputs: Float32Array[][]): boolean {
      const input = inputs[0]?.[0]; if (!input) return true;
      for (const current of input) {
        while (this.nextOutput <= this.sourceIndex) {
          const fraction = Math.max(0, Math.min(1, this.nextOutput - this.sourceIndex + 1));
          const sample = Math.max(-1, Math.min(1, this.previous + (current - this.previous) * fraction));
          this.view.setInt16(this.offset++ * 2, Math.round(sample < 0 ? sample * 32_768 : sample * 32_767), true);
          this.nextOutput += this.ratio;
          if (this.offset === 2_400) { this.port.postMessage({ pcm: this.buffer, capturedAt: captureTime() }, [this.buffer]); this.buffer = new ArrayBuffer(4_800); this.view = new DataView(this.buffer); this.offset = 0; }
        }
        this.previous = current; this.sourceIndex++;
      }
      return true;
    }
  };
}
