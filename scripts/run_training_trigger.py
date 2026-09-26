#!/usr/bin/env python3
"""Cron-friendly entrypoint for TrustForge scheduled training triggers.

Composition root: wires the web-owned training submitter implementations
into the agent-layer trigger. Keeping these imports here (outside src/)
preserves the package dependency DAG (#1468).
"""

from trustforge.training_trigger import main


if __name__ == "__main__":
    from trustforge.modelhub_submit import submit_calibrator_training
    from trustforge.sagemaker_submit import submit_sagemaker_training

    raise SystemExit(
        main(
            modelhub_submitter=submit_calibrator_training,
            sagemaker_submitter=submit_sagemaker_training,
        )
    )
