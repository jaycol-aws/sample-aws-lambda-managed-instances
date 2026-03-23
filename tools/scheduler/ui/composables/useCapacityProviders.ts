// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { CapacityProviderListSchema } from '~/schemas/capacityProvider'
import { RegistrationListSchema } from '~/schemas/registration'
import type { Registration } from '~/types'

export interface CapacityProviderWithSchedule {
  name: string
  arn: string
  schedule: Registration | null
}

export function useCapacityProviders() {
  const api = useApi()

  const providers = ref<CapacityProviderWithSchedule[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function refresh() {
    loading.value = true
    error.value = null
    try {
      const [cpData, regData] = await Promise.all([
        api.get('/capacity-providers', CapacityProviderListSchema),
        api.get('/registrations', RegistrationListSchema),
      ])

      const regMap = new Map(
        regData.registrations.map(r => [r.capacity_provider_name, r]),
      )

      providers.value = cpData.capacityProviders.map(cp => {
        const shortName = cp.arn.split(':').pop() || cp.name
        return {
          name: shortName,
          arn: cp.arn,
          schedule: regMap.get(shortName) ?? null,
        }
      })
    }
    catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
    finally {
      loading.value = false
    }
  }

  return { providers, loading, error, refresh }
}
