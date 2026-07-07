"""LLM-generated implementation for adamw2017-m1 (Butterbase gateway). Review before trusting."""
#!/usr/bin/env python3
import numpy as np
import json
import os

def generate_classification_data(n_samples, n_features, n_classes, rng):
    """Generate synthetic classification data."""
    X = rng.standard_normal((n_samples, n_features))
    # Normalize features
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8)
    
    # Generate true weights
    true_w = rng.standard_normal((n_features, n_classes))
    logits = X @ true_w
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs = probs / probs.sum(axis=1, keepdims=True)
    
    # Sample labels
    y = np.array([rng.choice(n_classes, p=probs[i]) for i in range(n_samples)])
    return X, y

def softmax(logits):
    """Compute softmax probabilities."""
    exp_logits = np.exp(logits - logits.max(axis=1, keepdims=True))
    return exp_logits / exp_logits.sum(axis=1, keepdims=True)

def cross_entropy_loss(X, y, w, lambda_reg=0.0):
    """Compute cross-entropy loss with optional L2 regularization."""
    logits = X @ w
    probs = softmax(logits)
    n = X.shape[0]
    
    # Cross-entropy
    log_probs = np.log(probs[np.arange(n), y] + 1e-10)
    ce_loss = -log_probs.mean()
    
    # L2 regularization
    reg_loss = 0.5 * lambda_reg * np.sum(w ** 2)
    
    return ce_loss + reg_loss

def compute_gradient(X, y, w, lambda_reg=0.0):
    """Compute gradient of cross-entropy loss."""
    n, d = X.shape
    n_classes = w.shape[1]
    
    logits = X @ w
    probs = softmax(logits)
    
    # One-hot encode y
    y_onehot = np.zeros((n, n_classes))
    y_onehot[np.arange(n), y] = 1
    
    # Gradient of cross-entropy
    grad = X.T @ (probs - y_onehot) / n
    
    # Add L2 regularization gradient
    grad += lambda_reg * w
    
    return grad

def adam_with_l2(X_train, y_train, X_test, y_test, lr=0.001, lambda_reg=0.01,
                 beta1=0.9, beta2=0.999, eps=1e-8, n_epochs=100):
    """Adam optimizer with L2 regularization in the gradient."""
    n, d = X_train.shape
    n_classes = len(np.unique(y_train))
    
    rng = np.random.default_rng(42)
    w = rng.standard_normal((d, n_classes)) * 0.01
    
    m = np.zeros_like(w)
    v = np.zeros_like(w)
    
    for epoch in range(n_epochs):
        # Compute gradient with L2 regularization
        grad = compute_gradient(X_train, y_train, w, lambda_reg=lambda_reg)
        
        # Update biased first moment estimate
        m = beta1 * m + (1 - beta1) * grad
        
        # Update biased second raw moment estimate
        v = beta2 * v + (1 - beta2) * (grad ** 2)
        
        # Compute bias-corrected first moment estimate
        m_hat = m / (1 - beta1 ** (epoch + 1))
        
        # Compute bias-corrected second raw moment estimate
        v_hat = v / (1 - beta2 ** (epoch + 1))
        
        # Update parameters
        w = w - lr * m_hat / (np.sqrt(v_hat) + eps)
    
    # Compute test accuracy
    logits_test = X_test @ w
    y_pred = np.argmax(logits_test, axis=1)
    test_error = 1.0 - np.mean(y_pred == y_test)
    
    return test_error, w

def adamw(X_train, y_train, X_test, y_test, lr=0.001, lambda_wd=0.01,
          beta1=0.9, beta2=0.999, eps=1e-8, n_epochs=100):
    """AdamW optimizer with decoupled weight decay."""
    n, d = X_train.shape
    n_classes = len(np.unique(y_train))
    
    rng = np.random.default_rng(42)
    w = rng.standard_normal((d, n_classes)) * 0.01
    
    m = np.zeros_like(w)
    v = np.zeros_like(w)
    
    for epoch in range(n_epochs):
        # Compute gradient WITHOUT L2 regularization
        grad = compute_gradient(X_train, y_train, w, lambda_reg=0.0)
        
        # Update biased first moment estimate
        m = beta1 * m + (1 - beta1) * grad
        
        # Update biased second raw moment estimate
        v = beta2 * v + (1 - beta2) * (grad ** 2)
        
        # Compute bias-corrected first moment estimate
        m_hat = m / (1 - beta1 ** (epoch + 1))
        
        # Compute bias-corrected second raw moment estimate
        v_hat = v / (1 - beta2 ** (epoch + 1))
        
        # Update parameters with decoupled weight decay
        w = w - lr * (m_hat / (np.sqrt(v_hat) + eps) + lambda_wd * w)
    
    # Compute test accuracy
    logits_test = X_test @ w
    y_pred = np.argmax(logits_test, axis=1)
    test_error = 1.0 - np.mean(y_pred == y_test)
    
    return test_error, w

