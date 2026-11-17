import numpy as np
import torch
from scipy.stats import genpareto
from sklearn.ensemble import IsolationForest
from torch import nn


class Autoencoder(nn.Module):
    def __init__(self, dimensions):
        super().__init__()
        self.encoder = nn.GRU(dimensions, 16, batch_first=True)
        self.decoder = nn.GRU(16, 16, batch_first=True)
        self.output = nn.Linear(16, dimensions)

    def forward(self, windows):
        _, hidden = self.encoder(windows)
        context = hidden.transpose(0, 1).expand(-1, windows.shape[1], -1)
        decoded, _ = self.decoder(context, hidden)
        return self.output(decoded)


def window_features(windows):
    return np.concatenate(
        [windows[:, -1], windows.mean(axis=1), windows.var(axis=1),
         windows[:, -1] - windows[:, -2]], axis=1
    )


def threshold(scores, probability):
    cutoff = np.quantile(scores, 0.9)
    tail = scores[scores > cutoff] - cutoff
    if len(tail) >= 20 and len(tail) / len(scores) > probability:
        shape, _, scale = genpareto.fit(tail, floc=0)
        estimate = cutoff + genpareto.isf(
            probability * len(scores) / len(tail), shape, scale=scale
        )
        if np.isfinite(estimate) and estimate > cutoff:
            return float(estimate)
    return float(np.quantile(scores, 1 - probability))


class Detector:
    def __init__(self, training, window=12, epochs=8):
        self.window = window
        split = int(len(training) * 0.75)
        if split < window + 20 or len(training) - split < window + 20:
            raise ValueError("Insufficient baseline observations")
        if not np.isfinite(training).all():
            raise ValueError("Baseline contains missing or invalid observations")

        baseline = training[:split]
        self.center = baseline.mean(axis=0)
        self.scale = baseline.std(axis=0, ddof=1).clip(min=1e-6)
        fitting = self.windows(baseline)
        calibration = self.windows(training[split:])
        self.forest = IsolationForest(n_estimators=100, random_state=42)
        self.forest.fit(window_features(fitting))

        torch.manual_seed(42)
        self.autoencoder = Autoencoder(training.shape[1])
        optimizer = torch.optim.Adam(self.autoencoder.parameters())
        examples = torch.from_numpy(fitting)
        for _ in range(epochs):
            for indices in torch.randperm(len(examples)).split(128):
                batch = examples[indices]
                loss = (self.autoencoder(batch) - batch).square().mean()
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
        self.autoencoder.eval()

        forest, errors = self.raw(calibration)
        raw_scores = np.column_stack([forest, errors.mean(axis=1)])
        self.reference = np.sort(raw_scores, axis=0)
        individual = self.normalize(raw_scores)
        combined = self.smooth(individual.mean(axis=1))
        self.warning = min(threshold(combined, 0.01), 0.999)
        self.critical = min(max(threshold(combined, 0.002), self.warning + 1e-6), 0.9999)
        self.model_cutoffs = np.quantile(individual, 0.99, axis=0)

    def windows(self, rows):
        normalized = ((rows - self.center) / self.scale).astype(np.float32)
        return np.array(
            np.lib.stride_tricks.sliding_window_view(normalized, self.window, axis=0)
            .swapaxes(1, 2),
            copy=True,
            order="C",
        )

    @torch.inference_mode()
    def raw(self, windows):
        forest = -self.forest.score_samples(window_features(windows))
        errors = []
        for batch in torch.from_numpy(windows).split(256):
            errors.append((self.autoencoder(batch)[:, -1] - batch[:, -1]).square().numpy())
        return forest, np.concatenate(errors)

    def normalize(self, scores):
        # Tail extrapolation retains separation above the largest calibration score.
        result = []
        for values, reference in zip(scores.T, self.reference.T):
            upper = reference[-1]
            spread = max(upper - np.quantile(reference, 0.95), 1e-8)
            ranks = np.searchsorted(reference, values, side="left") / len(reference)
            excess = np.maximum(values - upper, 0)
            result.append(ranks * 0.98 + 0.02 * (1 - np.exp(-excess / spread)))
        return np.column_stack(result)

    @staticmethod
    def smooth(scores, previous=None):
        result = []
        for score in scores:
            previous = score if previous is None else 0.6 * previous + 0.4 * score
            result.append(previous)
        return np.asarray(result)

    def predict(self, rows, previous=None):
        forest, errors = self.raw(self.windows(rows))
        models = self.normalize(np.column_stack([forest, errors.mean(axis=1)]))
        return self.smooth(models.mean(axis=1), previous), models, errors
