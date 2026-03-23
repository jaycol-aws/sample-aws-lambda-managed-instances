// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { z } from 'zod'
import type { CapacityProviderSchema, CapacityProviderListSchema } from '~/schemas/capacityProvider'
import type { RegistrationSchema, RegistrationListSchema } from '~/schemas/registration'
import type { CostEventSchema, CpSummarySchema, CpReportSchema, AggregateReportSchema } from '~/schemas/report'
import type { ScheduleFormSchema } from '~/schemas/scheduleForm'

export type CapacityProvider = z.infer<typeof CapacityProviderSchema>
export type CapacityProviderList = z.infer<typeof CapacityProviderListSchema>

export type Registration = z.infer<typeof RegistrationSchema>
export type RegistrationList = z.infer<typeof RegistrationListSchema>

export type CostEvent = z.infer<typeof CostEventSchema>
export type CpSummary = z.infer<typeof CpSummarySchema>
export type CpReport = z.infer<typeof CpReportSchema>
export type AggregateReport = z.infer<typeof AggregateReportSchema>

export type ScheduleFormData = z.infer<typeof ScheduleFormSchema>
