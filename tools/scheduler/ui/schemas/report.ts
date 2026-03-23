// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { z } from 'zod'

export const CostEventSchema = z.object({
  estimated_savings_usd: z.coerce.number(),
  duration_hours: z.coerce.number(),
  scale_down_time: z.string(),
  scale_up_time: z.string().optional(),
})

// Per-CP summary within the aggregate report
export const CpSummarySchema = z.object({
  capacity_provider: z.string(),
  estimated_savings_usd: z.number(),
  hours_down: z.number(),
  event_count: z.number(),
})

// Per-CP detail report (GET /reports/{cpName})
export const CpReportSchema = z.object({
  capacity_provider: z.string(),
  total_estimated_savings_usd: z.number(),
  total_hours_down: z.number(),
  event_count: z.number(),
  events: z.array(CostEventSchema),
  disclaimer: z.string().optional(),
})

// Aggregate report (GET /reports)
export const AggregateReportSchema = z.object({
  total_estimated_savings_usd: z.number(),
  total_hours_down: z.number(),
  capacity_providers: z.array(CpSummarySchema),
  disclaimer: z.string().optional(),
})
