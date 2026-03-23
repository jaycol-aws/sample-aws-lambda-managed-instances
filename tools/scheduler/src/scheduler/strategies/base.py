# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Abstract base class for LMI scaling strategies.

Each strategy defines how to scale down a capacity provider (to reduce costs)
and how to scale up (to restore the original configuration). Strategies operate
on runtime state via AWS APIs — they do not modify IaC definitions.

Two concrete implementations exist:
    - ShapeStrategy: Adjusts per-function execution environment bounds to targets.
    - PauseStrategy: Sets per-function execution environments to 0.
"""

from abc import ABC, abstractmethod
from typing import Any


class ScalingStrategy(ABC):
    """Interface that all scaling strategies must implement."""

    @abstractmethod
    def scale_down(self, cp_name: str) -> dict[str, Any]:
        """Scale down a capacity provider to minimise EC2 instance costs.

        Captures the current scaling configuration as a snapshot before
        making changes so it can be restored on scale-up.

        Args:
            cp_name: Name of the capacity provider to scale down.

        Returns:
            dict: Snapshot of the original scaling configuration. Both
                Shape and Pause return per-function execution environment
                counts in {"functions": {<arn>: {"min": N, "max": N}}}.
        """
        pass

    @abstractmethod
    def scale_up(self, cp_name: str, snapshot: dict[str, Any]) -> None:
        """Restore a capacity provider to its pre-scale-down configuration.

        Args:
            cp_name: Name of the capacity provider to scale up.
            snapshot: The snapshot dict returned by scale_down(), containing
                the original values to restore.
        """
        pass
