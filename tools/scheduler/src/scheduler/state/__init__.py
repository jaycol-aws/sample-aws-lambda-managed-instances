# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""State persistence for the LMI scheduler.

Uses a DynamoDB single-table design to store:
    - Registration records (CP name, strategy, schedule ARNs, state)
    - Pre-scale-down snapshots (original scaling values for restoration)
    - Cost events (per-cycle savings records for reporting)
"""
