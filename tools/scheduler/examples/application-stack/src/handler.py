# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Sample Lambda function for the LMI example application stack.

A minimal handler that confirms the function is running on Lambda Managed
Instances. Used as the test target for validating scheduler strategies.
"""

import os
import platform


def handler(event, context):
    """Return basic runtime info to confirm the function is running on LMI.

    Args:
        event: Lambda invocation event (not used).
        context: Lambda context object (not used).

    Returns:
        dict: Response with status code and runtime metadata.
    """
    return {
        "statusCode": 200,
        "body": {
            "message": "Running on Lambda Managed Instances",
            "python_version": platform.python_version(),
            "function_name": os.environ.get("AWS_LAMBDA_FUNCTION_NAME"),
            "function_version": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION"),
        },
    }
