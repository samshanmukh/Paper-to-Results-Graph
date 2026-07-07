"""LLM-generated implementation for zhang2019-m1 (Butterbase gateway). Review before trusting."""
import numpy as np
import os
import json
import time

# Reproducibility
rng = np.random.default_rng(0)

# Read experiment parameters from environment
k = int(os.environ.get("P2R_K", "5"))
alpha_slow = float(os.environ.get("P2R_ALPHA_SLOW", "0.5"))

# Generate synthetic dataset
n_samples = 200
n_features = 20
X = rng.standard_normal((n_samples, n_features))
y = rng.standard_normal((n_samples, 1))

# Normalize for stability
X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)
y = (y - y.mean()) / (y.std() + 1e-8)

# Hyperparameters
lr_inner = 0.01
n_outer_steps = 100

# Helper function to compute loss and gradient
def compute_loss_grad(X, y, w):
    pred = X @ w
    loss = np.mean((pred - y)**2)
    grad = 2 * X.T @ (pred - y) / len(X)
    return loss, grad

# Lookahead optimizer
print("Running Lookahead optimizer...")
w_slow_lookahead = rng.standard_normal((n_features, 1)) * 0.01
w_fast = w_slow_lookahead.copy()
lookahead_losses = []
lookahead_grad_norms = []

start_time = time.time()
for outer_step in range(n_outer_steps):
    w_fast = w_slow_lookahead.copy()
    
    # Inner loop: k fast weight updates
    for i in range(k):
        loss, grad = compute_loss_grad(X, y, w_fast)
        w_fast = w_fast - lr_inner * grad
    
    # Slow weight update
    w_slow_lookahead = w_slow_lookahead + alpha_slow * (w_fast - w_slow_lookahead)
    
    # Compute metrics on slow weights
    loss, grad = compute_loss_grad(X, y, w_slow_lookahead)
    lookahead_losses.append(loss)
    lookahead_grad_norms.append(np.linalg.norm(grad))
    
    if outer_step % 20 == 0:
        print(f'Lookahead Step {outer_step}, Loss: {loss:.6f}')

lookahead_time = time.time() - start_time

# Baseline SGD (same total number of gradient steps)
print("\nRunning baseline SGD...")
w_sgd = rng.standard_normal((n_features, 1)) * 0.01
sgd_losses = []
sgd_grad_norms = []

start_time = time.time()
step_count = 0
for outer_step in range(n_outer_steps):
    for i in range(k):
        loss, grad = compute_loss_grad(X, y, w_sgd)
        w_sgd = w_sgd - lr_inner * grad
        step_count += 1
    
    # Record metrics at same frequency as Lookahead
    loss, grad = compute_loss_grad(X, y, w_sgd)
    sgd_losses.append(loss)
    sgd_grad_norms.append(np.linalg.norm(grad))
    
    if outer_step % 20 == 0:
        print(f'SGD Step {outer_step}, Loss: {loss:.6f}')

sgd_time = time.time() - start_time

# Compute metrics
lookahead_losses = np.array(lookahead_losses)
sgd_losses = np.array(sgd_losses)
lookahead_grad_norms = np.array(lookahead_grad_norms)
sgd_grad_norms = np.array(sgd_grad_norms)

# Variance of losses (stability metric)
lookahead_loss_variance = np.var(lookahead_losses[10:])  # Skip initial transient
sgd_loss_variance = np.var(sgd_losses[10:])
variance_reduction = (sgd_loss_variance - lookahead_loss_variance) / (sgd_loss_variance + 1e-10)

# Convergence speed: final loss comparison
lookahead_final_loss = lookahead_losses[-1]
sgd_final_loss = sgd_losses[-1]
convergence_improvement = (sgd_final_loss - lookahead_final_loss) / (sgd_final_loss + 1e-10)

# Computational overhead
overhead_ratio = lookahead_time / sgd_time

# Gradient norm variance (another stability metric)
lookahead_grad_variance = np.var(lookahead_grad_norms[10:])
sgd_grad_variance = np.var(sgd_grad_norms[10:])
grad_variance_reduction = (sgd_grad_variance - lookahead_grad_variance) / (sgd_grad_variance + 1e-10)

print(f"\nLookahead final loss: {lookahead_final_loss:.6f}")
print(f"SGD final loss: {sgd_final_loss:.6f}")
print(f"Loss variance reduction: {variance_reduction:.4f}")
print(f"Gradient variance reduction: {grad_variance_reduction:.4f}")
print(f"Time overhead ratio: {overhead_ratio:.4f}")

# Claim checks
claim_checks = []

# Claim 1: Reduces variance and improves stability
# VALIDATES if variance reduction > 10% OR gradient variance reduction > 10%
validates_c1 = (variance_reduction > 0.1) or (grad_variance_reduction > 0.1)
claim_checks.append({
    "claim_id": "zhang2019-c1",
    "verdict": "VALIDATES" if validates_c1 else "REFUTES",
    "detail": f"Loss variance reduction: {variance_reduction:.3f}, Gradient variance reduction: {grad_variance_reduction:.3f}. Threshold: >0.1 for either metric."
})

# Claim 2: Faster convergence (lower final loss)
# VALIDATES if lookahead achieves at least 5% lower final loss
validates_c2 = convergence_improvement > 0.05
claim_checks.append({
    "claim_id": "zhang2019-c2",
    "verdict": "VALIDATES" if validates_c2 else "REFUTES",
    "detail": f"Convergence improvement: {convergence_improvement:.3f} (Lookahead: {lookahead_final_loss:.6f} vs SGD: {sgd_final_loss:.6f}). Threshold: >0.05."
})

# Claim 3: Negligible overhead
# VALIDATES if overhead < 1.5x (50% overhead is reasonable for k=5)
validates_c3 = overhead_ratio < 1.5
claim_checks.append({
    "claim_id": "zhang2019-c3",
    "verdict": "VALIDATES" if validates_c3 else "REFUTES",
    "detail": f"Time overhead ratio: {overhead_ratio:.3f}. Threshold: <1.5x."
})

# Final JSON output
result = {
    "method_id": "zhang2019-m1",
    "params": {
        "k": k,
        "alpha_slow": alpha_slow,
        "lr_inner": lr_inner,
        "n_outer_steps": n_outer_steps
    },
    "metrics": {
        "lookahead_final_loss": float(lookahead_final_loss),
        "sgd_final_loss": float(sgd_final_loss),
        "loss_variance_reduction": float(variance_reduction),
        "grad_variance_reduction": float(grad_variance_reduction),
        "convergence_improvement": float(convergence_improvement),
        "time_overhead_ratio": float(overhead_ratio)
    },
    "claim_checks": claim_checks
}

print(json.dumps(result))
