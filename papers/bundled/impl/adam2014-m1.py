"""LLM-generated implementation for adam2014-m1 (Butterbase gateway). Review before trusting."""
#!/usr/bin/env python3
import numpy as np
import json
import os

def generate_synthetic_data(n_samples=1000, n_features=20, seed=0):
    """Generate synthetic binary classification dataset."""
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n_samples, n_features))
    true_weights = rng.standard_normal(n_features)
    logits = X @ true_weights
    probs = 1 / (1 + np.exp(-logits))
    y = (rng.random(n_samples) < probs).astype(np.float64)
    return X, y

def sigmoid(z):
    """Numerically stable sigmoid."""
    return np.where(z >= 0, 
                    1 / (1 + np.exp(-z)),
                    np.exp(z) / (1 + np.exp(z)))

def logistic_loss(X, y, weights):
    """Binary cross-entropy loss."""
    logits = X @ weights
    probs = sigmoid(logits)
    eps = 1e-15
    probs = np.clip(probs, eps, 1 - eps)
    return -np.mean(y * np.log(probs) + (1 - y) * np.log(1 - probs))

def logistic_gradient(X, y, weights):
    """Gradient of logistic loss."""
    logits = X @ weights
    probs = sigmoid(logits)
    return X.T @ (probs - y) / len(y)

class AdamOptimizer:
    """Adam optimizer implementation."""
    def __init__(self, n_params, alpha=0.001, beta1=0.9, beta2=0.999, eps=1e-8):
        self.alpha = alpha
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.m = np.zeros(n_params)
        self.v = np.zeros(n_params)
        self.t = 0
    
    def step(self, params, grad):
        """Perform one optimization step."""
        self.t += 1
        self.m = self.beta1 * self.m + (1 - self.beta1) * grad
        self.v = self.beta2 * self.v + (1 - self.beta2) * (grad ** 2)
        m_hat = self.m / (1 - self.beta1 ** self.t)
        v_hat = self.v / (1 - self.beta2 ** self.t)
        params -= self.alpha * m_hat / (np.sqrt(v_hat) + self.eps)
        return params

class SGDOptimizer:
    """SGD optimizer implementation."""
    def __init__(self, n_params, lr=0.01):
        self.lr = lr
    
    def step(self, params, grad):
        """Perform one optimization step."""
        params -= self.lr * grad
        return params

def train_logistic_regression(X, y, optimizer, n_epochs=100):
    """Train logistic regression with given optimizer."""
    n_features = X.shape[1]
    weights = np.zeros(n_features)
    losses = []
    
    for epoch in range(n_epochs):
        grad = logistic_gradient(X, y, weights)
        weights = optimizer.step(weights, grad)
        loss = logistic_loss(X, y, weights)
        losses.append(loss)
        
        if epoch % 20 == 0:
            print(f"  Epoch {epoch}: loss = {loss:.6f}")
    
    return weights, losses

def test_diagonal_rescaling_invariance(X, y, n_epochs=50):
    """Test if Adam's updates are invariant to diagonal rescaling of gradients."""
    n_features = X.shape[1]
    rng = np.random.default_rng(42)
    
    # Train with original gradients
    weights1 = np.zeros(n_features)
    opt1 = AdamOptimizer(n_features)
    for _ in range(n_epochs):
        grad = logistic_gradient(X, y, weights1)
        weights1 = opt1.step(weights1, grad)
    
    # Train with rescaled gradients (scale each dimension differently)
    scale_factors = rng.uniform(0.1, 10.0, n_features)
    weights2 = np.zeros(n_features)
    opt2 = AdamOptimizer(n_features)
    for _ in range(n_epochs):
        grad = logistic_gradient(X, y, weights2)
        grad_rescaled = grad * scale_factors
        weights2 = opt2.step(weights2, grad_rescaled)
    
    # Check if final losses are similar (invariance means similar convergence)
    loss1 = logistic_loss(X, y, weights1)
    loss2 = logistic_loss(X, y, weights2)
    
    return loss1, loss2

