# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Cost reporting for the LMI scheduler.

Captures CloudWatch capacity provider metrics at scale-down time and
calculates estimated dollar savings using EC2 on-demand pricing plus
the 15% LMI management fee.
"""
