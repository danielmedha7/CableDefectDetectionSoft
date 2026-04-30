"""Ultrasonic A-scan feature extractor.

Turns a windowed A-scan signal (1-D float array sampled at e.g. 1 MS/s)
into a fixed-length feature vector consumed by the XGBoost classifier
and depth/size regressors.

Features (in order):
   0   peak_amp           — max abs amplitude in gate
   1   peak_time_us       — time of peak inside gate (µs)
   2   rms                — root-mean-square of gate window
   3   energy             — Σ x² inside gate
   4   zero_crossings     — count of sign changes
   5   peak_to_peak       — max - min
   6   skewness
   7   kurtosis
   8   centroid_freq_hz   — spectral centroid via rFFT
   9   bandwidth_hz       — spectral bandwidth (rms about centroid)
  10   spectral_flatness  — geomean / arithmean of |FFT|
  11   dominant_freq_hz   — argmax(|FFT|)
  12   echo_count         — number of significant peaks (≥40 % of max)
  13   tof_first_peak_us  — time of first peak ≥ threshold
  14+  log-binned spectrum — 8 bands of mean log|FFT|
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

N_BANDS = 8
N_SCALAR = 14
FEATURE_LEN = N_SCALAR + N_BANDS  # = 22


@dataclass
class FeatureExtractorConfig:
    sample_rate_hz: int = 1_000_000
    gate_start_us: float = 10.0
    gate_end_us: float = 120.0
    echo_threshold_ratio: float = 0.40
    band_count: int = N_BANDS


class UltrasonicFeatureExtractor:

    def __init__(self, config: FeatureExtractorConfig | None = None):
        self.cfg = config or FeatureExtractorConfig()

    def _gate(self, signal: np.ndarray) -> tuple[np.ndarray, int]:
        fs = self.cfg.sample_rate_hz
        i0 = int(self.cfg.gate_start_us * 1e-6 * fs)
        i1 = int(self.cfg.gate_end_us * 1e-6 * fs)
        i0 = max(0, min(i0, signal.size - 2))
        i1 = max(i0 + 1, min(i1, signal.size))
        return signal[i0:i1], i0

    @staticmethod
    def _zero_crossings(x: np.ndarray) -> int:
        return int(np.sum(np.diff(np.sign(x)) != 0))

    @staticmethod
    def _moments(x: np.ndarray) -> tuple[float, float]:
        if x.size < 4:
            return 0.0, 0.0
        m = x.mean()
        std = x.std()
        if std < 1e-12:
            return 0.0, 0.0
        c = (x - m) / std
        skew = float(np.mean(c ** 3))
        kurt = float(np.mean(c ** 4) - 3.0)
        return skew, kurt

    def _spectrum(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = x.size
        if n < 8:
            return np.zeros(1), np.zeros(1)
        spec = np.abs(np.fft.rfft(x * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, d=1.0 / self.cfg.sample_rate_hz)
        return freqs, spec

    def _log_bands(self, freqs: np.ndarray, spec: np.ndarray) -> np.ndarray:
        if spec.size < 4:
            return np.zeros(self.cfg.band_count, dtype=np.float32)
        log_spec = np.log1p(spec)
        # geometric (log) split of frequency range
        f_min = max(freqs[1], 1.0)
        f_max = freqs[-1]
        edges = np.geomspace(f_min, f_max, self.cfg.band_count + 1)
        bands = np.zeros(self.cfg.band_count, dtype=np.float32)
        for i in range(self.cfg.band_count):
            mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
            if mask.any():
                bands[i] = float(log_spec[mask].mean())
        return bands

    def extract(self, signal: np.ndarray) -> np.ndarray:
        signal = np.asarray(signal, dtype=np.float32).ravel()
        gated, i0 = self._gate(signal)
        if gated.size == 0:
            return np.zeros(FEATURE_LEN, dtype=np.float32)

        # time-domain
        peak_amp = float(np.max(np.abs(gated)))
        peak_idx = int(np.argmax(np.abs(gated)))
        peak_time_us = (i0 + peak_idx) * 1e6 / self.cfg.sample_rate_hz
        rms = float(np.sqrt(np.mean(gated ** 2)))
        energy = float(np.sum(gated ** 2))
        zc = self._zero_crossings(gated)
        ptp = float(gated.max() - gated.min())
        skew, kurt = self._moments(gated)

        # echoes
        thresh = peak_amp * self.cfg.echo_threshold_ratio
        echo_idx = np.where(np.abs(gated) >= thresh)[0]
        echo_count = int(np.sum(np.diff(echo_idx) > 1)) + (1 if echo_idx.size else 0)
        tof_first_us = (i0 + int(echo_idx[0])) * 1e6 / self.cfg.sample_rate_hz if echo_idx.size else 0.0

        # frequency-domain
        freqs, spec = self._spectrum(gated)
        if spec.sum() > 1e-12:
            centroid = float(np.sum(freqs * spec) / np.sum(spec))
            bw = float(np.sqrt(np.sum(((freqs - centroid) ** 2) * spec) / np.sum(spec)))
            geo = float(np.exp(np.mean(np.log(spec + 1e-12))))
            arith = float(spec.mean())
            flatness = geo / arith if arith > 0 else 0.0
            dom_freq = float(freqs[int(np.argmax(spec))])
        else:
            centroid = bw = flatness = dom_freq = 0.0

        bands = self._log_bands(freqs, spec)

        scalar = np.array(
            [peak_amp, peak_time_us, rms, energy, zc, ptp, skew, kurt,
             centroid, bw, flatness, dom_freq, echo_count, tof_first_us],
            dtype=np.float32,
        )
        return np.concatenate([scalar, bands]).astype(np.float32)

    def extract_batch(self, signals: np.ndarray) -> np.ndarray:
        if signals.ndim == 1:
            return self.extract(signals).reshape(1, -1)
        return np.stack([self.extract(s) for s in signals], axis=0)
