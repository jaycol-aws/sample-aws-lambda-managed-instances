# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Scaling strategies for LMI capacity providers.

Strategies define how to reduce and restore capacity on a capacity provider.
Each strategy captures original values before modification so they can be
reliably restored on scale-up.

Available strategies:
    ShapeStrategy: Adjusts per-function execution environment bounds to targets.
    PauseStrategy: Zeros out per-function execution environments.
"""