def main():
    # Read parameters from environment
    n_train = int(os.environ.get("P2R_N_TRAIN", "1000"))
    n_test = int(os.environ.get("P2R_N_TEST", "500"))
    n_features = int(os.environ.get("P2R_N_FEATURES", "50"))
    n_classes = int(os.environ.get("P2R_N_CLASSES", "5"))
    lr = float(os.environ.get("P2R_LR", "0.01"))
    lambda_reg = float(os.environ.get("P2R_LAMBDA", "0.01"))
    n_epochs = int(os.environ.get("P2R_N_EPOCHS", "200"))
    
    print(f"Generating synthetic classification data...")
    print(f"  n_train={n_train}, n_test={n_test}, n_features={n_features}, n_classes={n_classes}")
    
    rng = np.random.default_rng(0)
    X_train, y_train = generate_classification_data(n_train, n_features, n_classes, rng)
    X_test, y_test = generate_classification_data(n_test, n_features, n_classes, rng)
    
    print(f"\nTraining Adam with L2 regularization...")
    test_error_adam_l2, w_adam_l2 = adam_with_l2(
        X_train, y_train, X_test, y_test,
        lr=lr, lambda_reg=lambda_reg, n_epochs=n_epochs
    )
    print(f"  Test error: {test_error_adam_l2:.4f}")
    
    print(f"\nTraining AdamW with decoupled weight decay...")
    test_error_adamw, w_adamw = adamw(
        X_train, y_train, X_test, y_test,
        lr=lr, lambda_wd=lambda_reg, n_epochs=n_epochs
    )
    print(f"  Test error: {test_error_adamw:.4f}")
    
    # Test with different learning rates to check decoupling
    print(f"\nTesting learning rate independence...")
    lr_high = lr * 5.0
    test_error_adam_l2_high, _ = adam_with_l2(
        X_train, y_train, X_test, y_test,
        lr=lr_high, lambda_reg=lambda_reg, n_epochs=n_epochs
    )
    test_error_adamw_high, _ = adamw(
        X_train, y_train, X_test, y_test,
        lr=lr_high, lambda_wd=lambda_reg, n_epochs=n_epochs
    )
    print(f"  Adam-L2 (lr={lr_high:.4f}): {test_error_adam_l2_high:.4f}")
    print(f"  AdamW (lr={lr_high:.4f}): {test_error_adamw_high:.4f}")
    
    # Compute metrics
    improvement = test_error_adam_l2 - test_error_adamw
    relative_improvement = improvement / test_error_adam_l2 if test_error_adam_l2 > 0 else 0
    
    # Check claim 1: Adam-L2 under-regularizes
    # AdamW should perform better (lower test error)
    claim1_validates = test_error_adamw < test_error_adam_l2 and improvement > 0.01
    
    # Check claim 2: AdamW substantially improves generalization
    # Require at least 5% relative improvement
    claim2_validates = relative_improvement > 0.05
    
    # Check claim 3: Decoupling makes optimal weight decay independent of learning rate
    # AdamW should be more stable across learning rates than Adam-L2
    adam_l2_lr_sensitivity = abs(test_error_adam_l2_high - test_error_adam_l2)
    adamw_lr_sensitivity = abs(test_error_adamw_high - test_error_adamw)
    claim3_validates = adamw_lr_sensitivity < adam_l2_lr_sensitivity
    
    claim_checks = [
        {
            "claim_id": "adamw2017-c1",
            "verdict": "VALIDATES" if claim1_validates else "REFUTES",
            "detail": f"AdamW test error ({test_error_adamw:.4f}) vs Adam-L2 ({test_error_adam_l2:.4f}), improvement: {improvement:.4f}"
        },
        {
            "claim_id": "adamw2017-c2",
            "verdict": "VALIDATES" if claim2_validates else "REFUTES",
            "detail": f"Relative improvement: {relative_improvement:.2%} (threshold: 5%)"
        },
        {
            "claim_id": "adamw2017-c3",
            "verdict": "VALIDATES" if claim3_validates else "REFUTES",
            "detail": f"LR sensitivity - Adam-L2: {adam_l2_lr_sensitivity:.4f}, AdamW: {adamw_lr_sensitivity:.4f}"
        }
    ]
    
    result = {
        "method_id": "adamw2017-m1",
        "params": {
            "n_train": n_train,
            "n_test": n_test,
            "n_features": n_features,
            "n_classes": n_classes,
            "lr": lr,
            "lambda_reg": lambda_reg,
            "n_epochs": n_epochs
        },
        "metrics": {
            "test_error_adam_l2": float(test_error_adam_l2),
            "test_error_adamw": float(test_error_adamw),
            "improvement": float(improvement),
            "relative_improvement": float(relative_improvement),
            "test_error_adam_l2_high_lr": float(test_error_adam_l2_high),
            "test_error_adamw_high_lr": float(test_error_adamw_high),
            "adam_l2_lr_sensitivity": float(adam_l2_lr_sensitivity),
            "adamw_lr_sensitivity": float(adamw_lr_sensitivity)
        },
        "claim_checks": claim_checks
    }
    
    print(json.dumps(result))

if __name__ == "__main__":
    main()
