// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { z } from 'zod'

export const CapacityProviderSchema = z.object({
  name: z.string(),
  arn: z.string(),
})

export const CapacityProviderListSchema = z.object({
  capacityProviders: z.array(CapacityProviderSchema),
})
