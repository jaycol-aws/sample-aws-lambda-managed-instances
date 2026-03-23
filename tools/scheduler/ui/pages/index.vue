// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div>
    <!-- Loading state -->
    <div v-if="isLoading" class="space-y-4">
      <div class="h-24 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div v-for="i in 3" :key="i" class="h-48 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
      </div>
    </div>

    <!-- Error state -->
    <div v-else-if="errorMessage" class="bg-red-50 border border-red-200 rounded-lg p-8 text-center">
      <svg class="mx-auto mb-3 h-8 w-8 text-red-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
      </svg>
      <p class="text-sm font-semibold text-red-800 mb-1">Failed to load dashboard</p>
      <p class="text-xs text-red-600 mb-4">{{ errorMessage }}</p>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-red-700 bg-white border border-red-300 rounded-md shadow-sm hover:bg-red-50 transition-colors duration-150 cursor-pointer"
        @click="fetchAll"
      >
        <svg class="h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
        </svg>
        Retry
      </button>
    </div>

    <!-- Empty state -->
    <div v-else-if="cpState.providers.value.length === 0" class="bg-slate-800 border border-slate-700 rounded-lg p-10 text-center">
      <p class="text-slate-400">No capacity providers found.</p>
    </div>

    <!-- Dashboard content -->
    <div v-else>
      <!-- Remove error -->
      <div
        v-if="removeError"
        class="bg-red-900/20 border border-red-700/50 rounded-lg p-4 text-sm text-red-300 mb-4"
      >
        {{ removeError }}
      </div>

      <!-- Project description -->
      <div class="mb-6">
        <h1 class="text-xl font-semibold text-slate-100 mb-1">Capacity Provider Scheduler</h1>
        <p class="text-sm text-slate-400 max-w-3xl">
          Automatically scales Lambda Managed Instance capacity providers on a defined schedule to eliminate idle EC2 costs during low-traffic periods.
          Register a capacity provider, choose a strategy, and set scale-down and scale-up cron expressions —
          the scheduler handles the rest via EventBridge Scheduler.
        </p>
      </div>

      <!-- Aggregate savings summary -->
      <div class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-5 mb-6">
        <div class="flex flex-wrap gap-8">
          <div>
            <p class="text-sm text-slate-400">Total Estimated Savings</p>
            <p class="text-2xl font-bold text-slate-100">
              ${{ reportState.aggregateReport.value?.total_estimated_savings_usd?.toFixed(2) ?? '0.00' }}
            </p>
          </div>
          <div>
            <p class="text-sm text-slate-400">CPs with Schedules</p>
            <p class="text-2xl font-bold text-slate-100">{{ scheduledCount }}</p>
          </div>
          <div>
            <p class="text-sm text-slate-400">Total Capacity Providers</p>
            <p class="text-2xl font-bold text-slate-100">{{ cpState.providers.value.length }}</p>
          </div>
        </div>
        <p
          v-if="reportState.aggregateReport.value?.disclaimer"
          class="text-xs text-slate-500 mt-3"
        >
          {{ reportState.aggregateReport.value.disclaimer }}
        </p>
      </div>

      <!-- No schedules yet callout -->
      <div
        v-if="scheduledCount === 0"
        class="bg-blue-900/20 border border-blue-700/50 rounded-lg p-5 mb-6 flex items-start gap-4"
      >
        <svg class="mt-0.5 h-5 w-5 shrink-0 text-blue-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
        </svg>
        <div>
          <p class="text-sm font-semibold text-blue-200">No schedules configured</p>
          <p class="text-sm text-blue-300/80 mt-0.5">
            Add a schedule to a capacity provider to start managing idle costs automatically.
            Select a capacity provider below and click <strong>Add Schedule</strong> to get started.
          </p>
        </div>
      </div>

      <!-- CP cards grid -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <CpCard
          v-for="cp in cpState.providers.value"
          :key="cp.name"
          :provider="cp"
          :savings="getSavings(cp.name)"
          @remove="onRemove"
          @scale-down="onScaleDown"
          @scale-up="onScaleUp"
        />
      </div>

      <!-- Confirm remove dialog -->
      <ConfirmDialog
        :open="confirmOpen"
        title="Remove Schedule"
        :message="`Remove the schedule for ${removingCp}? This cannot be undone.`"
        confirm-label="Remove"
        cancel-label="Cancel"
        @confirm="onConfirmRemove"
        @cancel="confirmOpen = false"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
const cpState = useCapacityProviders()
const reportState = useReports()
const api = useApi()

const isLoading = computed(() => cpState.loading.value || reportState.loading.value)
const errorMessage = computed(() => cpState.error.value || reportState.error.value)

const scheduledCount = computed(
  () => cpState.providers.value.filter(cp => cp.schedule !== null).length,
)

const savingsMap = computed(() => {
  const map = new Map<string, number>()
  if (reportState.aggregateReport.value) {
    for (const cp of reportState.aggregateReport.value.capacity_providers) {
      map.set(cp.capacity_provider, cp.estimated_savings_usd)
    }
  }
  return map
})

function getSavings(cpName: string): number | null {
  return savingsMap.value.get(cpName) ?? null
}

async function fetchAll() {
  await Promise.all([cpState.refresh(), reportState.fetchAggregate()])
}

// Remove schedule state
const confirmOpen = ref(false)
const removingCp = ref('')
const removeError = ref<string | null>(null)

function onRemove(cpName: string) {
  removingCp.value = cpName
  removeError.value = null
  confirmOpen.value = true
}

async function onConfirmRemove() {
  confirmOpen.value = false
  try {
    await api.del(`/registrations/${encodeURIComponent(removingCp.value)}`)
    await fetchAll()
  }
  catch (e: any) {
    removeError.value = e?.data?.message || e?.message || 'Failed to remove schedule'
  }
}

onMounted(() => {
  fetchAll()
})

function onScaleDown(cpName: string) {
  const cp = cpState.providers.value.find(p => p.name === cpName)
  if (cp?.schedule) {
    cp.schedule = { ...cp.schedule, state: 'scaled-down' }
  }
}

function onScaleUp(cpName: string) {
  const cp = cpState.providers.value.find(p => p.name === cpName)
  if (cp?.schedule) {
    cp.schedule = { ...cp.schedule, state: 'active' }
  }
}
</script>
