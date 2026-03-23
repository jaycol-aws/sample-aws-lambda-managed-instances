# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AWS LMI Scheduler — manages Lambda Managed Instance capacity on schedules.

Subpackages:
    strategies: Scaling strategy implementations (Shape, Pause).
    state: DynamoDB persistence for registrations, snapshots, and cost events.
    reporting: CloudWatch-based cost savings capture and calculation.

Top-level modules:
    management: API Gateway Lambda handler for CRUD operations.
    executor: EventBridge Scheduler Lambda handler for scale-down/scale-up.
"""
