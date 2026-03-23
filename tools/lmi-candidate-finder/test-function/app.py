import json
import hashlib
import os

def handler(event, context):
    """CPU-bound Lambda that simulates a workload ideal for LMI:
    - Moderate duration (~500ms)
    - Uses allocated memory
    - Deterministic output for verification
    """
    iterations = int(event.get("iterations", 50000))
    # CPU work: hash chaining
    data = b"lmi-candidate-test"
    for _ in range(iterations):
        data = hashlib.sha256(data).digest()

    # Allocate some memory to simulate real workload
    payload = os.urandom(1024 * 64)  # 64 KB allocation

    return {
        "statusCode": 200,
        "body": json.dumps({
            "hash": data.hex()[:16],
            "iterations": iterations,
            "memory_mb": context.memory_limit_in_mb,
            "remaining_ms": context.get_remaining_time_in_millis(),
        }),
    }
