"""Wilson et al. 2017 (arXiv:1705.08292) — linearly separable counterexample.

Reproduces the paper's core construction: a linearly separable binary
classification problem where a non-adaptive method (gradient descent)
generalizes perfectly while Adam approaches chance-level test error.

Construction (section 3.3 of the paper, simplified):
  - x[0] = y (the only informative feature)
  - x[1] = x[2] = 1 (always-on dummy features)
  - each example gets its own private block of dims set to 1 — one dim for
    y=+1, five dims for y=-1 — enabling memorization. Test examples use
    fresh private dims, so at test time only the first three weights matter.
  - classes are imbalanced (P[y=+1]=0.6) so the dummy features acquire
    positive weight.

Adam equalizes per-dim update magnitudes (sign-descent-like), converging to
|w0| ~= |w1| ~= |w2|, which predicts +1 for everything: test error ~= P[y=-1].
Gradient descent's update is dominated by the informative feature: error ~= 0.

Non-adaptive training is full-batch GD (the paper's theory covers GD and SGD
alike; full-batch keeps the demo deterministic-ish and fast).

Output contract: last stdout line is a single JSON object with metrics and
claim verdicts.
"""

import json

import numpy as np

rng = np.random.default_rng(0)

N_TRAIN = 200
N_TEST = 200
STEPS = 4000
P_POS = 0.6


def make_data(n, offset):
    y = np.where(rng.random(n) < P_POS, 1.0, -1.0)
    d = 3 + 5 * (N_TRAIN + N_TEST)
    X = np.zeros((n, d))
    X[:, 0] = y
    X[:, 1] = 1.0
    X[:, 2] = 1.0
    for i in range(n):
        start = 3 + 5 * (offset + i)
        width = 1 if y[i] > 0 else 5
        X[i, start:start + width] = 1.0
    return X, y


def logistic_grad(w, X, y):
    z = np.clip(y * (X @ w), -30, 30)
    p = 1.0 / (1.0 + np.exp(-z))
    return -(X * (y * (1 - p))[:, None]).mean(axis=0)


def error_rate(w, X, y):
    pred = np.sign(X @ w)
    pred[pred == 0] = 1.0
    return float((pred != y).mean())


def train_gd(X, y, lr=1.0):
    w = np.zeros(X.shape[1])
    for _ in range(STEPS):
        w -= lr * logistic_grad(w, X, y)
    return w


def train_adam(X, y, lr=0.01, b1=0.9, b2=0.999, eps=1e-8):
    w = np.zeros(X.shape[1])
    m = np.zeros_like(w)
    v = np.zeros_like(w)
    for t in range(1, STEPS + 1):
        g = logistic_grad(w, X, y)
        m = b1 * m + (1 - b1) * g
        v = b2 * v + (1 - b2) * g * g
        m_hat = m / (1 - b1 ** t)
        v_hat = v / (1 - b2 ** t)
        w -= lr * m_hat / (np.sqrt(v_hat) + eps)
    return w


def main():
    X_train, y_train = make_data(N_TRAIN, offset=0)
    X_test, y_test = make_data(N_TEST, offset=N_TRAIN)

    w_gd = train_gd(X_train, y_train)
    w_adam = train_adam(X_train, y_train)

    err_gd_train = error_rate(w_gd, X_train, y_train)
    err_adam_train = error_rate(w_adam, X_train, y_train)
    err_gd = error_rate(w_gd, X_test, y_test)
    err_adam = error_rate(w_adam, X_test, y_test)

    print(f"train error  GD={err_gd_train:.3f}  Adam={err_adam_train:.3f}")
    print(f"test  error  GD={err_gd:.3f}  Adam={err_adam:.3f}")
    print(f"Adam first weights (equalized as theory predicts): {w_adam[:3]}")

    # wilson2017-c2: SGD/GD ~zero test error while adaptive approaches chance
    validated = err_gd <= 0.05 and err_adam >= 0.30
    result = {
        "method_id": "wilson2017-m1",
        "metrics": {
            "train_error_gd": err_gd_train,
            "train_error_adam": err_adam_train,
            "test_error_gd": err_gd,
            "test_error_adam": err_adam,
        },
        "claim_checks": [
            {
                "claim_id": "wilson2017-c2",
                "verdict": "VALIDATES" if validated else "REFUTES",
                "detail": (
                    f"GD test error {err_gd:.3f} vs Adam test error "
                    f"{err_adam:.3f} on the separable construction "
                    f"(n_train={N_TRAIN}, steps={STEPS})"
                ),
            },
            {
                "claim_id": "wilson2017-c1",
                "verdict": "VALIDATES" if err_adam > err_gd + 0.05 else "REFUTES",
                "detail": (
                    f"Adam generalized worse than GD by "
                    f"{err_adam - err_gd:.3f} test-error points despite "
                    f"both reaching {err_adam_train:.3f}/{err_gd_train:.3f} "
                    f"training error"
                ),
            },
        ],
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
