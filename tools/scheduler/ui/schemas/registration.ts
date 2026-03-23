// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { z } from 'zod'

export const RegistrationSchema = z.object({
  capacity_provider_name: z.string(),
  strategy: z.enum(['shape', 'pause']),
  state: z.enum(['active', 'scaled-down']),
  scale_down_cron: z.string(),
  scale_up_cron: z.string(),
  min_execution_environments: z.number().optional(),
  max_execution_environments: z.number().optional(),
})

export const RegistrationListSchema = z.object({
  registrations: z.array(RegistrationSchema),
})
