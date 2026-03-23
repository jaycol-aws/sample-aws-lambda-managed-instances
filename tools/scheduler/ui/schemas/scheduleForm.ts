// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { z } from 'zod'

export const ScheduleFormSchema = z.object({
  capacityProviderName: z.string().min(1),
  strategy: z.enum(['shape', 'pause']),
  scaleDownSchedule: z.string().min(1),
  scaleUpSchedule: z.string().min(1),
  minExecutionEnvironments: z.number().int().min(0).optional(),
  maxExecutionEnvironments: z.number().int().min(1).optional(),
}).refine(
  (data) => data.strategy !== 'shape' || (data.minExecutionEnvironments !== undefined && data.maxExecutionEnvironments !== undefined),
  { message: 'Shape strategy requires min and max execution environments' }
)
