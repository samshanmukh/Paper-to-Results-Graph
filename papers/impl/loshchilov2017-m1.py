#!/usr/bin/env python3
"""Small deterministic SGDR reproduction on an ill-conditioned quadratic."""

import json
import math
import os


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(f"P2R_{name}", default))


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(f"P2R_{name}", default))


def optimize(schedule: str, epochs: int, t_0: int, t_mult: int,
             eta_max: float, eta_min: float, momentum: float) -> dict:
    # f(x, y) = 0.5 x² + 5 y²; condition number 10.
    position = [8.0, -6.0]
    velocity = [0.0, 0.0]
    losses = []
    target_epoch = None
    period = t_0
    period_start = 0

    for epoch in range(epochs):
        if schedule == "sgdr":
            while epoch - period_start >= period:
                period_start += period
                print(f"  restart at epoch {period_start}: loss={losses[-1]:.6f}")
                period *= t_mult
            progress = (epoch - period_start) / period
            learning_rate = eta_min + 0.5 * (eta_max - eta_min) * (
                1.0 + math.cos(math.pi * progress)
            )
        else:
            # A conservative fixed learning rate is the no-restart baseline.
            learning_rate = max(eta_min, eta_max / 10.0)

        gradient = [position[0], 10.0 * position[1]]
        velocity = [
            momentum * velocity[i] + gradient[i]
            for i in range(2)
        ]
        position = [
            position[i] - learning_rate * velocity[i]
            for i in range(2)
        ]
        loss = 0.5 * position[0] ** 2 + 5.0 * position[1] ** 2
        losses.append(loss)
        if target_epoch is None and loss < 0.01:
            target_epoch = epoch + 1

    return {
        "final_loss": losses[-1],
        "epochs_to_target": target_epoch or epochs + 1,
        # Lower is better; log1p prevents early large losses dominating.
        "anytime_score": sum(math.log1p(loss) for loss in losses),
    }


def main() -> None:
    epochs = env_int("EPOCHS", 200)
    t_0 = env_int("T_0", 10)
    t_mult = env_int("T_MULT", 2)
    eta_max = env_float("ETA_MAX", 0.05)
    eta_min = env_float("ETA_MIN", 0.0)
    momentum = env_float("MOMENTUM", 0.9)

    print("SGDR warm-restart experiment on an ill-conditioned quadratic")
    print(
        f"  epochs={epochs}, T_0={t_0}, T_mult={t_mult}, "
        f"eta=[{eta_min}, {eta_max}], momentum={momentum}"
    )
    sgdr = optimize("sgdr", epochs, t_0, t_mult, eta_max, eta_min, momentum)
    fixed = optimize("fixed", epochs, t_0, t_mult, eta_max, eta_min, momentum)
    speedup = fixed["epochs_to_target"] / sgdr["epochs_to_target"]

    faster = speedup >= 2.0
    better_anytime = (
        sgdr["epochs_to_target"] <= fixed["epochs_to_target"]
        and sgdr["anytime_score"] < fixed["anytime_score"]
    )
    claim_checks = [
        {
            "claim_id": "loshchilov2017-c1",
            "verdict": "VALIDATES" if faster else "REFUTES",
            "detail": (
                f"Toy quadratic speedup was {speedup:.2f}x "
                f"({sgdr['epochs_to_target']} vs {fixed['epochs_to_target']} epochs); "
                "the paper claim requires 2-4x."
            ),
        },
        {
            "claim_id": "loshchilov2017-c3",
            "verdict": "VALIDATES" if better_anytime else "REFUTES",
            "detail": (
                f"SGDR anytime score {sgdr['anytime_score']:.3f} vs "
                f"fixed schedule {fixed['anytime_score']:.3f}; lower is better."
            ),
        },
    ]
    print(
        json.dumps(
            {
                "method_id": "loshchilov2017-m1",
                "params": {
                    "epochs": epochs,
                    "T_0": t_0,
                    "T_mult": t_mult,
                    "eta_max": eta_max,
                    "eta_min": eta_min,
                    "momentum": momentum,
                },
                "metrics": {
                    "sgdr_final_loss": sgdr["final_loss"],
                    "fixed_final_loss": fixed["final_loss"],
                    "sgdr_epochs_to_target": sgdr["epochs_to_target"],
                    "fixed_epochs_to_target": fixed["epochs_to_target"],
                    "speedup": speedup,
                    "sgdr_anytime_score": sgdr["anytime_score"],
                    "fixed_anytime_score": fixed["anytime_score"],
                },
                "claim_checks": claim_checks,
            }
        )
    )


if __name__ == "__main__":
    main()
