#!/usr/bin/env python3
"""
Seed DynamoDB tables with realistic test data for all 3 use cases.

Usage:
  python3 seed-data.py --use-case 1 --stack-name lmi-blog-pdf-standard
  python3 seed-data.py --use-case 2 --stack-name lmi-blog-etl-standard
  python3 seed-data.py --use-case 3 --stack-name lmi-blog-api-standard
  python3 seed-data.py --all  # seeds all tables from all stacks

Requires: boto3, aws credentials configured
"""
import argparse
import boto3
import random
import string
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")
cfn = boto3.client("cloudformation")


def get_table_name(stack_name, output_key):
    resp = cfn.describe_stacks(StackName=stack_name)
    for o in resp["Stacks"][0]["Outputs"]:
        if o["OutputKey"] == output_key:
            return o["OutputValue"]
    raise ValueError(f"Output {output_key} not found in stack {stack_name}")


# ── Use Case 1: Financial Transactions ──────────────────────────────────────
def seed_transactions(table_name, num_accounts=50, txns_per_account=200):
    """Generate realistic financial transaction records.
    50 accounts × 200 transactions = 10,000 records.
    Each PDF generation request queries one account's transactions."""
    table = dynamodb.Table(table_name)
    categories = ["Payroll", "Utilities", "Software", "Travel", "Office Supplies",
                   "Marketing", "Insurance", "Consulting", "Equipment", "Maintenance"]
    statuses = ["COMPLETED", "COMPLETED", "COMPLETED", "PENDING", "REVERSED"]
    descriptions = [
        "Monthly payroll processing", "AWS cloud services", "Office lease payment",
        "Employee travel reimbursement", "Software license renewal",
        "Marketing campaign spend", "Insurance premium Q1", "Consulting engagement",
        "Server hardware purchase", "Building maintenance fee",
        "Client dinner expense", "Conference registration", "Training materials",
        "Shipping and logistics", "Legal services retainer",
    ]

    print(f"Seeding {num_accounts * txns_per_account} transactions into {table_name}...")
    with table.batch_writer() as batch:
        for acct in range(1, num_accounts + 1):
            account_id = f"ACCT-{acct}"
            base_date = datetime(2026, 1, 1)
            for t in range(txns_per_account):
                txn_date = base_date + timedelta(days=random.randint(0, 30),
                                                  hours=random.randint(0, 23),
                                                  minutes=random.randint(0, 59))
                date_str = txn_date.strftime("%Y-%m-%d")
                # Sort key prefixed with date for range queries
                txn_id = f"{date_str}#{uuid.uuid4().hex[:8]}"
                amount = round(random.uniform(-5000, 15000), 2)
                batch.put_item(Item={
                    "accountId": account_id,
                    "transactionId": txn_id,
                    "date": date_str,
                    "description": random.choice(descriptions),
                    "category": random.choice(categories),
                    "amount": Decimal(str(amount)),
                    "currency": "USD",
                    "status": random.choice(statuses),
                })
    print(f"  ✓ {num_accounts * txns_per_account} transactions seeded")


# ── Use Case 2: IoT Sensor Readings ────────────────────────────────────────
def seed_sensor_readings(table_name, num_devices=20, readings_per_device=500):
    """Generate realistic IoT sensor data.
    20 devices × 500 readings = 10,000 records.
    Each aggregation request processes one device's readings."""
    table = dynamodb.Table(table_name)
    locations = ["Factory-Floor-A", "Factory-Floor-B", "Warehouse-1",
                 "Warehouse-2", "Server-Room", "Loading-Dock", "Clean-Room",
                 "Assembly-Line-1", "Assembly-Line-2", "QA-Lab"]
    statuses = ["NORMAL", "NORMAL", "NORMAL", "NORMAL", "WARNING", "CRITICAL"]

    print(f"Seeding {num_devices * readings_per_device} sensor readings into {table_name}...")
    with table.batch_writer() as batch:
        for d in range(1, num_devices + 1):
            device_id = f"DEVICE-{d}"
            base_temp = random.uniform(18, 35)
            base_humidity = random.uniform(30, 70)
            base_pressure = random.uniform(1010, 1025)
            base_voltage = random.uniform(3.2, 3.7)
            base_time = datetime(2026, 1, 1)

            for r in range(readings_per_device):
                ts = base_time + timedelta(minutes=r * 3)  # reading every 3 min
                batch.put_item(Item={
                    "deviceId": device_id,
                    "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "temperature": Decimal(str(round(base_temp + random.gauss(0, 2), 2))),
                    "humidity": Decimal(str(round(base_humidity + random.gauss(0, 5), 2))),
                    "pressure": Decimal(str(round(base_pressure + random.gauss(0, 1.5), 2))),
                    "voltage": Decimal(str(round(base_voltage + random.gauss(0, 0.1), 3))),
                    "location": random.choice(locations),
                    "status": random.choice(statuses),
                })
    print(f"  ✓ {num_devices * readings_per_device} sensor readings seeded")


# ── Use Case 3: Products (orders are created by the function itself) ────────
def seed_products(table_name, num_products=100):
    """Generate product catalog. Orders are created by the Lambda function
    during load testing — we only need to seed the product inventory."""
    table = dynamodb.Table(table_name)
    product_names = [
        "Widget Pro", "Gadget Max", "Sensor Kit", "Cable Bundle", "Power Supply",
        "Display Module", "Memory Card", "Processor Unit", "Battery Pack", "Adapter",
        "Router", "Switch", "Antenna", "Enclosure", "Fan Module",
        "LED Panel", "Relay Board", "Transformer", "Capacitor Set", "Resistor Pack",
    ]

    print(f"Seeding {num_products} products into {table_name}...")
    with table.batch_writer() as batch:
        for p in range(1, num_products + 1):
            batch.put_item(Item={
                "productId": f"PROD-{p}",
                "name": f"{random.choice(product_names)} v{random.randint(1,5)}",
                "price": Decimal(str(round(random.uniform(9.99, 499.99), 2))),
                "stockQuantity": random.randint(100, 10000),
            })
    print(f"  ✓ {num_products} products seeded")


def main():
    parser = argparse.ArgumentParser(description="Seed test data for LMI blog use cases")
    parser.add_argument("--use-case", type=int, choices=[1, 2, 3])
    parser.add_argument("--stack-name", type=str, help="CloudFormation stack name")
    parser.add_argument("--all", action="store_true", help="Seed all use cases (uses default stack names)")
    args = parser.parse_args()

    if args.all:
        for uc, stack, output_key, fn in [
            (1, "lmi-blog-pdf-standard", "TransactionsTableName", seed_transactions),
            (2, "lmi-blog-etl-standard", "SensorTableName", seed_sensor_readings),
            (3, "lmi-blog-api-standard", "ProductTableName", seed_products),
        ]:
            try:
                table_name = get_table_name(stack, output_key)
                fn(table_name)
            except Exception as e:
                print(f"  ⚠ UC{uc} skipped: {e}")
    elif args.use_case and args.stack_name:
        if args.use_case == 1:
            seed_transactions(get_table_name(args.stack_name, "TransactionsTableName"))
        elif args.use_case == 2:
            seed_sensor_readings(get_table_name(args.stack_name, "SensorTableName"))
        elif args.use_case == 3:
            seed_products(get_table_name(args.stack_name, "ProductTableName"))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
