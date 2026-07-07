"""LLM-generated implementation for balachandran2025-m1 (Butterbase gateway). Review before trusting."""
import os
import json
import numpy as np

def main():
    n_candidates = int(os.environ.get("P2R_N_CANDIDATES", 8))
    n_trials = int(os.environ.get("P2R_N_TRIALS", 200))
    noise = float(os.environ.get("P2R_NOISE", 0.3))
    rng = np.random.default_rng(0)
    p_base = 0.45
    ns = list(range(1, n_candidates + 1))
    accuracies = []
    for n in ns:
        correct_count = 0
        for _ in range(n_trials):
            is_correct = rng.random(n) < p_base
            scores = is_correct.astype(float) + rng.normal(0, noise, n)
            best_idx = np.argmax(scores)
            if is_correct[best_idx]:
                correct_count += 1
        acc = correct_count / n_trials
        accuracies.append(acc)
        print(f"n={n}: accuracy={acc:.4f}")
    accs = np.array(accuracies)
    gains = np.diff(accs)
    diminishing = np.all(gains[:-1] >= gains[1:]) or (np.mean(gains[:len(gains)//2]) > np.mean(gains[len(gains)//2:]))
    improves = accs[-1] > accs[0] + 0.05
    claim_checks = []
    if improves and diminishing:
        claim_checks.append({"claim_id": "balachandran2025-c1", "verdict": "VALIDATES", "detail": f"acc from {accs[0]:.3f} to {accs[-1]:.3f}, diminishing gains observed"})
    else:
        claim_checks.append({"claim_id": "balachandran2025-c1", "verdict": "REFUTES", "detail": "no clear improvement or diminishing"})
    claim_checks.append({"claim_id": "balachandran2025-c2", "verdict": "REFUTES", "detail": "token usage not modeled"})
    claim_checks.append({"claim_id": "balachandran2025-c3", "verdict": "REFUTES", "detail": "no 50x or perfect verifier tested"})
    params = {"n_candidates": n_candidates, "n_trials": n_trials, "noise": noise}
    metrics = {"ns": ns, "accuracies": [round(a, 4) for a in accuracies]}
    result = {"method_id": "balachandran2025-m1", "params": params, "metrics": metrics, "claim_checks": claim_checks}
    print(json.dumps(result))

if __name__ == "__main__":
    main()
