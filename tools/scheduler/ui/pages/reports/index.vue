// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div>
    <h1 class="text-xl font-semibold text-slate-100 mb-1">Cost Reports</h1>
    <p class="text-sm text-slate-400 max-w-3xl mb-6">
      Estimated savings are calculated at each scale-down event by snapshotting the capacity provider's allocated vCPU and memory,
      then measuring how long it remained scaled down. Savings reflect EC2 on-demand pricing plus the 15% LMI management fee
      and are an approximation — actual savings will vary if you have Savings Plans or Reserved Instances.
    </p>

    <DateRangePicker
      :start="dateRange.start"
      :end="dateRange.end"
      class="mb-6"
      @update="onDateChange"
    />

    <!-- Loading -->
    <div v-if="loading" class="space-y-4">
      <div class="h-24 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
      <div class="h-64 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
      <div class="h-48 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="bg-red-50 border border-red-200 rounded-lg p-8 text-center">
      <svg class="mx-auto mb-3 h-8 w-8 text-red-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
      </svg>
      <p class="text-sm font-semibold text-red-800 mb-1">Failed to load reports</p>
      <p class="text-xs text-red-600 mb-4">{{ error }}</p>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-red-700 bg-white border border-red-300 rounded-md shadow-sm hover:bg-red-50 transition-colors duration-150 cursor-pointer"
        @click="refresh"
      >
        <svg class="h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
        </svg>
        Retry
      </button>
    </div>

    <!-- Empty state -->
    <div
      v-else-if="aggregateReport && aggregateReport.capacity_providers.length === 0"
      class="bg-slate-800 border border-slate-700 rounded-lg p-10 text-center"
    >
      <p class="text-slate-400">No cost events found for the selected date range.</p>
    </div>

    <!-- Report content -->
    <div v-else-if="aggregateReport" class="space-y-6">
      <!-- Total savings card -->
      <div class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-5">
        <p class="text-sm text-slate-400">Total Estimated Savings</p>
        <p class="text-2xl font-bold text-green-400">
          ${{ aggregateReport.total_estimated_savings_usd.toFixed(2) }}
        </p>
      </div>

      <!-- Aggregate chart -->
      <SavingsChart
        v-if="aggChartLabels.length > 0"
        :labels="aggChartLabels"
        :data="aggChartData"
        title="Savings by Capacity Provider"
        chart-type="bar"
      />

      <!-- Breakdown table -->
      <div class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-5">
        <h2 class="text-base font-semibold text-slate-100 mb-3">Per-CP Breakdown</h2>
        <CpBreakdownTable :providers="aggregateReport.capacity_providers" />
      </div>

      <!-- Disclaimer -->
      <p
        v-if="aggregateReport.disclaimer"
        class="text-xs text-slate-500 mt-4"
      >
        {{ aggregateReport.disclaimer }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
const { aggregateReport, loading, error, fetchAggregate } = useReports()

function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return formatDate(d)
}

const dateRange = reactive({
  start: daysAgo(30),
  end: formatDate(new Date()),
})

function onDateChange(range: { start: string; end: string }) {
  dateRange.start = range.start
  dateRange.end = range.end
  refresh()
}

function refresh() {
  fetchAggregate(dateRange.start, dateRange.end)
}

// Build aggregate chart data from CP summaries (no per-event data in aggregate response)
const aggChartLabels = computed(() => {
  if (!aggregateReport.value) return []
  return aggregateReport.value.capacity_providers
    .sort((a, b) => b.estimated_savings_usd - a.estimated_savings_usd)
    .map(cp => cp.capacity_provider)
})

const aggChartData = computed(() => {
  if (!aggregateReport.value) return []
  return aggregateReport.value.capacity_providers
    .sort((a, b) => b.estimated_savings_usd - a.estimated_savings_usd)
    .map(cp => cp.estimated_savings_usd)
})

onMounted(() => {
  refresh()
})
</script>
