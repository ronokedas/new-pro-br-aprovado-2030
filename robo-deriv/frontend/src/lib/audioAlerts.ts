// audioAlerts.ts - Sistema Sintetizado de Alertas Sonoros via Web Audio API

const STORAGE_KEY_PRE_SIGNAL = 'btc_radar_alert_pre_signal';
const STORAGE_KEY_SR_TOUCH = 'btc_radar_alert_sr_touch';
const STORAGE_KEY_VOLUME = 'btc_radar_alert_volume';

export interface AudioPreferences {
  preSignalEnabled: boolean;
  srTouchEnabled: boolean;
  volume: number; // 0.0 to 1.0
}

class AudioAlertEngine {
  private ctx: AudioContext | null = null;
  private lastSrTriggerTime: Map<string, number> = new Map();
  private lastBeepSecond: number = -1;

  constructor() {
    // AudioContext is initialized on first user interaction or call
  }

  private getContext(): AudioContext | null {
    try {
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!this.ctx && AudioCtx) {
        this.ctx = new AudioCtx();
      }
      if (this.ctx && this.ctx.state === 'suspended') {
        this.ctx.resume();
      }
      return this.ctx;
    } catch {
      return null;
    }
  }

  public getPreferences(): AudioPreferences {
    try {
      const pre = localStorage.getItem(STORAGE_KEY_PRE_SIGNAL);
      const sr = localStorage.getItem(STORAGE_KEY_SR_TOUCH);
      const vol = localStorage.getItem(STORAGE_KEY_VOLUME);

      return {
        preSignalEnabled: pre === null ? true : pre === 'true',
        srTouchEnabled: sr === null ? true : sr === 'true',
        volume: vol === null ? 0.8 : Math.max(0.1, Math.min(1.0, parseFloat(vol))),
      };
    } catch {
      return { preSignalEnabled: true, srTouchEnabled: true, volume: 0.8 };
    }
  }

  public savePreferences(prefs: Partial<AudioPreferences>): void {
    try {
      if (prefs.preSignalEnabled !== undefined) {
        localStorage.setItem(STORAGE_KEY_PRE_SIGNAL, String(prefs.preSignalEnabled));
      }
      if (prefs.srTouchEnabled !== undefined) {
        localStorage.setItem(STORAGE_KEY_SR_TOUCH, String(prefs.srTouchEnabled));
      }
      if (prefs.volume !== undefined) {
        localStorage.setItem(STORAGE_KEY_VOLUME, String(prefs.volume));
      }
    } catch {
      // ignore
    }
  }

  // Toca um beep sintetizado customizado
  private playTone(
    freq: number,
    duration = 0.12,
    type: OscillatorType = 'sine',
    gainMultiplier = 1.0,
  ): void {
    const ctx = this.getContext();
    if (!ctx) return;

    try {
      const prefs = this.getPreferences();
      const masterVol = prefs.volume * 0.15 * gainMultiplier;

      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = type;
      osc.frequency.setValueAtTime(freq, ctx.currentTime);

      gain.gain.setValueAtTime(masterVol, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start();
      osc.stop(ctx.currentTime + duration);
    } catch {
      // ignore audio autoplay policies
    }
  }

  // Alerta 1: Pré-Sinal Fixo (:03s, :02s, :01s e sino de confirmação no :00s)
  public handleCountdownTick(secondsLeft: number): void {
    const prefs = this.getPreferences();
    if (!prefs.preSignalEnabled) return;

    if (secondsLeft === this.lastBeepSecond) return;
    this.lastBeepSecond = secondsLeft;

    if (secondsLeft === 3) {
      this.playTone(880, 0.12, 'sine', 0.9); // La 5
    } else if (secondsLeft === 2) {
      this.playTone(987.77, 0.12, 'sine', 0.95); // Si 5
    } else if (secondsLeft === 1) {
      this.playTone(1174.66, 0.14, 'sine', 1.0); // Ré 6
    } else if (secondsLeft === 0 || secondsLeft === 60) {
      // Sino de fechamento e travamento do novo sinal
      this.playConfirmationChime();
    }
  }

  // Sino de confirmação suave (:00s)
  public playConfirmationChime(): void {
    const ctx = this.getContext();
    if (!ctx) return;

    try {
      const prefs = this.getPreferences();
      const masterVol = prefs.volume * 0.18;

      const chord = [1046.5, 1318.51, 1567.98]; // Acorde C6 Maior
      chord.forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = 'triangle';
        osc.frequency.setValueAtTime(freq, ctx.currentTime + idx * 0.04);

        gain.gain.setValueAtTime(masterVol / chord.length, ctx.currentTime + idx * 0.04);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.45);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(ctx.currentTime + idx * 0.04);
        osc.stop(ctx.currentTime + 0.45);
      });
    } catch {
      // ignore
    }
  }

  // Alerta 2: Toque em Suporte ou Resistência M5/M15 com proteção anti-spam (45s cooldown)
  public handleSrTouch(zoneKey: string, isSupport: boolean): boolean {
    const prefs = this.getPreferences();
    if (!prefs.srTouchEnabled) return false;

    const now = Date.now();
    const lastTime = this.lastSrTriggerTime.get(zoneKey) || 0;

    // Cooldown de 45 segundos para a mesma zona
    if (now - lastTime < 45000) {
      return false;
    }

    this.lastSrTriggerTime.set(zoneKey, now);

    // Toca som característico de toque
    this.playSrChime(isSupport);
    return true;
  }

  // Som característico de toque em suporte ou resistência
  public playSrChime(isSupport: boolean): void {
    const ctx = this.getContext();
    if (!ctx) return;

    try {
      const prefs = this.getPreferences();
      const masterVol = prefs.volume * 0.22;

      // Suporte: tom ascendente esperançoso (Fá -> Lá -> Dó)
      // Resistência: tom descendente de alerta/rejeição (Dó -> Sol -> Mi)
      const freqs = isSupport ? [698.46, 880.0, 1046.5] : [1046.5, 783.99, 659.25];

      freqs.forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = isSupport ? 'sine' : 'triangle';
        osc.frequency.setValueAtTime(freq, ctx.currentTime + idx * 0.07);

        gain.gain.setValueAtTime(masterVol / freqs.length, ctx.currentTime + idx * 0.07);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.55);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(ctx.currentTime + idx * 0.07);
        osc.stop(ctx.currentTime + 0.55);
      });
    } catch {
      // ignore
    }
  }

  // Métodos de teste sonoro para o usuário pré-escutar
  public testPreSignal(): void {
    this.playTone(880, 0.12, 'sine', 1.0);
    setTimeout(() => this.playTone(987.77, 0.12, 'sine', 1.0), 160);
    setTimeout(() => this.playTone(1174.66, 0.14, 'sine', 1.0), 320);
    setTimeout(() => this.playConfirmationChime(), 480);
  }

  public testSrTouch(isSupport = true): void {
    this.playSrChime(isSupport);
  }
}

export const audioAlerts = new AudioAlertEngine();
