// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div class="max-w-lg mx-auto">
    <div class="mb-6">
      <NuxtLink to="/" class="text-sm text-blue-400 hover:text-blue-300">&larr; Back to dashboard</NuxtLink>
      <h1 class="text-xl font-semibold text-slate-100 mt-2">Edit Schedule — {{ cpName }}</h1>
    </div>

    <div v-if="loading" class="h-48 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />

    <div v-else-if="loadError" class="bg-red-900/20 border border-red-700/50 rounded-lg p-6 text-center">
      <p class="text-sm text-red-300">{{ loadError }}</p>
    </div>

    <div v-else class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-6">
      <ScheduleForm
        :initial-data="initialData"
        :cp-name="cpName"
        :disabled="isScaledDown"
        :api-error="apiError"
        @submit="onSubmit"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { RegistrationSchema } from '~/schemas/registration'

const route = useRoute()
const api = useApi()

const cpName = route.params.cpName as string

const loading = ref(true)
const loadError = ref<string | null>(null)
const apiError = ref<string | null>(null)
const isScaledDown = ref(false)

const initialData = ref<{
  strategy: 'shape' | 'pause'
  scaleDownSchedule: string
  scaleUpSchedule: string
  minExecutionEnvironments?: number
  maxExecutionEnvironments?: number
}>()

onMounted(async () => {
  try {
    const reg = await api.get(`/registrations/${encodeURIComponent(cpName)}`, RegistrationSchema)
    isScaledDown.value = reg.state === 'scaled-down'
    initialData.value = {
      strategy: reg.strategy,
      scaleDownSchedule: reg.scale_down_cron,
      scaleUpSchedule: reg.scale_up_cron,
      minExecutionEnvironments: reg.min_execution_environments,
      maxExecutionEnvironments: reg.max_execution_environments,
    }
  }
  catch (e: any) {
    loadError.value = e?.data?.message || e?.message || 'Failed to load schedule'
  }
  finally {
    loading.value = false
  }
})

async function onSubmit(data: {
  strategy: 'shape' | 'pause'
  scaleDownSchedule: string
  scaleUpSchedule: string
  minExecutionEnvironments?: number
  maxExecutionEnvironments?: number
}) {
  apiError.value = null
  try {
    await api.put(`/registrations/${encodeURIComponent(cpName)}`, {
      capacityProviderName: cpName,
      strategy: data.strategy,
      scaleDownSchedule: data.scaleDownSchedule,
      scaleUpSchedule: data.scaleUpSchedule,
      ...(data.minExecutionEnvironments != null ? { minExecutionEnvironments: data.minExecutionEnvironments } : {}),
      ...(data.maxExecutionEnvironments != null ? { maxExecutionEnvironments: data.maxExecutionEnvironments } : {}),
    })
    await navigateTo('/')
  }
  catch (e: any) {
    apiError.value = e?.data?.message || e?.message || 'Failed to update schedule'
  }
}
</script>
