// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div class="max-w-lg mx-auto">
    <div class="mb-6">
      <NuxtLink to="/" class="text-sm text-blue-400 hover:text-blue-300">&larr; Back to dashboard</NuxtLink>
      <h1 class="text-xl font-semibold text-slate-100 mt-2">Add Schedule</h1>
    </div>

    <div v-if="cpState.loading.value" class="h-32 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />

    <div v-else class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-6 space-y-5">
      <!-- CP selector -->
      <div>
        <label for="cpSelect" class="block text-sm font-medium text-slate-300 mb-1">Capacity Provider</label>
        <select
          id="cpSelect"
          v-model="selectedCp"
          class="w-full bg-slate-700 border border-slate-600 text-slate-100 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
        >
          <option value="" disabled>Select a capacity provider</option>
          <option v-for="cp in availableCps" :key="cp.name" :value="cp.name">
            {{ cp.arn.split(':').pop() }} ({{ cp.arn.split(':')[3] }})
          </option>
        </select>
      </div>

      <ScheduleForm
        :cp-name="selectedCp"
        :api-error="apiError"
        @submit="onSubmit"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
const route = useRoute()
const cpState = useCapacityProviders()
const api = useApi()

const selectedCp = ref('')
const apiError = ref<string | null>(null)

const availableCps = computed(() =>
  cpState.providers.value.filter(cp => cp.schedule === null)
)

onMounted(async () => {
  await cpState.refresh()
  // Pre-select from query param
  const qp = route.query.cp
  if (typeof qp === 'string' && qp) {
    selectedCp.value = qp
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
    await api.post('/registrations', {
      capacityProviderName: selectedCp.value,
      strategy: data.strategy,
      scaleDownSchedule: data.scaleDownSchedule,
      scaleUpSchedule: data.scaleUpSchedule,
      ...(data.minExecutionEnvironments != null ? { minExecutionEnvironments: data.minExecutionEnvironments } : {}),
      ...(data.maxExecutionEnvironments != null ? { maxExecutionEnvironments: data.maxExecutionEnvironments } : {}),
    })
    await navigateTo('/')
  }
  catch (e: any) {
    apiError.value = e?.data?.message || e?.message || 'Failed to create schedule'
  }
}
</script>