def main():
    # Read parameters from environment
    n_samples = int(os.environ.get("P2R_N_SAMPLES", "1000"))
    n_features = int(os.environ.get("P2R_N_FEATURES", "20"))
    n_epochs = int(os.environ.get("P2R_N_EPOCHS", "100"))
    adam_lr = float(os.environ.get("P2R_ADAM_LR", "0.001"))
    adam_beta1 = float(os.environ.get("P2R_ADAM_BETA1", "0.9"))
    adam_beta2 = float(os.environ.get("P2R_ADAM_BETA2", "0.999"))
    sgd_lr = float(os.environ.get("P2R_SGD_LR", "0.01"))
    seed = int(os.environ.get("P2R_SEED", "0"))
    
    print(f"Generating synthetic dataset: {n_samples} samples, {n_features} features")
    X, y = generate_synthetic_data(n_samples, n_features, seed)
    
    # Normalize features for better convergence
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)
    
    print(f"\nTraining with Adam (lr={adam_lr}, beta1={adam_beta1}, beta2={adam_beta2})...")
    adam_opt = AdamOptimizer(n_features, alpha=adam_lr, beta1=adam_beta1, beta2=adam_beta2)
    adam_weights, adam_losses = train_logistic_regression(X, y, adam_opt, n_epochs)
    
    print(f"\nTraining with SGD (lr={sgd_lr})...")
    sgd_opt = SGDOptimizer(n_features, lr=sgd_lr)
    sgd_weights, sgd_losses = train_logistic_regression(X, y, sgd_opt, n_epochs)
    
    print(f"\nTesting diagonal rescaling invariance...")
    loss_original, loss_rescaled = test_diagonal_rescaling_invariance(X, y, n_epochs=50)
    print(f"  Original gradients final loss: {loss_original:.6f}")
    print(f"  Rescaled gradients final loss: {loss_rescaled:.6f}")
    print(f"  Relative difference: {abs(loss_original - loss_rescaled) / loss_original:.4f}")
    
    # Compute metrics
    adam_final_loss = adam_losses[-1]
    sgd_final_loss = sgd_losses[-1]
    adam_convergence_speed = np.argmax(np.array(adam_losses) < 0.5) if any(np.array(adam_losses) < 0.5) else n_epochs
    sgd_convergence_speed = np.argmax(np.array(sgd_losses) < 0.5) if any(np.array(sgd_losses) < 0.5) else n_epochs
    rescaling_invariance_ratio = abs(loss_original - loss_rescaled) / loss_original
    
    print(f"\n=== Results ===")
    print(f"Adam final loss: {adam_final_loss:.6f}")
    print(f"SGD final loss: {sgd_final_loss:.6f}")
    print(f"Adam epochs to loss<0.5: {adam_convergence_speed}")
    print(f"SGD epochs to loss<0.5: {sgd_convergence_speed}")
    
    # Claim checks
    claim_checks = []
    
    # Claim adam2014-c1: Adam converges faster than SGD
    converges_faster = adam_final_loss < sgd_final_loss and adam_convergence_speed < sgd_convergence_speed
    claim_checks.append({
        "claim_id": "adam2014-c1",
        "verdict": "VALIDATES" if converges_faster else "REFUTES",
        "detail": f"Adam final loss {adam_final_loss:.6f} vs SGD {sgd_final_loss:.6f}; Adam converged in {adam_convergence_speed} epochs vs SGD {sgd_convergence_speed} epochs"
    })
    
    # Claim adam2014-c2: Default settings work well
    # Check if Adam with defaults achieves reasonable loss (< 0.5 is good for binary classification)
    works_well = adam_final_loss < 0.5
    claim_checks.append({
        "claim_id": "adam2014-c2",
        "verdict": "VALIDATES" if works_well else "REFUTES",
        "detail": f"Adam with default hyperparameters achieved final loss {adam_final_loss:.6f} (threshold: 0.5)"
    })
    
    # Claim adam2014-c3: Invariant to diagonal rescaling
    # If relative difference is small (< 10%), updates are approximately invariant
    is_invariant = rescaling_invariance_ratio < 0.1
    claim_checks.append({
        "claim_id": "adam2014-c3",
        "verdict": "VALIDATES" if is_invariant else "REFUTES",
        "detail": f"Relative difference between original and rescaled gradients: {rescaling_invariance_ratio:.4f} (threshold: 0.1)"
    })
    
    # Final JSON output
    result = {
        "method_id": "adam2014-m1",
        "params": {
            "n_samples": n_samples,
            "n_features": n_features,
            "n_epochs": n_epochs,
            "adam_lr": adam_lr,
            "adam_beta1": adam_beta1,
            "adam_beta2": adam_beta2,
            "sgd_lr": sgd_lr,
            "seed": seed
        },
        "metrics": {
            "adam_final_loss": float(adam_final_loss),
            "sgd_final_loss": float(sgd_final_loss),
            "adam_convergence_epochs": int(adam_convergence_speed),
            "sgd_convergence_epochs": int(sgd_convergence_speed),
            "rescaling_invariance_ratio": float(rescaling_invariance_ratio)
        },
        "claim_checks": claim_checks
    }
    
    print(json.dumps(result))

if __name__ == "__main__":
    main()
